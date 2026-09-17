"""这是工具层兼容适配器：自托管路径使用本项目 snake_case HTTP 契约，供应商路径
读取 PatentHub 的旧字段/端点，再统一成 MCP/工具调用方看到的 patents/detail 形状。
"""

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional
from urllib.parse import quote

import httpx

from app.mappings.detail_mapper import map_detail_response
from app.mappings.ipc_mapper import (
    normalize_compat_ipc_records,
    normalized_ipc_list,
    normalized_main_ipc,
    normalized_record_ipc_list,
)
from app.mappings.patent_type_mapper import normalized_patent_type
from app.mappings.text_value import normalized_text


DEFAULT_PATENTHUB_BASE_URL = "https://www.patenthub.cn"
PATENTHUB_API_VERSION = "1"


@dataclass
class PatentHubAdapterConfig:
    """工具兼容适配器的路由、认证与资源上限配置。

    默认优先调用自托管服务，只有显式关闭 ``use_self_hosted`` 才会请求外部
    PatentHub。两个 token 属于完全不同的协议边界，不能互换或透传；超时和页大小
    是工具层的第二道资源约束，后端仍保留最终校验权。
    """

    # use_self_hosted 默认开启；vendor_* 仅在显式关闭时使用，避免本地工具意外
    # 把查询发到外部服务。page_size_limit 和 timeout 是工具层第二道边界。
    self_hosted_base_url: str = "http://127.0.0.1:8000"
    self_hosted_api_token: str = ""
    use_self_hosted: bool = True
    page_size_limit: int = 50
    vendor_base_url: str = DEFAULT_PATENTHUB_BASE_URL
    vendor_api_token: str = ""
    timeout_seconds: int = 245

    @classmethod
    def from_env(cls) -> "PatentHubAdapterConfig":
        """从兼容适配器环境变量构造配置，供独立工具进程或测试宿主复用。"""
        # 适配器可以独立使用（例如 SaaS harness），因此保留自己的环境读取入口。
        return cls(
            self_hosted_base_url=os.getenv("PATENT_SEARCH_BASE_URL", "http://127.0.0.1:8000"),
            self_hosted_api_token=os.getenv("PATENT_SEARCH_API_TOKEN", ""),
            use_self_hosted=_env_bool("PATENT_SEARCH_USE_SELF_HOSTED", True),
            page_size_limit=_env_int("PATENT_SEARCH_PAGE_SIZE_LIMIT", 50),
            vendor_base_url=os.getenv("PATENTHUB_BASE_URL", DEFAULT_PATENTHUB_BASE_URL),
            vendor_api_token=os.getenv("PATENTHUB_API_TOKEN", ""),
            timeout_seconds=_env_int("PATENT_SEARCH_TIMEOUT_SECONDS", 245),
        )


