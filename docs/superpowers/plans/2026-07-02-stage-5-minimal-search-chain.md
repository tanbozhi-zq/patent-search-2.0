# Stage 5 Minimal Search Chain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the first usable `POST /api/patent/search` chain: HTTP request -> validation -> minimal query DSL -> OpenSearch search -> result mapping -> vendor-like response.

**Architecture:** Keep Stage 5 narrow. `app/api/search.py` handles HTTP, `app/schemas/search.py` validates request parameters, `app/query/dsl_builder.py` builds only the Stage 5 DSL subset, `app/repositories/opensearch_repo.py` executes OpenSearch calls, `app/mappings/result_mapper.py` formats records, and `app/services/search_service.py` orchestrates the chain.

**Tech Stack:** Python 3.9.19, FastAPI, Pydantic Settings, OpenSearch Python Client, pytest, httpx.

## Global Constraints

- Stage 5 implements only `POST /api/patent/search`.
- Stage 5 must support only these query forms: plain keyword, `title:(...)`, `ab:(...)`, `ipc:...`, `ad:[YYYY-MM-DD TO YYYY-MM-DD]`.
- Stage 5 must support `page`, `page_size`, `sort=relation`, `sort=!applicationDate`, `ds=cn/all`, and `highlight=0/1` parameter acceptance.
- Stage 5 must not implement `tscd`, `applicant`, `currentAssignee`, `legalStatus`, `type`, `documentYear`, nested parentheses, complex `AND/OR`, or `NOT`.
- Business success response must be close to the vendor shape and must not force `success/code/message/data`.
- Error responses use the unified error shape from `docs/api_spec.md`.
- Tests must cover DSL and mapper without requiring live OpenSearch.
- Live OpenSearch smoke test may require `.env` with real credentials and should be opt-in.

---

## File Structure

Create or modify:

```text
app/api/search.py
app/mappings/__init__.py
app/mappings/result_mapper.py
app/query/__init__.py
app/query/dsl_builder.py
app/repositories/opensearch_repo.py
app/schemas/search.py
app/services/__init__.py
app/services/search_service.py
scripts/smoke_search.py
tests/test_search_api.py
tests/test_search_dsl_builder.py
tests/test_search_result_mapper.py
tests/test_search_service.py
```

## Task 1: Search Request Schema

**Files:**
- Create: `app/schemas/search.py`
- Test: `tests/test_search_api.py`

**Interfaces:**
- Produces: `SearchRequest`
- Produces: `SearchRequest.offset -> int`

- [ ] **Step 1: Write request validation tests**

```python
from pydantic import ValidationError
import pytest

from app.schemas.search import SearchRequest


def test_search_request_defaults():
    request = SearchRequest(q="阀门")

    assert request.ds == "cn"
    assert request.sort == "relation"
    assert request.page == 1
    assert request.page_size == 50
    assert request.highlight == 0
    assert request.offset == 0


def test_search_request_rejects_invalid_page_size():
    with pytest.raises(ValidationError):
        SearchRequest(q="阀门", page_size=101)
```

- [ ] **Step 2: Run failing tests**

Run: `python3 -m pytest tests/test_search_api.py -q`

Expected: FAIL because `app.schemas.search` does not exist.

- [ ] **Step 3: Implement schema**

```python
from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    q: str = Field(min_length=1, max_length=1000)
    ds: str = Field(default="cn", pattern="^(cn|all)$")
    sort: str = Field(default="relation", pattern="^(relation|!applicationDate)$")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)
    highlight: int = Field(default=0, ge=0, le=1)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_search_api.py -q`

Expected: PASS.

## Task 2: Minimal DSL Builder

**Files:**
- Create: `app/query/__init__.py`
- Create: `app/query/dsl_builder.py`
- Test: `tests/test_search_dsl_builder.py`

**Interfaces:**
- Consumes: `SearchRequest`
- Produces: `build_search_dsl(request: SearchRequest) -> dict`

- [ ] **Step 1: Write DSL tests**

