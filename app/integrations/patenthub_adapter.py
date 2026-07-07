import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import quote

import httpx


DEFAULT_PATENTHUB_BASE_URL = "https://www.patenthub.cn"
PATENTHUB_API_VERSION = "1"


@dataclass
class PatentHubAdapterConfig:
    self_hosted_base_url: str = "http://127.0.0.1:8000"
    self_hosted_api_token: str = ""
    use_self_hosted: bool = True
    page_size_limit: int = 50
    vendor_base_url: str = DEFAULT_PATENTHUB_BASE_URL
    vendor_api_token: str = ""
    timeout_seconds: int = 30

    @classmethod
    def from_env(cls) -> "PatentHubAdapterConfig":
        return cls(
            self_hosted_base_url=os.getenv("PATENT_SEARCH_BASE_URL", "http://127.0.0.1:8000"),
            self_hosted_api_token=os.getenv("PATENT_SEARCH_API_TOKEN", ""),
            use_self_hosted=_env_bool("PATENT_SEARCH_USE_SELF_HOSTED", True),
            page_size_limit=_env_int("PATENT_SEARCH_PAGE_SIZE_LIMIT", 50),
            vendor_base_url=os.getenv("PATENTHUB_BASE_URL", DEFAULT_PATENTHUB_BASE_URL),
            vendor_api_token=os.getenv("PATENTHUB_API_TOKEN", ""),
            timeout_seconds=_env_int("PATENT_SEARCH_TIMEOUT_SECONDS", 30),
        )