class PatentHubToolAdapter:
    """在自托管 API 与旧 PatentHub API 之间维持统一的工具输出契约。

    自托管路径使用当前 snake_case HTTP API；供应商路径先读取其旧 endpoint/字段，
    再复用本项目 mapper 生成相同的详情和搜索结构。所有公开方法返回 JSON 字符串，
    这是历史工具接口约定；MCP client 会在更外层严格解析为对象。
    """

    def __init__(
        self,
        config: Optional[PatentHubAdapterConfig] = None,
        client: Optional[httpx.Client] = None,
    ):
        """使用显式配置和可替换 HTTP client 创建适配器。

        调用方可注入测试 client 或带额外响应校验的 client；未注入时才创建带配置
        超时的普通 ``httpx.Client``。本构造函数不发起网络访问。
        """
        self.config = config or PatentHubAdapterConfig.from_env()
        self.client = client or httpx.Client(timeout=self.config.timeout_seconds)

    def patent_search(
        self,
        q: str | None = None,
        ds: str = "cn",
        page: int = 1,
        page_size: int = 10,
        sort: str = "relation",
        highlight: bool = False,
        *,
        mode: Literal["boolean", "vector", "hybrid"] = "boolean",
        semantic_text: str | None = None,
        vector_fields: list[str] | None = None,
        top_k: int | None = None,
    ) -> str:
        """检索专利并输出兼容工具层的 JSON 分页结果。

        自托管模式发送当前 HTTP 请求体并将 ``records`` 映射为 ``patents``；供应商
        模式转向旧参数协议。两条路径都在这里裁剪页大小并将失败收缩为有限错误对象，
        不把底层响应全文或 token 暴露给工具调用方。
        """
        # 自托管模式先限制 page_size，再把 highlight bool 转成后端兼容的 0/1；
        # vendor 模式走旧的 /api/s 参数名，最终都映射为 patents。
        if not self.config.use_self_hosted:
            if mode != "boolean":
                raise ValueError("semantic search requires the self-hosted API")
            if q is None:
                raise ValueError("boolean search requires q")
            return self._vendor_search(q, ds, page, page_size, sort, highlight)

        payload = {
            "mode": mode,
            "ds": ds,
            "page": page,
            "page_size": self._limited_page_size(page_size),
            "sort": sort,
            "highlight": 1 if highlight else 0,
        }
        if q is not None:
            payload["q"] = q
        if semantic_text is not None:
            payload["semantic_text"] = semantic_text
        if vector_fields is not None:
            payload["vector_fields"] = vector_fields
        if top_k is not None:
            payload["top_k"] = top_k
        data = self._request_json(
            "POST",
            self._self_hosted_url("/api/patent/search"),
            json=payload,
            headers=self._self_hosted_headers(),
        )
        if self._is_error(data):
            return _format_json(self._tool_error(data))

        records = data.get("records", [])
        result = {
            "total": data.get("total"),
            "page": data.get("page", page),
            "page_size": data.get("page_size", payload["page_size"]),
            "total_pages": data.get("total_pages"),
            "accessible_pages": data.get("accessible_pages"),
            "next_page": data.get("next_page"),
            "took_ms": data.get("took_ms"),
            "patents": [self._map_search_record(record) for record in records],
        }
        if "search_context" in data:
            result["search_context"] = data["search_context"]
        return _format_json(result)

    def patent_get_detail(self, patent_id: str, include_description: bool = False) -> str:
        """按专利 ID 获取详情，并仅在需要时请求说明书大字段。

        自托管路径对 ID 做 URL 编码；供应商路径会拼合其分散的 base、claims 和
        可选 description 响应，再交给同一详情 mapper。两种模式均返回格式化 JSON。
        """
        # 路径参数必须 URL 编码，避免专利 ID 中的斜杠/空格改变路由；说明书只在
        # 显式请求时作为 query 参数发送。
        if not self.config.use_self_hosted:
            return self._vendor_detail(patent_id, include_description)

        path = "/api/patent/detail/{}".format(quote(patent_id, safe=""))
        data = self._request_json(
            "GET",
            self._self_hosted_url(path),
            params={"include_description": "true"} if include_description else None,
            headers=self._self_hosted_headers(),
        )
        if self._is_error(data):
            return _format_json(self._tool_error(data))
        return _format_json(data)

    def patent_get_citations(self, patent_id: str) -> str:
        """按专利 ID 获取引证数据，并统一兼容对象内的 IPC 表示。"""
        # 引证响应额外清洗兼容列表中的 IPC 别名，保证自托管和供应商返回一致。
        if not self.config.use_self_hosted:
            return self._vendor_citations(patent_id)

        path = "/api/patent/citations/{}".format(quote(patent_id, safe=""))
        data = self._request_json(
            "GET",
            self._self_hosted_url(path),
            headers=self._self_hosted_headers(),
        )
        if self._is_error(data):
            return _format_json(self._tool_error(data))
        return _format_json(self._normalize_citations_ipc(data))

    def patent_get_legal_history(self, patent_id: str) -> str:
        """按专利 ID 获取法律历史，不为缺失供应商字段臆造交易信息。"""
        # 法律历史不做字段猜测，保持 HTTP mapper 给出的 transaction contract。
        if not self.config.use_self_hosted:
            return self._vendor_legal_history(patent_id)

        path = "/api/patent/legal-history/{}".format(quote(patent_id, safe=""))
        data = self._request_json(
            "GET",
            self._self_hosted_url(path),
            headers=self._self_hosted_headers(),
        )
        if self._is_error(data):
            return _format_json(self._tool_error(data))
        return _format_json(data)

    def _vendor_search(
        self,
        q: str,
        ds: str,
        page: int,
        page_size: int,
        sort: str,
        highlight: bool,
    ) -> str:
        """调用供应商旧搜索端点，并投影为当前工具分页结构。

        参数名、分页字段与成功标记均属于外部兼容细节；只有明确 ``success`` 的响应
        才会读取 ``patents``。失败时保留有限的错误信息，避免供应商原始信封泄露。
        """
        # 供应商搜索使用旧参数名和分页字段；只在 success=true 时读取 patents，
        # 失败则缩成工具错误对象。
        data = self._vendor_get(
            "/api/s",
            {
                "q": q,
                "ds": ds,
                "p": page,
                "ps": self._limited_page_size(page_size),
                "s": sort,
                "hl": 1 if highlight else 0,
            },
        )
        if not data.get("success"):
            return _format_json({"error": data.get("error", "Request failed"), "code": data.get("code")})

        patents = data.get("patents", [])
        return _format_json(
            {
                "total": data.get("total"),
                "page": page,
                "total_pages": data.get("totalPages"),
                "next_page": data.get("nextPage"),
                "took_ms": data.get("took"),
                "patents": [self._map_vendor_patent(record) for record in patents],
            }
        )

    def _vendor_detail(self, patent_id: str, include_description: bool) -> str:
        """拼合供应商的详情碎片并复用本服务的详情字段规则。

        base 是必需来源；claims 或 description 请求失败时只让相应字段缺失，而不
        丢弃已经获取的基础详情。临时 source 由 ``_vendor_detail_source`` 构建，最终
        映射仍交给 ``map_detail_response``，确保两条后端路径的回退顺序一致。
        """
        # 供应商详情拆成 base/claims/可选 desc 三次读取，再借用本地 detail_mapper
        # 生成相同 snake_case 详情，避免两条路径出现不同字段优先级。
        base_data = self._vendor_get("/api/patent/base", {"id": patent_id})
        if not base_data.get("success"):
            return _format_json({"error": base_data.get("error", "Request failed"), "code": base_data.get("code")})

        claims_data = self._vendor_get("/api/patent/claims", {"id": patent_id})
        patent = base_data.get("patent", {})
        description = None
        if include_description:
            desc_data = self._vendor_get("/api/patent/desc", {"id": patent_id})
            description = desc_data.get("patent", {}).get("description") if desc_data.get("success") else None
        claims = claims_data.get("patent", {}).get("claims") if claims_data.get("success") else None
        return _format_json(
            map_detail_response(
                {"_source": _vendor_detail_source(patent, claims=claims, description=description)},
                include_description=include_description,
            )
        )

    def _vendor_citations(self, patent_id: str) -> str:
        # 供应商引证字段名与自托管不同，这里只做端点/字段转换，不改变摘要 schema。
        data = self._vendor_get("/api/patent/citing", {"id": patent_id})
        if not data.get("success"):
            return _format_json({"error": data.get("error", "Request failed"), "code": data.get("code")})
        return _format_json(
            {
                "patent_id": patent_id,
                "cited_by": [self._map_vendor_patent(record) for record in data.get("citedList", [])],
                "patent_references": [self._map_vendor_patent(record) for record in data.get("patentXref", [])],
                "non_patent_references": data.get("noPatentXref", []),
            }
        )

    def _vendor_legal_history(self, patent_id: str) -> str:
        # 供应商交易字段在这里映射为最小稳定历史条目，保持时间/类型/内容信息。
        data = self._vendor_get("/api/patent/tx", {"id": patent_id})
        if not data.get("success"):
            return _format_json({"error": data.get("error", "Request failed"), "code": data.get("code")})

        transactions = data.get("transactions", [])
        return _format_json(
            {
                "patent_id": patent_id,
                "transaction_count": len(transactions),
                "transactions": [
                    {
                        "date": transaction.get("date"),
                        "type": transaction.get("type"),
                        "application_number": transaction.get("applicationNumber"),
                        "content": transaction.get("content"),
                    }
                    for transaction in transactions
                ],
            }
        )

    def _vendor_get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """按供应商遗留协议发起 GET 请求，并隔离其查询参数 token。

        调用方只传业务参数；该函数注入供应商所需的 ``t`` 与 API 版本，且绝不将
        它们用于自托管路径或日志。缺 token 时直接返回受控错误对象，不尝试匿名请求。
        """
        # 供应商 token 目前按其旧协议放在查询参数 t；这是外部兼容边界，不能把该
        # token 传播到自托管请求或日志字段。
        if not self.config.vendor_api_token:
            return {"success": False, "code": 40101, "error": "PATENTHUB_API_TOKEN is not configured"}
        clean_params = {key: value for key, value in params.items() if value is not None}
        clean_params["t"] = self.config.vendor_api_token
        clean_params["v"] = PATENTHUB_API_VERSION
        return self._request_json("GET", self._vendor_url(path), params=clean_params)

    def _request_json(self, method: str, url: str, **kwargs: Any) -> Dict[str, Any]:
        """执行适配器请求，并把 HTTP/JSON/网络失败压缩为字典结果。

        这是兼容层的非抛出式边界：成功 JSON object 原样交给上层转换；状态码错误、
        非对象响应和 ``httpx.HTTPError`` 则编码成有限 ``success/code/message``
        形状。更外层负责决定它是否成为工具错误，不把异常跨过历史接口。
        """
        # 统一把 HTTP/JSON/网络异常压缩为 dict，调用方再按 success/code 转成工具输出。
        try:
            response = self.client.request(method, url, **kwargs)
            if response.status_code >= 400:
                try:
                    data = response.json()
                except ValueError:
                    return {"success": False, "code": response.status_code, "message": response.text}
                return data if isinstance(data, dict) else {"success": False, "code": response.status_code}
            data = response.json()
            return data if isinstance(data, dict) else {"success": False, "code": 50002, "message": "invalid response"}
        except httpx.HTTPError as exc:
            return {"success": False, "code": 50001, "message": str(exc)}

    def _self_hosted_url(self, path: str) -> str:
        # 只拼接固定 path；调用方负责对路径参数 quote，避免手工 URL 编码分散。
        return self.config.self_hosted_base_url.rstrip("/") + path

    def _vendor_url(self, path: str) -> str:
        return self.config.vendor_base_url.rstrip("/") + path

    def _self_hosted_headers(self) -> Dict[str, str]:
        # 没有自托管 Token 时不发送空的 X-API-Key，方便开发环境关闭鉴权。
        if not self.config.self_hosted_api_token:
            return {}
        return {"X-API-Key": self.config.self_hosted_api_token}

    def _limited_page_size(self, page_size: int) -> int:
        # 工具层强制至少 1，并把调用方请求裁剪到配置上限；后端仍会再次校验硬上限。
        limit = max(1, self.config.page_size_limit)
        return min(max(1, page_size), limit)

    def _tool_error(self, data: Dict[str, Any]) -> Dict[str, Any]:
        # 工具错误只保留 message/error 和 code，不把后端完整响应信封或 header 透传。
        return {
            "error": data.get("message") or data.get("error") or "Request failed",
            "code": data.get("code"),
        }

    def _is_error(self, data: Dict[str, Any]) -> bool:
        # 兼容自托管错误信封和供应商 code 形态。
        return data.get("success") is False or ("code" in data and data.get("code") not in (0, None))

    def _map_search_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """归一化自托管或供应商单条搜索记录为统一 16 字段契约。

        字段别名、IPC、专利类型和空文本都在这一处收敛；任何未经列出的原始字段都
        不会透传。函数接受两条路径的混合命名，但输出只使用当前工具 API 的
        snake_case 名称。
        """
        # 搜索记录统一为 16 个 snake_case 字段；字段别名只在这里处理，避免前端分支。
        return {
            "id": normalized_text(record.get("id") or record.get("patent_id")),
            "application_number": normalized_text(
                _first_value(record, ("application_number", "applicationNumber"))
            ),
            "publication_number": normalized_text(
                _first_value(
                    record,
                    ("publication_number", "publicationNumber", "document_number", "documentNumber"),
                )
            ),
            "title": normalized_text(_first_value(record, ("title", "ti"))),
            "abstract": normalized_text(_first_value(record, ("abstract", "summary", "ab"))),
            "applicant": normalized_text(_first_value(record, ("applicant", "pa"))),
            "current_assignee": normalized_text(
                _first_value(record, ("current_assignee", "currentAssignee", "assignee"))
            ),
            "inventor": normalized_text(_first_value(record, ("inventor",))),
            "main_ipc": normalized_main_ipc(record),
            "ipc_list": _normalized_ipc_list(record),
            "main_claim": normalized_text(_first_value(record, ("main_claim", "mainClaim"))),
            "application_date": normalized_text(
                _first_value(record, ("application_date", "applicationDate", "ad"))
            ),
            "publication_date": normalized_text(
                _first_value(
                    record,
                    ("publication_date", "publicationDate", "document_date", "documentDate"),
                )
            ),
            "legal_status": normalized_text(_first_value(record, ("legal_status", "legalStatus"))),
            "type": _normalized_record_type(record),
            "score": _first_value(record, ("score", "rank"), default=None),
        }

    def _map_vendor_patent(self, patent: Dict[str, Any]) -> Dict[str, Any]:
        return self._map_search_record(patent)

    @staticmethod
    def _normalize_citations_ipc(data: Dict[str, Any]) -> Dict[str, Any]:
        """复制引证响应并归一化其中四类可能携带 IPC 的兼容列表。

        输入对象不原地修改，以免调用方复用同一后端数据时看到意外副作用；不存在的
        字段保持缺失，存在的字段统一交给兼容 IPC mapper 处理。
        """
        # 不原地修改后端对象；四个可能携带 IPC 的兼容列表分别做浅层归一化。
        normalized = dict(data)
        for field in ("cited_by", "patent_references", "referencesCited", "relatedDocuments"):
            if field in normalized:
                normalized[field] = normalize_compat_ipc_records(normalized[field])
        return normalized


