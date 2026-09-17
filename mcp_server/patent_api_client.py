"""MCP client 是 HTTP API 的严格边界适配器：它给后端请求注入关联 ID，验证后端
错误/成功响应的形状和 request-id 一致性，再转换成 MCP 工具需要的 payload。
"""

import json
import logging
from dataclasses import dataclass
from time import monotonic
from typing import Any, Callable, Literal, Optional
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.exceptions import ErrorCode, error_definition
from app.core.logging import log_event
from app.core.request_context import (
    REQUEST_ID_HEADER,
    bind_request_id,
    current_request_id,
    is_valid_request_id,
    new_request_id,
    reset_request_id,
)
from app.integrations.patenthub_adapter import PatentHubAdapterConfig, PatentHubToolAdapter
from app.schemas.response import CitationResponse, LegalHistoryResponse, PatentDetailResponse, SearchResponse
from mcp_server.settings import McpServerSettings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class McpToolResult:
    """MCP 工具执行完成后的安全结果信封。

    ``payload`` 只保存可交给 Agent 的结构化业务数据或已裁剪的错误数据，HTTP
    响应对象、headers 和异常细节不会跨越这一层。``is_error`` 直接映射到 MCP
    协议的 ``isError``，使 transport 层无需重新判断错误形状。
    """

    # is_error 对应 MCP 的 isError；payload 保持工具层对象，不把 HTTP headers/raw
    # response 直接暴露给 Agent。
    payload: dict[str, Any]
    is_error: bool = False


def mcp_error_result(code: ErrorCode) -> McpToolResult:
    """为 MCP 本地拒绝生成与 HTTP 适配错误相同的稳定结果信封。"""
    return McpToolResult(payload=_local_error(code), is_error=True)


class _BackendErrorResponse(BaseModel):
    """后端错误响应在进入 MCP 前必须满足的最小可信结构。

    只接受本服务稳定承诺的字段并忽略未来扩展字段，既能校验 request id 与重试
    语义，也避免把后端临时诊断内容作为 MCP 工具输出的一部分。
    """

    # 后端错误只允许稳定字段，extra=ignore 兼容未来扩展但不让未知数据泄露到 MCP。
    model_config = ConfigDict(extra="ignore", strict=True)

    success: Literal[False]
    code: int
    message: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    retryable: bool


class _McpHttpError(RuntimeError):
    """携带已脱敏工具错误载荷的内部控制流异常。

    它只在 HTTP 校验层和统一 ``_call`` 边界之间传递；调用方看到的是其中的 payload，
    而不是网络库异常、响应正文或堆栈信息。
    """

    # 这是“可安全返回给工具调用方”的错误包装，payload 已经完成字段裁剪。
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        super().__init__(payload["message"])