```python
import pytest

from app.query.dsl_builder import UnsupportedQuerySyntax, build_search_dsl
from app.schemas.search import SearchRequest


def test_plain_keyword_searches_title_and_abstract():
    dsl = build_search_dsl(SearchRequest(q="阀门"))

    assert dsl["query"]["bool"]["must"][0]["multi_match"]["query"] == "阀门"
    assert dsl["query"]["bool"]["must"][0]["multi_match"]["fields"] == ["Title", "Abstract"]


def test_title_query_uses_title_fields():
    dsl = build_search_dsl(SearchRequest(q="title:(阀门)"))

    fields = dsl["query"]["bool"]["must"][0]["multi_match"]["fields"]
    assert fields == ["Title", "TitleCN", "TitleEN"]


def test_ipc_query_uses_should_terms():
    dsl = build_search_dsl(SearchRequest(q="ipc:H02M"))

    should = dsl["query"]["bool"]["must"][0]["bool"]["should"]
    assert {"term": {"IPC": "H02M"}} in should
    assert {"match": {"IPCList": "H02M"}} in should


def test_application_date_range_query():
    dsl = build_search_dsl(SearchRequest(q="ad:[2020-01-01 TO 2020-12-31]"))

    assert dsl["query"]["bool"]["must"][0]["range"]["ApplicationDate"] == {
        "gte": "2020-01-01",
        "lte": "2020-12-31",
    }


def test_rejects_stage_six_syntax():
    with pytest.raises(UnsupportedQuerySyntax):
        build_search_dsl(SearchRequest(q="tscd:(均衡)"))
```

- [ ] **Step 2: Run failing tests**

Run: `python3 -m pytest tests/test_search_dsl_builder.py -q`

Expected: FAIL because `app.query.dsl_builder` does not exist.

- [ ] **Step 3: Implement minimal DSL builder**

```python
import re

from app.schemas.search import SearchRequest


class UnsupportedQuerySyntax(ValueError):
    pass


def build_search_dsl(request: SearchRequest) -> dict:
    must = [_build_query_clause(request.q)]
    filters = []

    if request.ds == "cn":
        filters.append({"term": {"Country": "CN"}})

    sort = _build_sort(request.sort)

    return {
        "from": request.offset,
        "size": request.page_size,
        "track_total_hits": True,
        "query": {
            "bool": {
                "must": must,
                "filter": filters,
            }
        },
        "sort": sort,
    }


def _build_query_clause(q: str) -> dict:
    q = q.strip()

    title_match = re.fullmatch(r"title:\((.+)\)", q)
    if title_match:
        return _multi_match(_strip_quotes(title_match.group(1)), ["Title", "TitleCN", "TitleEN"])

    abstract_match = re.fullmatch(r"ab:\((.+)\)", q)
    if abstract_match:
        return _multi_match(_strip_quotes(abstract_match.group(1)), ["Abstract", "AbstractCN", "AbstractEN"])

    ipc_match = re.fullmatch(r"ipc:([A-Za-z0-9/]+)", q)
    if ipc_match:
        code = ipc_match.group(1)
        return {
            "bool": {
                "should": [
                    {"term": {"IPC": code}},
                    {"match": {"IPCList": code}},
                    {"term": {"IPCSmallCategory": code}},
                ],
                "minimum_should_match": 1,
            }
        }

    date_match = re.fullmatch(r"ad:\[(\d{4}-\d{2}-\d{2}) TO (\d{4}-\d{2}-\d{2})\]", q)
    if date_match:
        return {
            "range": {
                "ApplicationDate": {
                    "gte": date_match.group(1),
                    "lte": date_match.group(2),
                }
            }
        }

    if any(token in q for token in ["tscd:", "applicant:", "currentAssignee:", "legalStatus:", "type:", "documentYear:", " AND ", " OR ", " NOT "]):
        raise UnsupportedQuerySyntax("q 查询语法错误：暂不支持该语法")

    return _multi_match(_strip_quotes(q), ["Title", "Abstract"])


def _multi_match(query: str, fields: list) -> dict:
    return {
        "multi_match": {
            "query": query,
            "fields": fields,
        }
    }


def _strip_quotes(value: str) -> str:
    return value.strip().strip('"')


def _build_sort(sort: str) -> list:
    if sort == "!applicationDate":
        return [{"ApplicationDate": {"order": "desc"}}]
    return ["_score"]
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_search_dsl_builder.py -q`

Expected: PASS.

## Task 3: Result Mapper

**Files:**
- Create: `app/mappings/__init__.py`
- Create: `app/mappings/result_mapper.py`
- Test: `tests/test_search_result_mapper.py`

**Interfaces:**
- Produces: `map_search_response(raw: dict, page: int, page_size: int) -> dict`

- [ ] **Step 1: Write mapper tests**