def _first_value(record: Dict[str, Any], fields: tuple[str, ...], default: Any = "") -> Any:
    # 对供应商/历史字段按优先级取第一个非空值。
    for field in fields:
        value = record.get(field)
        if value not in (None, ""):
            return value
    return default


def _normalized_ipc_list(record: Dict[str, Any]) -> list[str]:
    # 优先使用已经是公共命名的 ipc_list，否则回退到兼容字段列表。
    if "ipc_list" in record:
        return normalized_ipc_list(record.get("ipc_list"))
    return normalized_record_ipc_list(record)


def _normalized_record_type(record: Dict[str, Any]) -> str:
    # 把工具层命名重新装入公共 mapper 的 source 形状，复用唯一类型优先级。
    return normalized_patent_type(
        {
            "Type": _first_value(record, ("type", "Type")),
            "PatentTypeCode": _first_value(record, ("patent_type_code", "PatentTypeCode")),
            "Kind": _first_value(record, ("kind", "Kind")),
            "PublicationCountry": _first_value(record, ("publication_country", "PublicationCountry")),
        }
    )


def _vendor_detail_source(patent: Dict[str, Any], claims: Any, description: Any) -> Dict[str, Any]:
    """把供应商详情及其补充响应投影为本地 detail mapper 可识别的 source。

    此函数只做字段命名和结构适配，不决定详情的最终空值、图片或优先级展示规则；
    那些规则统一留给 ``map_detail_response``。图片链接在此分为外部摘要图与内部
    存储路径，避免污染 ``images`` 的路径语义。
    """
    # 把供应商详情临时投影成 detail_mapper 认识的 source 字段；这里不直接生成
    # 最终响应，保证自托管/供应商共享同一套空值、IPC、图片和 priority 规则。
    image_path = _first_value(patent, ("image_path", "imagePath"))
    source = {
        "patent_id": patent.get("id") or patent.get("patent_id"),
        "ApplicationNumber": _first_value(patent, ("application_number", "applicationNumber")),
        "PublicationNumber": _first_value(
            patent,
            ("publication_number", "publicationNumber", "document_number", "documentNumber"),
        ),
        "Title": _first_value(patent, ("title", "ti")),
        "Abstract": _first_value(patent, ("abstract", "summary", "ab")),
        "Applicant": _first_value(patent, ("applicant", "pa")),
        "FirstApplicant": _first_value(patent, ("first_applicant", "firstApplicant")),
        "Assignee": _first_value(patent, ("current_assignee", "currentAssignee", "assignee")),
        "Inventor": _first_value(patent, ("inventor",)),
        "FirstInventor": _first_value(patent, ("first_inventor", "firstInventor")),
        "ApplicantAddress": _first_value(patent, ("applicant_address", "applicantAddress")),
        "Agency": _first_value(patent, ("agency",)),
        "Agent": _first_value(patent, ("agent",)),
        "IPC": _first_value(patent, ("main_ipc", "mainIpc", "ipc", "IPC")),
        "IPCList": _first_value(patent, ("ipc_list", "ipcMainList", "ipc_main_list", "IPCList"), default=[]),
        "MainClaim": _first_value(patent, ("main_claim", "mainClaim")),
        "IndependentClaimsOriginal": _first_value(patent, ("independent_claims", "independentClaims")),
        "Requirement": claims,
        "ApplicationDate": _first_value(patent, ("application_date", "applicationDate", "ad")),
        "PublicationDate": _first_value(
            patent,
            ("publication_date", "publicationDate", "document_date", "documentDate"),
        ),
        "LatestLegalStatus": _first_value(patent, ("legal_status", "legalStatus", "current_status", "currentStatus")),
        "Type": _first_value(patent, ("type", "Type")),
        "PatentTypeCode": _first_value(patent, ("patent_type_code", "PatentTypeCode")),
        "Kind": _first_value(patent, ("kind", "Kind")),
        "PublicationCountry": _first_value(patent, ("publication_country", "PublicationCountry")),
        "Priority": _vendor_priority_source(_first_value(patent, ("priority_numbers", "priorityNumbers", "priority_number", "priorityNumber"), default=[])),
        "PCTApplicationDate": _first_value(patent, ("pct_application_date", "pctApplicationDate", "pct_date", "pctDate")),
        "PCTApplicationNumber": _first_value(
            patent,
            ("pct_application_number", "pctApplicationNumber", "pct_application_data", "pctApplicationData"),
        ),
        "PCTPublicationNumber": _first_value(
            patent,
            ("pct_publication_number", "pctPublicationNumber", "pct_publication_data", "pctPublicationData"),
        ),
        "PatentImages": _first_value(patent, ("images", "image_list", "PatentImages"), default=[]),
        "Family": _first_value(patent, ("family", "Family"), default=[]),
        "Instructions": description,
    }
    if _is_external_url(image_path):
        source["AbstractFigureUrl"] = image_path
    else:
        source["PatentImage"] = image_path
    return source


