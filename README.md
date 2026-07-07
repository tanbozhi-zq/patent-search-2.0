# patent-search-service

Self-hosted patent search backend service based on FastAPI and OpenSearch.

## Stage

Current status: Stage 12.3 DeerFlow / Flow integration passed; Stage 12.4 MCP Server development may continue separately.

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
- SaaS PatentHub tool adapter for self-hosted search/detail/citations/legal-history
- Stage 12.2 DeerFlow Tool wrappers under `deerflow_tool/`
- legacy static inspection page under `/test/`
- pytest test suite
- systemd deployment template

Next stage:

- Stage 12.4 continues with MCP Server packaging on an isolated development branch.
- Stage 12 no longer uses a separate test environment, tester assignment, test acceptance sheet, or test report; quality gates are developer self-check, project-control review, real integration records, and delivery-doc review.

Project boundaries:

- `patent_harness_base_副本/` is a local read-only SaaS contract reference.
- `app/` is the core FastAPI service and must not depend on DeerFlow Tool or MCP Server packaging.
- DeerFlow Tool and MCP Server must call the self-hosted HTTP API instead of querying OpenSearch directly.
- Do not modify OpenSearch mapping or rebuild the index in the current stage.

## Documentation

Current documentation index:

```text
docs/README.md
```

Key Stage 12 documents:

- `docs/delivery/stage12_deerflow_tool_mcp_work_plan.md`
- `docs/delivery/deerflow_tool_integration_guide.md`
- `docs/internal/stage12_deerflow_tool_dev_assignment.md`
- `docs/delivery/api_spec.md`
- `docs/delivery/query_syntax.md`
- `docs/delivery/field_mapping.md`
- `docs/ops/deployment_runbook.md`

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

Stage 10+ provides `app.integrations.patenthub_adapter.PatentHubToolAdapter` for SaaS/Agent integration. It exposes PatentHub-like `patent_search`, `patent_get_detail`, `patent_get_citations`, and `patent_get_legal_history` methods, maps self-hosted `records` to tool-layer `patents`, and converts service errors to `{error, code}`.

Key environment variables:

```bash
export PATENT_SEARCH_BASE_URL=http://127.0.0.1:8000
export PATENT_SEARCH_API_TOKEN="$API_TOKEN"
export PATENT_SEARCH_USE_SELF_HOSTED=true
export PATENT_SEARCH_PAGE_SIZE_LIMIT=50
```

Local self-check:

```bash
python3 scripts/smoke_saas_adapter.py http://127.0.0.1:8000 "$API_TOKEN"
python3 scripts/smoke_deerflow_tool.py http://127.0.0.1:8000 "$API_TOKEN"
```