```python
from app.mappings.result_mapper import map_search_response


def test_maps_search_response_to_vendor_like_shape():
    raw = {
        "hits": {
            "total": {"value": 1},
            "hits": [
                {
                    "_score": 12.3,
                    "_source": {
                        "patent_id": "cn-1",
                        "ApplicationNumber": "CN1",
                        "PublicationNumber": "CN1A",
                        "Title": "标题",
                        "Abstract": "摘要",
                        "Applicant": "申请人",
                        "Assignee": "权利人",
                        "Inventor": "发明人",
                        "IPC": "H02M",
                        "IPCList": ["H02M"],
                        "ApplicationDate": "2020-01-01",
                        "PublicationDate": "2020-02-01",
                        "LatestLegalStatus": "授权",
                        "Type": "发明专利",
                    },
                }
            ],
        }
    }

    mapped = map_search_response(raw, page=1, page_size=50)

    assert mapped["total"] == 1
    assert mapped["records"][0]["patent_id"] == "cn-1"
    assert mapped["records"][0]["title"] == "标题"
    assert mapped["records"][0]["ipcMainList"] == ["H02M"]
```

- [ ] **Step 2: Run failing tests**

Run: `python3 -m pytest tests/test_search_result_mapper.py -q`

Expected: FAIL because `app.mappings.result_mapper` does not exist.

- [ ] **Step 3: Implement mapper**

```python
def map_search_response(raw: dict, page: int, page_size: int) -> dict:
    hits = raw.get("hits", {})
    total = _extract_total(hits.get("total", 0))
    records = [_map_record(hit) for hit in hits.get("hits", [])]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "records": records,
    }


def _extract_total(total):
    if isinstance(total, dict):
        return total.get("value", 0)
    return total or 0


def _map_record(hit: dict) -> dict:
    source = hit.get("_source", {})
    patent_id = _string(source.get("patent_id"))
    title = _string(source.get("Title"))
    abstract = _string(source.get("Abstract"))
    applicant = _string(source.get("Applicant"))
    legal_status = _string(source.get("LatestLegalStatus") or source.get("LegalStatus"))

    return {
        "id": patent_id,
        "patent_id": patent_id,
        "applicationNumber": _string(source.get("ApplicationNumber")),
        "documentNumber": _string(source.get("PublicationNumber")),
        "title": title,
        "ti": title,
        "abstract": abstract,
        "ab": abstract,
        "applicant": applicant,
        "pa": applicant,
        "currentAssignee": _string(source.get("Assignee") or source.get("Applicant")),
        "inventor": _string(source.get("Inventor")),
        "mainIpc": _string(source.get("IPC")),
        "ipcMainList": _array(source.get("IPCList")),
        "applicationDate": _string(source.get("ApplicationDate")),
        "ad": _string(source.get("ApplicationDate")),
        "documentDate": _string(source.get("PublicationDate")),
        "legalStatus": legal_status,
        "currentStatus": _string(source.get("LatestLegalStatus")),
        "type": _string(source.get("Type")),
        "score": hit.get("_score"),
    }


def _string(value) -> str:
    if value is None:
        return ""
    return str(value)


def _array(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]
```

- [ ] **Step 4: Run tests**

Run: `python3 -m pytest tests/test_search_result_mapper.py -q`

Expected: PASS.

## Task 4: Repository Search Method And Service

**Files:**
- Modify: `app/repositories/opensearch_repo.py`
- Create: `app/services/__init__.py`
- Create: `app/services/search_service.py`
- Test: `tests/test_search_service.py`

**Interfaces:**
- Produces: `OpenSearchRepository.search(body: dict) -> dict`
- Produces: `SearchService.search(request: SearchRequest) -> dict`

- [ ] **Step 1: Write service test with fake repository**

```python
from app.schemas.search import SearchRequest
from app.services.search_service import SearchService


class FakeRepository:
    def __init__(self):
        self.body = None

    def search(self, body):
        self.body = body
        return {"hits": {"total": {"value": 0}, "hits": []}}


def test_search_service_builds_dsl_and_maps_response():
    repository = FakeRepository()
    service = SearchService(repository=repository)

    result = service.search(SearchRequest(q="阀门"))

    assert repository.body["size"] == 50
    assert result == {"total": 0, "page": 1, "page_size": 50, "records": []}
```

- [ ] **Step 2: Run failing test**

Run: `python3 -m pytest tests/test_search_service.py -q`

Expected: FAIL because `app.services.search_service` does not exist.

- [ ] **Step 3: Add repository search method**

Add to `OpenSearchRepository`:

```python
    def search(self, body: dict) -> dict:
        return self.client.search(index=self.index_name, body=body)
```

- [ ] **Step 4: Implement search service**

```python
from typing import Optional

from app.mappings.result_mapper import map_search_response
from app.query.dsl_builder import build_search_dsl
from app.repositories.opensearch_repo import OpenSearchRepository
from app.schemas.search import SearchRequest


class SearchService:
    def __init__(self, repository: Optional[OpenSearchRepository] = None):
        self.repository = repository or OpenSearchRepository()

    def search(self, request: SearchRequest) -> dict:
        body = build_search_dsl(request)
        raw = self.repository.search(body)
        return map_search_response(raw, page=request.page, page_size=request.page_size)
```

