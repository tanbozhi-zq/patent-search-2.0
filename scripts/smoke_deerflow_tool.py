import json
import os
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from deerflow_tool import tools


SEARCH_CASES = [
    ("keyword", "阀门"),
    ("tscd", "tscd:(阀门 AND 密封)"),
    ("ipc_tscd", "ipc:H02M AND tscd:(均衡 OR 平衡)"),
    ("applicant", "applicant:宁德时代新能源科技股份有限公司"),
    ("current_assignee", "currentAssignee:华为技术有限公司"),
]


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else os.getenv("PATENT_SEARCH_BASE_URL", "http://127.0.0.1:8000")
    api_token = sys.argv[2] if len(sys.argv) > 2 else os.getenv("PATENT_SEARCH_API_TOKEN", "")
    if not api_token:
        api_token = get_settings().api_token

    os.environ["PATENT_SEARCH_BASE_URL"] = base_url
    os.environ["PATENT_SEARCH_API_TOKEN"] = api_token
    os.environ["PATENT_SEARCH_USE_SELF_HOSTED"] = "true"
    os.environ.setdefault("PATENT_SEARCH_PAGE_SIZE_LIMIT", "50")

    first_patent_id = ""
    for name, query in SEARCH_CASES:
        result = _loads(tools.patent_search(q=query, page=1, page_size=1))
        patents = result.get("patents", [])
        ok = "error" not in result and "records" not in result and isinstance(patents, list)
        if patents and not first_patent_id:
            first_patent_id = patents[0].get("id") or ""
        print(
            json.dumps(
                {
                    "name": f"tool_search_{name}",
                    "ok": ok,
                    "total": result.get("total"),
                    "patents": len(patents),
                    "metadata": all(field in result for field in ("total_pages", "next_page", "took_ms")),
                },
                ensure_ascii=False,
            )
        )
        if not ok:
            return 1

    if not first_patent_id:
        print(json.dumps({"name": "tool_chain", "ok": False, "error": "empty patent id"}, ensure_ascii=False))
        return 1

    detail = _loads(tools.patent_get_detail(first_patent_id, include_description=True))
    detail_ok = "error" not in detail and not _missing(detail, ["id", "patent_id", "claims", "description"])
    print(json.dumps({"name": "tool_detail", "ok": detail_ok}, ensure_ascii=False))
    if not detail_ok:
        return 1

    citations = _loads(tools.patent_get_citations(first_patent_id))
    citations_ok = "error" not in citations and not _missing(
        citations,
        ["patent_id", "cited_by", "patent_references", "non_patent_references"],
    )
    print(json.dumps({"name": "tool_citations", "ok": citations_ok}, ensure_ascii=False))
    if not citations_ok:
        return 1

    legal_history = _loads(tools.patent_get_legal_history(first_patent_id))
    legal_ok = "error" not in legal_history and not _missing(
        legal_history,
        ["patent_id", "transaction_count", "transactions"],
    )
    print(json.dumps({"name": "tool_legal_history", "ok": legal_ok}, ensure_ascii=False))
    if not legal_ok:
        return 1

    error = _loads(tools.patent_search(q="ipc:H02M AND AND tscd:(均衡)"))
    error_ok = error.get("code") == 40001 and "error" in error
    print(json.dumps({"name": "tool_error", "ok": error_ok, "code": error.get("code")}, ensure_ascii=False))
    return 0 if error_ok else 1


def _loads(value: str) -> dict:
    return json.loads(value)


def _missing(data: dict, fields: list) -> list:
    return [field for field in fields if field not in data]


if __name__ == "__main__":
    raise SystemExit(main())