class _ValidatedHttpClient:
    """为 MCP 到自托管 FastAPI 的调用补齐可信响应边界。

    每个请求都会携带或创建 request id；错误响应必须回显同一标识，成功响应必须
    命中对应 endpoint 的 Pydantic schema。任何网络、编码、状态码或协议异常都会
    在本层收敛为 ``_McpHttpError``，不能让未经验证的后端数据进入工具输出。
    """

    def __init__(self, client: httpx.Client):
        self._client = client

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """执行一次 HTTP 请求并验证其错误或成功契约。

        返回值仍是原始 ``httpx.Response``，供兼容适配器读取已验证的 JSON；失败
        情况不返回半成品响应，而是转换为带安全 payload 的 ``_McpHttpError``。URL
        路径必须属于四个明确允许的业务端点，防止该 client 变成通用代理。
        """
        # 每次向 FastAPI 发请求都沿用当前 MCP 工具的 request_id；如果上层没有
        # 上下文则生成一个。后端 response header/body 必须回传同一个 ID。
        outbound_request_id = current_request_id() or new_request_id()
        headers = httpx.Headers(kwargs.pop("headers", None))
        headers[REQUEST_ID_HEADER] = outbound_request_id
        kwargs["headers"] = headers
        try:
            response = self._client.request(method, url, **kwargs)
        except httpx.InvalidURL as exc:
            raise _McpHttpError(_local_error(ErrorCode.SEARCH_DEPENDENCY_ERROR)) from exc
        except httpx.TimeoutException as exc:
            raise _McpHttpError(_local_error(ErrorCode.SEARCH_DEPENDENCY_TIMEOUT)) from exc
        except (httpx.NetworkError, httpx.ProxyError) as exc:
            raise _McpHttpError(_local_error(ErrorCode.SEARCH_DEPENDENCY_UNAVAILABLE)) from exc
        except (httpx.ProtocolError, httpx.DecodingError, httpx.RequestError) as exc:
            raise _McpHttpError(_local_error(ErrorCode.SEARCH_DEPENDENCY_ERROR)) from exc

        # 先解析为对象，再判断 HTTP 状态/业务 code；非对象、坏 JSON 和不完整错误
        # 都统一映射为安全的 50001，而不把响应原文返给 MCP。
        data = _response_json(response)
        has_error_code = "code" in data and data.get("code") not in (0, None)
        if not 200 <= response.status_code < 300 or data.get("success") is False or has_error_code:
            try:
                error = _BackendErrorResponse.model_validate(data)
            except ValidationError as exc:
                raise _McpHttpError(_local_error(ErrorCode.SEARCH_DEPENDENCY_ERROR)) from exc
            response_request_id = response.headers.get(REQUEST_ID_HEADER)
            if (
                not is_valid_request_id(error.request_id)
                or error.request_id != outbound_request_id
                or (
                    response_request_id is not None
                    and response_request_id != outbound_request_id
                )
            ):
                raise _McpHttpError(_local_error(ErrorCode.SEARCH_DEPENDENCY_ERROR))
            raise _McpHttpError(
                {
                    "error": error.message,
                    "code": error.code,
                    "message": error.message,
                    "request_id": error.request_id,
                    "retryable": error.retryable,
                }
            )

        # 成功路径按 URL 白名单选择 Pydantic 模型，防止某个“200 + 任意 JSON”接口
        # 被误当成合法工具结果。
        model = _success_model(urlparse(str(url)).path)
        if model is None:
            raise _McpHttpError(_local_error(ErrorCode.SEARCH_DEPENDENCY_ERROR))
        try:
            model.model_validate(data, strict=True)
        except ValidationError as exc:
            raise _McpHttpError(_local_error(ErrorCode.SEARCH_DEPENDENCY_ERROR)) from exc
        return response


