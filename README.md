# patent-search-service

Self-hosted patent search backend service based on FastAPI and OpenSearch.

## Stage

Current status: Stage 10 accepted, Stage 11 deployment and delivery preparation.

Implemented so far:

- FastAPI app
- `GET /health`
- `.env` configuration loading
- `X-API-Key` auth dependency
- OpenSearch client construction
- `POST /api/patent/search`
- Boolean `q` parser and OpenSearch DSL builder
- analyzer compatibility mode through `index_analyzer_mode`
- `GET /api/patent/detail/{patent_id}`
- `GET /api/patent/detail/{patent_id}?include_description=true`
- `GET /api/patent/citations/{patent_id}`
- flat error responses for validation, business, auth, and OpenSearch failures
- search record snake_case compatibility aliases
- SaaS PatentHub tool adapter for self-hosted search/detail/citations
- static manual test page under `/test/`
- pytest test suite
- systemd deployment template

Next stage:

- Stage 11 deployment, operations handoff, and delivery documentation.
- Stage 11 starts with deployment design, deploy assignment, smoke validation, rollback, and handoff.

Project boundaries:

- `patent_harness_base_副本/` is a local read-only SaaS contract reference.
- Do not modify OpenSearch mapping or rebuild the index in the current stage.
- Do not enter SaaS integration or gray release before the corresponding stage documents are accepted.

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python3 -m pytest -q
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Health Check

```bash
curl http://127.0.0.1:8000/health
python3 scripts/smoke_health.py http://127.0.0.1:8000
```

## Production Deployment Shape

The first deployment uses:

- `work` Linux user
- Python venv
- FastAPI/Uvicorn
- systemd
- port `8000`
- `.env` for secrets

OpenSearch credentials must not be committed to Git.

## Error Responses

Business interfaces return successful payloads directly. All errors use the flat envelope below, without an outer `detail` wrapper:

```json
{
  "success": false,
  "code": 40002,
  "message": "参数非法：...",
  "data": null
}
```

Current codes:

| Code | Scenario | HTTP |
|---|---|---|
| `40001` | query syntax error | 400 |
| `40002` | invalid general parameter | 400 |
| `40003` | invalid `page` or `page_size` | 400 |
| `40101` | missing or invalid `X-API-Key` | 401 |
| `40401` | patent not found | 404 |
| `50001` | OpenSearch query failure | 502 |
| `50002` | internal service failure | 500 |

Stage 8 compatibility notes: `page_size` is capped at 100; `highlight=1` is accepted for compatibility but does not return highlight fragments; search records keep `records` and include snake_case aliases for `application_number`, `document_number`, `application_date`, `document_date`, `legal_status`, and `main_ipc`.

## SaaS Tool Adapter

Stage 10 provides `app.integrations.patenthub_adapter.PatentHubToolAdapter` for SaaS/Agent integration. It exposes PatentHub-like `patent_search`, `patent_get_detail`, and `patent_get_citations` methods, maps self-hosted `records` to tool-layer `patents`, and converts service errors to `{error, code}`.

Key environment variables:

```bash
export PATENT_SEARCH_BASE_URL=http://127.0.0.1:8000
export PATENT_SEARCH_API_TOKEN="$API_TOKEN"
export PATENT_SEARCH_USE_SELF_HOSTED=true
export PATENT_SEARCH_PAGE_SIZE_LIMIT=50
```

Smoke:

```bash
python3 scripts/smoke_saas_adapter.py http://127.0.0.1:8000 "$API_TOKEN"
```