def _vendor_priority_source(value: Any) -> list[dict[str, Any]]:
    """把供应商的 priority 编号或对象列表转换为 detail mapper 所需的对象列表。

    已是对象的条目保留其附加信息；标量编号被包裹为 ``ApplicationNumber``，空值
    被丢弃。函数不负责去重，最终详情 mapper 依据其自身契约处理显示列表。
    """
    # 供应商 priority 可能是编号列表或对象列表，统一成 mapper 需要的 ApplicationNumber。
    values = value if isinstance(value, list) else [value]
    result = []
    for item in values:
        if isinstance(item, dict):
            result.append(item)
        elif item not in (None, ""):
            result.append({"ApplicationNumber": item})
    return result


def _is_external_url(value: Any) -> bool:
    # 供应商图片 URL 与本地 TOS 路径分流，避免把外链写进 images 内部路径列表。
    return isinstance(value, str) and value.lower().startswith(("http://", "https://", "//"))


def patent_search(
    q: str,
    ds: str = "cn",
    page: int = 1,
    page_size: int = 10,
    sort: str = "relation",
    highlight: bool = False,
) -> str:
    """以默认环境配置执行一次兼容专利搜索，供历史直接导入者调用。"""
    return PatentHubToolAdapter().patent_search(q, ds, page, page_size, sort, highlight)


def patent_get_detail(patent_id: str, include_description: bool = False) -> str:
    """以默认环境配置读取一件专利详情。"""
    return PatentHubToolAdapter().patent_get_detail(patent_id, include_description)


def patent_get_citations(patent_id: str) -> str:
    """以默认环境配置读取一件专利的引证数据。"""
    return PatentHubToolAdapter().patent_get_citations(patent_id)


def patent_get_legal_history(patent_id: str) -> str:
    """以默认环境配置读取一件专利的法律历史。"""
    return PatentHubToolAdapter().patent_get_legal_history(patent_id)


def _format_json(data: Dict[str, Any]) -> str:
    # 工具 adapter 对外仍返回 JSON 字符串，MCP client 再严格解析成 object。
    return json.dumps(data, ensure_ascii=False, indent=2)


def _env_bool(name: str, default: bool) -> bool:
    # 只接受常见真值拼写，其余值按 false 处理；环境读取保持与旧 adapter 兼容。
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    # 与 settings 一致地给非法整数使用默认值，避免工具进程导入阶段直接崩溃。
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default
