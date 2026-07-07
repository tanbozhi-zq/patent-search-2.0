import json
import os
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.integrations.patenthub_adapter import PatentHubToolAdapter


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else os.getenv("PATENT_SEARCH_BASE_URL", "http://127.0.0.1:8000")
    api_token = sys.argv[2] if len(sys.argv) > 2 else os.getenv("PATENT_SEARCH_API_TOKEN", "")

    if not api_token:
        api_token = get_settings().api_token

    os.environ["PATENT_SEARCH_BASE_URL"] = base_url
    os.environ["PATENT_SEARCH_API_TOKEN"] = api_token
    os.environ["PATENT_SEARCH_USE_SELF_HOSTED"] = "true"
    os.environ.setdefault("PATENT_SEARCH_PAGE_SIZE_LIMIT", "50")

    adapter = PatentHubToolAdapter()

    search = _loads(adapter.patent_search(q="阀门", page=1, page_size=10))
    if "error" in search:
        print(json.dumps({"name": "adapter_search", "ok": False, "result": search}, ensure_ascii=False))
        return 1
    patents = search.get("patents", [])
    patent_id = patents[0].get("id") if patents else ""
    missing = _missing_search_fields(patents[0]) if patents else ["patents[0]"]
    print(
        json.dumps(
            {
                "name": "adapter_search",
                "ok": bool(patent_id) and not missing and _has_search_metadata(search),
                "total": search.get("total"),
                "patents": len(patents),
                "patent_id": patent_id,
                "metadata": _has_search_metadata(search),
                "missing_fields": missing,
            },
            ensure_ascii=False,
        )
    )
    if not patent_id or missing or not _has_search_metadata(search):
        return 1

    detail = _loads(adapter.patent_get_detail(patent_id))
    detail_missing = _missing_detail_fields(detail)
    print(
        json.dumps(
            {
                "name": "adapter_detail",
                "ok": "error" not in detail and not detail_missing and "description" not in detail,
                "missing_fields": detail_missing,
                "has_description": "description" in detail,
            },
            ensure_ascii=False,
        )
    )
    if "error" in detail or detail_missing or "description" in detail:
        return 1

    detail_with_description = _loads(adapter.patent_get_detail(patent_id, include_description=True))
    print(
        json.dumps(
            {
                "name": "adapter_detail_description",
                "ok": "error" not in detail_with_description and "description" in detail_with_description,
                "has_description": "description" in detail_with_description,
            },
            ensure_ascii=False,
        )
    )
    if "error" in detail_with_description or "description" not in detail_with_description:
        return 1

    citations = _loads(adapter.patent_get_citations(patent_id))
    citation_missing = _missing_citation_fields(citations)
    print(
        json.dumps(
            {
                "name": "adapter_citations",
                "ok": "error" not in citations and not citation_missing,
                "missing_fields": citation_missing,
            },
            ensure_ascii=False,
        )
    )
    if "error" in citations or citation_missing:
        return 1

    legal_history = _loads(adapter.patent_get_legal_history(patent_id))
    legal_missing = _missing_legal_history_fields(legal_history)
    print(
        json.dumps(
            {
                "name": "adapter_legal_history",
                "ok": "error" not in legal_history and not legal_missing,
                "missing_fields": legal_missing,
            },
            ensure_ascii=False,
        )
    )
    if "error" in legal_history or legal_missing:
        return 1

    error = _loads(adapter.patent_search(q="ipc:H02M AND AND tscd:(均衡)"))
    print(
        json.dumps(
            {
                "name": "adapter_error",
                "ok": error.get("code") == 40001 and "error" in error,
                "code": error.get("code"),
            },
            ensure_ascii=False,
        )
    )
    if error.get("code") != 40001 or "error" not in error:
        return 1

    return 0


def _loads(value: str) -> dict:
    return json.loads(value)


def _missing_search_fields(record: dict) -> list:
    required = [
        "id",
        "title",
        "summary",
        "application_number",
        "document_number",
        "application_date",
        "document_date",
        "legal_status",
        "main_ipc",
    ]
    return [field for field in required if field not in record]


def _has_search_metadata(search: dict) -> bool:
    return all(field in search for field in ("total_pages", "next_page", "took_ms"))


def _missing_detail_fields(detail: dict) -> list:
    required = [
        "id",
        "patent_id",
        "title",
        "summary",
        "application_number",
        "document_number",
        "legal_status",
        "current_status",
        "current_assignee",
        "main_ipc",
        "claims",
    ]
    return [field for field in required if field not in detail]


def _missing_citation_fields(citations: dict) -> list:
    required = ["patent_id", "cited_by", "patent_references", "non_patent_references"]
    return [field for field in required if field not in citations]


def _missing_legal_history_fields(legal_history: dict) -> list:
    required = ["patent_id", "transaction_count", "transactions"]
    return [field for field in required if field not in legal_history]


if __name__ == "__main__":
    raise SystemExit(main())