- [ ] **Step 5: Run tests**

Run: `python3 -m pytest tests/test_search_service.py -q`

Expected: PASS.

## Task 5: Search API Route And Error Handling

**Files:**
- Modify: `app/api/search.py`
- Test: `tests/test_search_api.py`

**Interfaces:**
- Produces: `POST /api/patent/search`

- [ ] **Step 1: Add API tests using dependency override**

Append to `tests/test_search_api.py`:

```python
from app.api.search import get_search_service
from app.main import app


class FakeSearchService:
    def search(self, request):
        return {"total": 0, "page": request.page, "page_size": request.page_size, "records": []}


def test_search_endpoint_returns_vendor_like_shape(client):
    app.dependency_overrides[get_search_service] = lambda: FakeSearchService()
    try:
        response = client().post("/api/patent/search", json={"q": "阀门"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"total": 0, "page": 1, "page_size": 50, "records": []}
```

- [ ] **Step 2: Run failing API test**

Run: `python3 -m pytest tests/test_search_api.py -q`

Expected: FAIL because route is not implemented.

- [ ] **Step 3: Implement route**

```python
from fastapi import APIRouter, Depends, HTTPException

from app.core.security import require_api_key
from app.query.dsl_builder import UnsupportedQuerySyntax
from app.schemas.search import SearchRequest
from app.services.search_service import SearchService


router = APIRouter(prefix="/api/patent", tags=["patent-search"])


def get_search_service() -> SearchService:
    return SearchService()


@router.post("/search", dependencies=[Depends(require_api_key)])
def search_patents(
    request: SearchRequest,
    service: SearchService = Depends(get_search_service),
):
    try:
        return service.search(request)
    except UnsupportedQuerySyntax as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "code": 40001,
                "message": str(exc),
                "data": None,
            },
        )
```

- [ ] **Step 4: Run API tests**

Run: `python3 -m pytest tests/test_search_api.py -q`

Expected: PASS.

## Task 6: Live Smoke Script

**Files:**
- Create: `scripts/smoke_search.py`

**Interfaces:**
- Produces: `python3 scripts/smoke_search.py http://127.0.0.1:8000 API_TOKEN`

- [ ] **Step 1: Implement smoke script**

```python
import sys

import httpx


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    api_token = sys.argv[2] if len(sys.argv) > 2 else ""
    headers = {"X-API-Key": api_token} if api_token else {}
    payload = {"q": "阀门", "ds": "cn", "sort": "relation", "page": 1, "page_size": 1, "highlight": 0}

    response = httpx.post(f"{base_url.rstrip('/')}/api/patent/search", json=payload, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()
    if "total" not in data or "records" not in data:
        raise RuntimeError(f"unexpected search payload: {data}")
    print(f"search ok total={data['total']} records={len(data['records'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run full tests**

Run: `python3 -m pytest -q`

Expected: PASS.

- [ ] **Step 3: Run local smoke when credentials are available**

Run service:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Run smoke:

```bash
python3 scripts/smoke_search.py http://127.0.0.1:8000 "$API_TOKEN"
```

Expected: `search ok total=<number> records=<0 or 1>`.

## Stage 5 Acceptance Checklist

- [ ] `POST /api/patent/search` exists.
- [ ] Unit tests pass without live OpenSearch.
- [ ] Live smoke test can query real `patent_index` when `.env` has credentials.
- [ ] Response contains `total`, `page`, `page_size`, `records`.
- [ ] Records include the Stage 5 mapped fields from `docs/field_mapping.md`.
- [ ] Pagination uses `from=(page-1)*page_size` and `size=page_size`.
- [ ] `sort=!applicationDate` sorts by `ApplicationDate desc`.
- [ ] `ds=cn` filters to `Country=CN`; `ds=all` does not add country filter.
- [ ] Unsupported Stage 6 syntax returns a clear 400 error.
- [ ] No detail or citation business logic is implemented.

## Self-Review

Spec coverage:

- `POST /api/patent/search`: Task 5.
- Basic OpenSearch search: Task 4.
- Plain keyword, `title`, `ab`, `ipc`, `ad`: Task 2.
- Pagination and sort: Task 2.
- Field mapping: Task 3.
- Smoke test: Task 6.

Placeholder scan:

- No placeholder code is included.
- Live smoke depends on credentials by design and is opt-in.

Type consistency:

- `SearchRequest` is created in Task 1 and consumed by Tasks 2, 4, and 5.
- `build_search_dsl()` returns a dict consumed by `OpenSearchRepository.search()`.
- `map_search_response()` returns the vendor-like response consumed by the API route.
