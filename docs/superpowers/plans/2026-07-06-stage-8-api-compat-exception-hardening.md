# Stage 8 API Compatibility and Exception Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote search/detail/citations from "capabilities usable" to "contract-stable before SaaS integration" by unifying error responses into the flat `{success, code, message, data}` envelope (eliminating FastAPI's default 422 array and the outer `detail` wrapper), wrapping OpenSearch failures behind `50001`, and adding snake_case aliases to search records.

**Architecture:** Add a global `HTTPException` handler that unwraps `service_error` payloads into flat bodies, and a `RequestValidationError` handler that converts Pydantic 422s into 400 with `40002`/`40003` codes. Wrap `SearchService.search` repository failures in `OpenSearchQueryError` so the search route can map them to `50001` (mirroring detail/citations). Extend `result_mapper` with snake_case aliases. Update docs.

**Tech Stack:** Python 3, FastAPI, Pydantic, pytest, existing exception classes and `service_error` helper.

## Global Constraints

- Do not modify SaaS source copy under `patent_harness_base_副本/`.
- Do not modify OpenSearch index mapping or rebuild indexes.
- Do not do SaaS联调, gray release, enterprise portrait, or full legal-history endpoint in stage 8.
- Python 3.9-compatible (no PEP 604 `X | Y` in runtime code; PEP 585 `list`/`dict` OK on 3.9.19).
- Error responses MUST be the flat shape `{"success": false, "code": <int>, "message": <str>, "data": null}` — no outer `detail` wrapper, no FastAPI 422 array.
- Parameter-validation code: `40002` for general params; `40003` for `page`/`page_size` validation.
- `page_size` ceiling stays 100.
- `highlight=1` is compatibility-only (parameter accepted, no highlight fragments returned); documented explicitly.
- Business success responses keep returning objects directly (no `success/code/message/data` wrapper).
- Commit after each task.

---

## File Structure

- Create `app/core/error_handlers.py`: global handlers for `HTTPException` and `RequestValidationError`.
- Modify `app/main.py`: register handlers.
- Modify `app/core/exceptions.py`: add `PAGINATION_ERROR_FIELDS` constant (no, keep simple — see Task 1).
- Modify `app/services/search_service.py`: wrap repository failures in `OpenSearchQueryError`.
- Modify `app/api/search.py`: catch `OpenSearchQueryError` → `service_error(502, 50001, ...)`.
- Modify `app/mappings/result_mapper.py`: add snake_case aliases to each record.
- Modify `tests/test_search_api.py`, `tests/test_detail_api.py`, `tests/test_citations_api.py`: assert flat error bodies; add 50001 search test.
- Modify `docs/api_spec.md`, `docs/query_syntax.md`, `README.md`.
- Create `docs/stage8_test_report.md`.

---

### Task 1: Global Error Handlers (Flat Envelope)

**Files:**
- Create: `app/core/error_handlers.py`
- Modify: `app/main.py`
- Create: `tests/test_error_handlers.py`

**Interfaces:**
- Produces: `register_error_handlers(app: FastAPI) -> None`
- Produces internal: `http_exception_handler(exc: HTTPException) -> JSONResponse`
- Produces internal: `validation_exception_handler(request, exc: RequestValidationError) -> JSONResponse`

- [ ] **Step 1: Write failing handler tests**

Create `tests/test_error_handlers.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.error_handlers import register_error_handlers
from app.core.exceptions import service_error


def _app_with_extra_route():
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/raise-service-error")
    def _raise():
        raise service_error(400, 40002, "参数非法：page must be greater than 1")

    @app.get("/raise-plain-http-exception")
    def _raise_plain():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="plain string detail")

    @app.get("/needs-q")
    def _needs_q(q: str):
        return {"q": q}

    return app


def test_service_error_payload_is_returned_flat_without_detail_wrapper():
    client = TestClient(_app_with_extra_route())

    response = client.get("/raise-service-error")

    assert response.status_code == 400
    assert response.json() == {
        "success": False,
        "code": 40002,
        "message": "参数非法：page must be greater than 1",
        "data": None,
    }


def test_plain_http_exception_detail_becomes_flat_envelope():
    client = TestClient(_app_with_extra_route())

    response = client.get("/raise-plain-http-exception")

    assert response.status_code == 404
    assert response.json() == {
        "success": False,
        "code": 40400,
        "message": "plain string detail",
        "data": None,
    }


def test_request_validation_error_converts_to_400_with_40002():
    client = TestClient(_app_with_extra_route())

    response = client.get("/needs-q")

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["code"] == 40002
    assert "q" in body["message"]
    assert body["data"] is None
```