class PatentHubToolAdapter:
    def __init__(
        self,
        config: Optional[PatentHubAdapterConfig] = None,
        client: Optional[httpx.Client] = None,
    ):
        self.config = config or PatentHubAdapterConfig.from_env()
        self.client = client or httpx.Client(timeout=self.config.timeout_seconds)

    def patent_search(
        self,
        q: str,
        ds: str = "cn",
        page: int = 1,
        page_size: int = 10,
        sort: str = "relation",
        highlight: bool = False,
    ) -> str:
        if not self.config.use_self_hosted:
            return self._vendor_search(q, ds, page, page_size, sort, highlight)

        payload = {
            "q": q,
            "ds": ds,
            "page": page,
            "page_size": self._limited_page_size(page_size),
            "sort": sort,
            "highlight": 1 if highlight else 0,
        }
        data = self._request_json(
            "POST",
            self._self_hosted_url("/api/patent/search"),
            json=payload,
            headers=self._self_hosted_headers(),
        )
        if self._is_error(data):
            return _format_json(self._tool_error(data))

        records = data.get("records", [])
        return _format_json(
            {
                "total": data.get("total"),
                "page": data.get("page", page),
                "page_size": data.get("page_size", payload["page_size"]),
                "total_pages": data.get("total_pages"),
                "next_page": data.get("next_page"),
                "took_ms": data.get("took_ms"),
                "patents": [self._map_search_record(record) for record in records],
            }
        )

    def patent_get_detail(self, patent_id: str, include_description: bool = False) -> str:
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
        return _format_json(data)

    def patent_get_legal_history(self, patent_id: str) -> str:
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
        base_data = self._vendor_get("/api/patent/base", {"id": patent_id})
        if not base_data.get("success"):
            return _format_json({"error": base_data.get("error", "Request failed"), "code": base_data.get("code")})

        claims_data = self._vendor_get("/api/patent/claims", {"id": patent_id})
        patent = base_data.get("patent", {})
        result = {
            "id": patent.get("id"),
            "title": patent.get("title"),
            "type": patent.get("type"),
            "legal_status": patent.get("legalStatus"),
            "current_status": patent.get("currentStatus"),
            "application_number": patent.get("applicationNumber"),
            "application_date": patent.get("applicationDate"),
            "document_number": patent.get("documentNumber"),
            "document_date": patent.get("documentDate"),
            "applicant": patent.get("applicant"),
            "first_applicant": patent.get("firstApplicant"),
            "current_assignee": patent.get("currentAssignee"),
            "assignee": patent.get("assignee"),
            "inventor": patent.get("inventor"),
            "first_inventor": patent.get("firstInventor"),
            "applicant_address": patent.get("applicantAddress"),
            "agency": patent.get("agency"),
            "agent": patent.get("agent"),
            "ipc": patent.get("ipc"),
            "main_ipc": patent.get("mainIpc"),
            "loc": patent.get("loc"),
            "priority_number": patent.get("priorityNumber"),
            "full_priority_number": patent.get("fullPriorityNumber"),
            "pct_date": patent.get("pctDate"),
            "pct_application_data": patent.get("pctApplicationData"),
            "pct_publication_data": patent.get("pctPublicationData"),
            "image_path": patent.get("imagePath"),
            "pdf_list": patent.get("pdfList", []),
            "summary": patent.get("summary"),
            "claims": claims_data.get("patent", {}).get("claims") if claims_data.get("success") else None,
        }
        if include_description:
            desc_data = self._vendor_get("/api/patent/desc", {"id": patent_id})
            result["description"] = (
                desc_data.get("patent", {}).get("description") if desc_data.get("success") else None
            )
        return _format_json(result)

    def _vendor_citations(self, patent_id: str) -> str:
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
        if not self.config.vendor_api_token:
            return {"success": False, "code": 40101, "error": "PATENTHUB_API_TOKEN is not configured"}
        clean_params = {key: value for key, value in params.items() if value is not None}
        clean_params["t"] = self.config.vendor_api_token
        clean_params["v"] = PATENTHUB_API_VERSION
        return self._request_json("GET", self._vendor_url(path), params=clean_params)

    def _request_json(self, method: str, url: str, **kwargs: Any) -> Dict[str, Any]:
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
        return self.config.self_hosted_base_url.rstrip("/") + path

    def _vendor_url(self, path: str) -> str:
        return self.config.vendor_base_url.rstrip("/") + path

    def _self_hosted_headers(self) -> Dict[str, str]:
        if not self.config.self_hosted_api_token:
            return {}
        return {"X-API-Key": self.config.self_hosted_api_token}

    def _limited_page_size(self, page_size: int) -> int:
        limit = max(1, self.config.page_size_limit)
        return min(max(1, page_size), limit)

    def _tool_error(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "error": data.get("message") or data.get("error") or "Request failed",
            "code": data.get("code"),
        }

    def _is_error(self, data: Dict[str, Any]) -> bool:
        return data.get("success") is False or ("code" in data and data.get("code") not in (0, None))

    def _map_search_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": record.get("id") or record.get("patent_id"),
            "title": record.get("title"),
            "applicant": record.get("applicant"),
            "application_date": record.get("application_date") or record.get("applicationDate"),
            "application_number": record.get("application_number") or record.get("applicationNumber"),
            "document_number": record.get("document_number") or record.get("documentNumber"),
            "document_date": record.get("document_date") or record.get("documentDate"),
            "type": record.get("type"),
            "legal_status": record.get("legal_status") or record.get("legalStatus"),
            "main_ipc": record.get("main_ipc") or record.get("mainIpc"),
            "rank": record.get("rank"),
            "inventor": record.get("inventor"),
            "summary": record.get("summary", ""),
        }

    def _map_vendor_patent(self, patent: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": patent.get("id"),
            "title": patent.get("title"),
            "applicant": patent.get("applicant"),
            "application_date": patent.get("applicationDate"),
            "application_number": patent.get("applicationNumber"),
            "document_number": patent.get("documentNumber"),
            "document_date": patent.get("documentDate"),
            "type": patent.get("type"),
            "legal_status": patent.get("legalStatus"),
            "main_ipc": patent.get("mainIpc"),
            "rank": patent.get("rank"),
            "inventor": patent.get("inventor"),
            "summary": patent.get("summary", ""),
        }


def patent_search(
    q: str,
    ds: str = "cn",
    page: int = 1,
    page_size: int = 10,
    sort: str = "relation",
    highlight: bool = False,
) -> str:
    return PatentHubToolAdapter().patent_search(q, ds, page, page_size, sort, highlight)


def patent_get_detail(patent_id: str, include_description: bool = False) -> str:
    return PatentHubToolAdapter().patent_get_detail(patent_id, include_description)


def patent_get_citations(patent_id: str) -> str:
    return PatentHubToolAdapter().patent_get_citations(patent_id)


def patent_get_legal_history(patent_id: str) -> str:
    return PatentHubToolAdapter().patent_get_legal_history(patent_id)


def _format_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default