class PatentApiClient:
    """MCP 工具与专利 HTTP 服务之间的单一调用门面。

    客户端不直接访问 OpenSearch：它将工具参数交给 ``PatentHubToolAdapter``，后者
    负责兼容输出形状；本类则负责一次工具调用的 request-id 生命周期、异常收口和
    完成审计。构造函数允许注入 HTTP client，便于在测试中验证边界而不依赖网络。
    """

    def __init__(
        self,
        settings: Optional[McpServerSettings] = None,
        http_client: Optional[httpx.Client] = None,
    ):
        self.settings = settings or McpServerSettings.from_env()
        client = http_client or httpx.Client(timeout=self.settings.timeout_seconds)
        self._adapter = PatentHubToolAdapter(
            config=PatentHubAdapterConfig(
                self_hosted_base_url=self.settings.base_url,
                self_hosted_api_token=self.settings.api_token,
                use_self_hosted=True,
                page_size_limit=self.settings.page_size_limit,
                timeout_seconds=self.settings.timeout_seconds,
            ),
            client=_ValidatedHttpClient(client),
        )

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
    ) -> McpToolResult:
        """执行专利检索，并返回 MCP 规定的成功或错误结果信封。

        查询参数沿用工具 schema；分页上限和响应字段兼容由 adapter 统一处理。无论
        后端业务拒绝、网络失败还是内部异常，调用方都得到 ``McpToolResult``，不会
        直接接收 Python 异常。
        """
        # Adapter 负责把 HTTP records 改成 PatentHub 风格 patents；这里保持工具
        # 的参数形状和错误收口一致。
        return self._call(
            "patent_search",
            lambda: self._adapter.patent_search(
                q,
                ds,
                page,
                page_size,
                sort,
                highlight,
                mode=mode,
                semantic_text=semantic_text,
                vector_fields=vector_fields,
                top_k=top_k,
            ),
        )

    def patent_get_detail(self, patent_id: str, include_description: bool = False) -> McpToolResult:
        """按稳定专利 ID 读取详情；说明书仅在显式请求时包含。"""
        return self._call(
            "patent_get_detail",
            lambda: self._adapter.patent_get_detail(patent_id, include_description),
        )

    def patent_get_citations(self, patent_id: str) -> McpToolResult:
        """按稳定专利 ID 读取引证摘要与兼容引用字段。"""
        return self._call(
            "patent_get_citations",
            lambda: self._adapter.patent_get_citations(patent_id),
        )

    def patent_get_legal_history(self, patent_id: str) -> McpToolResult:
        """按稳定专利 ID 读取法律历史的标准交易结构。"""
        return self._call(
            "patent_get_legal_history",
            lambda: self._adapter.patent_get_legal_history(patent_id),
        )

    @staticmethod
    def _call(operation: str, call: Callable[[], str]) -> McpToolResult:
        """作为每个 MCP 工具调用的统一完成、错误转换和审计边界。

        该函数建立独立 request id 上下文，执行 adapter 返回的 JSON 字符串，并在
        ``finally`` 中记录不含查询内容的完成事件。预期 HTTP 错误保留已验证的
        后端 request id；未知异常只输出通用内部错误，异常类型仅进入服务端日志。
        """
        # _call 是 MCP 侧唯一的完成边界：成功、后端业务错误、网络错误和意外异常
        # 都产生一个工具结果，并写一条不含查询文本的完成事件。
        request_id = current_request_id() or new_request_id()
        token = bind_request_id(request_id)
        started = monotonic()
        outcome = "success"
        code = 0
        exception_type: str | None = None
        level = logging.INFO
        try:
            result = McpToolResult(payload=_loads(call()))
        except _McpHttpError as exc:
            # _McpHttpError.payload 已经是面向调用方的安全错误；保留后端 request_id
            # 以便跨 MCP → FastAPI → OpenSearch 关联。
            request_id = exc.payload["request_id"]
            outcome = "backend_error"
            code = exc.payload["code"]
            level = logging.WARNING
            result = McpToolResult(payload=exc.payload, is_error=True)
        except Exception as exc:
            # 未预期异常只暴露 50002 和生成的 request_id，异常类型仅进结构化日志。
            payload = _local_error(ErrorCode.INTERNAL_ERROR)
            request_id = payload["request_id"]
            outcome = "unexpected_error"
            code = payload["code"]
            exception_type = type(exc).__name__
            level = logging.ERROR
            result = McpToolResult(payload=payload, is_error=True)
        finally:
            try:
                log_event(
                    logger,
                    level,
                    "mcp_tool_completed",
                    request_id=request_id,
                    dependency="patent_search_api",
                    operation=operation,
                    outcome=outcome,
                    code=code,
                    elapsed_ms=round((monotonic() - started) * 1000, 3),
                    retry_count=0,
                    exception_type=exception_type,
                )
            finally:
                reset_request_id(token)
        return result


def _response_json(response: httpx.Response) -> dict[str, Any]:
    """将 HTTP 响应限制为 JSON object，否则生成安全的依赖错误。"""
    # HTTP 200/错误响应都必须是 JSON object；数组、纯文本和坏编码不能进入后续映射。
    try:
        data = response.json()
    except (ValueError, UnicodeError) as exc:
        raise _McpHttpError(_local_error(ErrorCode.SEARCH_DEPENDENCY_ERROR)) from exc
    if not isinstance(data, dict):
        raise _McpHttpError(_local_error(ErrorCode.SEARCH_DEPENDENCY_ERROR))
    return data


def _success_model(path: str) -> type[BaseModel] | None:
    """根据白名单路径选择成功响应的校验模型，未知路径一律拒绝。"""
    # 只允许四个自托管业务 endpoint 作为成功工具结果来源。
    if path.endswith("/api/patent/search"):
        return SearchResponse
    if "/api/patent/detail/" in path:
        return PatentDetailResponse
    if "/api/patent/citations/" in path:
        return CitationResponse
    if "/api/patent/legal-history/" in path:
        return LegalHistoryResponse
    return None


def _local_error(code: ErrorCode) -> dict[str, Any]:
    # 网络/解析错误没有后端 request_id，使用当前工具上下文或生成新的 opaque ID。
    definition = error_definition(code)
    return {
        "error": definition.message,
        "code": int(code),
        "message": definition.message,
        "request_id": current_request_id() or new_request_id(),
        "retryable": definition.retryable,
    }


def _loads(value: str) -> dict[str, Any]:
    # Adapter 返回的是 JSON 字符串；MCP 工具层坚持 object payload，拒绝标量/数组。
    data = json.loads(value)
    if not isinstance(data, dict):
        raise TypeError("tool result must be an object")
    return data