- [ ] **Step 2: Run handler tests and verify failure**

Run: `.venv/bin/python -m pytest tests/test_error_handlers.py -q`
Expected: FAIL because `app.core.error_handlers` does not exist.

- [ ] **Step 3: Implement error handlers**

Create `app/core/error_handlers.py`:

```python
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


_PAGINATION_FIELDS = {"page", "page_size"}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and {"success", "code", "message", "data"} <= detail.keys():
            return JSONResponse(status_code=exc.status_code, content=detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "code": exc.status_code * 100,
                "message": detail if isinstance(detail, str) else "internal error",
                "data": None,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        code = _validation_code(exc)
        message = _validation_message(exc)
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "code": code,
                "message": message,
                "data": None,
            },
        )


def _validation_code(exc: RequestValidationError) -> int:
    for error in exc.errors():
        loc = error.get("loc", ())
        if loc and loc[-1] in _PAGINATION_FIELDS:
            return 40003
    return 40002


def _validation_message(exc: RequestValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        loc = error.get("loc", ())
        field = ".".join(str(part) for part in loc if part not in ("body",))
        msg = error.get("msg", "invalid")
        parts.append(f"{field or '参数'} {msg}".strip())
    return "参数非法：" + "; ".join(parts) if parts else "参数非法"
```

- [ ] **Step 4: Register handlers in app/main.py**

Modify `app/main.py` — add registration after app creation (before router includes is fine; place right after `app = FastAPI(...)`):

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.citations import router as citations_router
from app.api.detail import router as detail_router
from app.api.search import router as search_router
from app.core.error_handlers import register_error_handlers
from app.core.logging import configure_logging


configure_logging()

app = FastAPI(
    title="patent-search-service",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

register_error_handlers(app)

app.include_router(search_router)
app.include_router(detail_router)
app.include_router(citations_router)

app.mount("/test", StaticFiles(directory="frontend", html=True), name="frontend")


@app.get("/health")
def health():
    return {
        "success": True,
        "code": 0,
        "message": "ok",
        "data": {
            "status": "healthy",
            "service": "patent-search-service",
        },
    }
```

- [ ] **Step 5: Run handler tests and full regression**

Run:
```bash
.venv/bin/python -m pytest tests/test_error_handlers.py -q
.venv/bin/python -m pytest -q
```
Expected: handler tests PASS. Full suite: existing detail/citations API tests that asserted `response.json()["detail"]["code"]` will now FAIL because the envelope is flat. Those tests will be updated in Task 4. For THIS task, expect only `tests/test_error_handlers.py` to pass, plus most of the suite — but if some API tests break on the `detail` wrapper removal, that is the expected transitional state; record which tests fail and proceed to Task 4 to fix them.

Actually — to keep each task self-contained, update the affected API tests NOW (append to this task, not a later one):

- [ ] **Step 6: Update existing API tests to assert flat envelope**

In `tests/test_detail_api.py`, replace:
```python
assert response.json()["detail"]["code"] == 40401
```
with:
```python
assert response.json()["code"] == 40401
```
Similarly for `50001` and all other `["detail"]["code"]` / `["detail"]["message"]` assertions in `tests/test_detail_api.py` and `tests/test_citations_api.py` and the 40001 assertions in `tests/test_search_api.py`.

Run:
```bash
.venv/bin/python -m pytest tests/test_detail_api.py tests/test_citations_api.py tests/test_search_api.py -q
```
Expected: PASS (all API tests now assert flat bodies).

Run full suite:
```bash
.venv/bin/python -m pytest -q
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/core/error_handlers.py app/main.py tests/test_error_handlers.py tests/test_detail_api.py tests/test_citations_api.py tests/test_search_api.py
git commit -m "fix: normalize request validation errors"
```

---

### Task 2: Wrap Search OpenSearch Failures

**Files:**
- Modify: `app/services/search_service.py`
- Modify: `app/api/search.py`
- Create: `tests/test_search_service.py` (append) or `tests/test_search_api.py` (append)

**Interfaces:**
- `SearchService.search` raises `OpenSearchQueryError` on repository failure (but re-raises `QuerySyntaxError` from DSL building).
- `search_patents` route catches `OpenSearchQueryError` → `service_error(502, 50001, ...)`.

- [ ] **Step 1: Write failing service test**

Append to `tests/test_search_service.py`:

```python
import pytest

from app.core.exceptions import OpenSearchQueryError
from app.services.search_service import SearchService
from app.schemas.search import SearchRequest


class ExplodingRepository:
    def search(self, body):
        raise RuntimeError("opensearch connection refused")


def test_search_service_wraps_repository_failure():
    service = SearchService(repository=ExplodingRepository())

    with pytest.raises(OpenSearchQueryError):
        service.search(SearchRequest(q="阀门"))
```

- [ ] **Step 2: Write failing API test**

Append to `tests/test_search_api.py`:

```python
from app.core.exceptions import OpenSearchQueryError


class OpenSearchFailingService:
    def search(self, request):
        raise OpenSearchQueryError("OpenSearch 查询异常")


def test_search_api_returns_50001_on_opensearch_failure(client):
    app.dependency_overrides[get_search_service] = lambda: OpenSearchFailingService()
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        response = client().post("/api/patent/search", json={"q": "阀门"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json()["code"] == 50001
    assert "opensearch" in response.json()["message"].lower() or "查询" in response.json()["message"]
    assert "connection refused" not in response.json()["message"]
```

- [ ] **Step 3: Run tests and verify failure**

Run:
```bash
.venv/bin/python -m pytest tests/test_search_service.py tests/test_search_api.py -q
```
Expected: FAIL (service doesn't wrap; route doesn't catch).

- [ ] **Step 4: Wrap repository failures in SearchService**

Modify `app/services/search_service.py`:

```python
from typing import Optional

from app.core.exceptions import OpenSearchQueryError, QuerySyntaxError
from app.mappings.result_mapper import map_search_response
from app.query.dsl_builder import build_search_dsl
from app.repositories.opensearch_repo import OpenSearchRepository
from app.schemas.search import SearchRequest


class SearchService:
    def __init__(self, repository: Optional[OpenSearchRepository] = None):
        self.repository = repository or OpenSearchRepository()

    def search(self, request: SearchRequest) -> dict:
        body = build_search_dsl(request)
        try:
            raw = self.repository.search(body)
        except Exception as exc:
            raise OpenSearchQueryError("OpenSearch 查询异常") from exc
        return map_search_response(raw, page=request.page, page_size=request.page_size)
```

- [ ] **Step 5: Catch OpenSearchQueryError in search route**

Modify `app/api/search.py`:

```python
from fastapi import APIRouter, Depends

from app.core.exceptions import OpenSearchQueryError, QuerySyntaxError, service_error
from app.core.security import require_api_key
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
    except QuerySyntaxError as exc:
        raise service_error(400, 40001, str(exc))
    except OpenSearchQueryError as exc:
        raise service_error(502, 50001, str(exc))
```

- [ ] **Step 6: Run tests and full regression**

Run:
```bash
.venv/bin/python -m pytest tests/test_search_service.py tests/test_search_api.py -q
.venv/bin/python -m pytest -q
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/services/search_service.py app/api/search.py tests/test_search_service.py tests/test_search_api.py
git commit -m "fix: wrap search opensearch failures"
```

---

### Task 3: Search Record snake_case Aliases

**Files:**
- Modify: `app/mappings/result_mapper.py`
- Modify: `tests/test_search_result_mapper.py` (append)

**Interfaces:**
- Each search record gains: `application_number`, `document_number`, `application_date`, `document_date`, `legal_status`, `main_ipc` alongside existing camelCase fields.

- [ ] **Step 1: Write failing mapper test**

Append to `tests/test_search_result_mapper.py`:

```python
def test_record_includes_snake_case_aliases():
    raw = {
        "hits": {
            "total": {"value": 1},
            "hits": [
                {
                    "_score": 1.0,
                    "_source": {
                        "patent_id": "cn-1",
                        "ApplicationNumber": "CN1",
                        "PublicationNumber": "CN1A",
                        "Title": "标题",
                        "Abstract": "摘要",
                        "Applicant": "申请人",
                        "Assignee": "权利人",
                        "IPC": "H02M",
                        "ApplicationDate": "2020-01-01",
                        "PublicationDate": "2020-02-01",
                        "LatestLegalStatus": "授权",
                        "Type": "发明专利",
                    },
                }
            ],
        }
    }

    record = map_search_response(raw, page=1, page_size=50)["records"][0]

    assert record["application_number"] == "CN1"
    assert record["document_number"] == "CN1A"
    assert record["application_date"] == "2020-01-01"
    assert record["document_date"] == "2020-02-01"
    assert record["legal_status"] == "授权"
    assert record["main_ipc"] == "H02M"
```

- [ ] **Step 2: Run test and verify failure**

Run: `.venv/bin/python -m pytest tests/test_search_result_mapper.py -q`
Expected: FAIL (snake_case aliases absent).

- [ ] **Step 3: Add snake_case aliases to result mapper**

In `app/mappings/result_mapper.py`, within `_map_record`, add the six aliases alongside their camelCase counterparts. The existing camelCase keys (`applicationNumber`, `documentNumber`, `applicationDate`, `documentDate`, `legalStatus`, `mainIpc`) are computed already; reuse the local variables. Add after the existing camelCase entries in the returned dict:

```python
        "application_number": application_number,
        "document_number": document_number,
        "application_date": application_date,
        "document_date": document_date,
        "legal_status": legal_status,
        "main_ipc": main_ipc,
```

(Where `application_number`, `document_number`, `application_date`, `document_date` are the existing local string variables in `_map_record`; `legal_status` is the existing local; `main_ipc` is the existing local for `mainIpc`. Ensure these locals are named consistently — if the current code uses different local names, adapt to use the same variables that feed the camelCase keys.)

- [ ] **Step 4: Run mapper and full tests**

Run:
```bash
.venv/bin/python -m pytest tests/test_search_result_mapper.py -q
.venv/bin/python -m pytest -q
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/mappings/result_mapper.py tests/test_search_result_mapper.py
git commit -m "feat: add search record snake_case aliases"
```

---

### Task 4: Documentation and Test Report

**Files:**
- Modify: `docs/api_spec.md`
- Modify: `docs/query_syntax.md`
- Modify: `README.md`
- Create: `docs/stage8_test_report.md`
- Modify: `tests/test_error_handlers.py` (add a 40003 pagination test if not already present)

- [ ] **Step 1: Add pagination 40003 test**

Append to `tests/test_error_handlers.py`:

```python
from pydantic import BaseModel


def test_pagination_validation_error_uses_40003():
    app = FastAPI()
    register_error_handlers(app)

    class FakeRequest(BaseModel):
        q: str
        page: int

    @app.post("/search")
    def _search(request: FakeRequest):
        return {"ok": True}

    client = TestClient(app)
    response = client.post("/search", json={"q": "阀门", "page": 0})

    assert response.status_code == 400
    assert response.json()["code"] == 40003
```

Run: `.venv/bin/python -m pytest tests/test_error_handlers.py -q`
Expected: PASS.

- [ ] **Step 2: Update API spec**

In `docs/api_spec.md`, update the error structure section to state:
1. All error responses are the flat envelope `{"success": false, "code": <int>, "message": <str>, "data": null}` — no outer `detail` wrapper.
2. Parameter-validation code: `40002` (general), `40003` (page/page_size).
3. `highlight=1` is compatibility-only: parameter accepted, no highlight fragments returned in stage 8.
4. Search records include snake_case aliases: `application_number`, `document_number`, `application_date`, `document_date`, `legal_status`, `main_ipc`.

- [ ] **Step 3: Update query_syntax.md and README**

In `docs/query_syntax.md`, add a section noting `highlight=1` 仅兼容接收，不返回高亮片段。In `README.md`, add a short "Error Responses" section documenting the flat envelope and the 40001/40002/40003/40101/40401/50001/50002 codes.

- [ ] **Step 4: Run full suite and smoke**

Run:
```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/smoke_health.py http://127.0.0.1:8000
```
(Start uvicorn in background for the health smoke; kill afterward.)
Expected: pytest PASS; health smoke `health ok`.

- [ ] **Step 5: Write test report**

Create `docs/stage8_test_report.md` with: pytest command + actual pass count; health smoke result; confirmation that 422/default-422 and outer-`detail` wrapper are eliminated; confirmation SaaS source and OpenSearch mapping untouched; explicit list of which codes map to which scenarios; recommendation on entering stage 9.

- [ ] **Step 6: Commit**

```bash
git add docs/api_spec.md docs/query_syntax.md README.md docs/stage8_test_report.md tests/test_error_handlers.py
git commit -m "docs: update stage 8 api compatibility notes"
```

---

## Review Checklist

- Error responses are the flat envelope — no `{"detail": {...}}`, no 422 array.
- Parameter errors: 40002 general, 40003 page/page_size.
- search/detail/citations OpenSearch failures → 502 / 50001, no stack/credentials leaked.
- search records include snake_case aliases.
- highlight=1 documented as compatibility-only.
- Existing success responses unchanged (total/page/page_size/records; detail fields; citations fields).
- SaaS source copy untouched. OpenSearch mapping untouched.
- Full pytest suite passes.