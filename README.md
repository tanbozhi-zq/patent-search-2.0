# patent-search-service

Self-hosted patent search backend service based on FastAPI and OpenSearch.

## Current State

The Stage 12 FastAPI + remote HTTP MCP delivery is deployed. The next active
workstream is adapting the service to the new OpenSearch v2 index while keeping
the existing production search path recoverable.

Implemented and deployed:

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
- `GET /api/patent/legal-history/{patent_id}`
- flat error responses for validation, business, auth, and OpenSearch failures
- search record snake_case compatibility aliases
- SaaS PatentHub tool adapter for self-hosted search/detail/citations/legal-history
- MCP server under `mcp_server/`, with stdio and remote HTTP transports
- HTTP MCP bearer-token authentication and smoke script
- systemd templates for the FastAPI and MCP services

Current runtime shape:

- FastAPI patent search service under `app/`
- Remote HTTP MCP Server under `mcp_server/`
- External workspaces connect to the public MCP URL through `type: "http"`

The local DeerFlow Tool plugin path is not part of the deployable scope.

### OpenSearch v2 Adaptation

Two physical indexes currently exist:

- `patent_index`: the current API/MCP read target configured through
  `OPENSEARCH_INDEX`.
- `patent_index_v2_20260716`: the new mapping target for the ongoing data
  migration and ingestion transition.

Creating or reindexing a new OpenSearch index does not mirror subsequent
writes from the old one. Before moving the read path to v2, the project must:

1. Confirm v2 has the required historical data and all incremental updates.
2. Update query compatibility for the v2 mapping, especially `IPCList`, which
   is a direct `keyword` field in v2 rather than `IPCList.keyword`.
3. Set an appropriate serving posture for v2, including refresh and replica
   settings, then verify fixed search, detail, citation, and legal-history
   cases.
4. Switch the read target atomically through an OpenSearch read alias; retain
   the old index for rollback.

The current code reads exactly the index named by `OPENSEARCH_INDEX`; it does
not automatically select the newest physical index.

Project boundaries:

- `patent_harness_base_副本/` is a local read-only SaaS contract reference.
- `app/` is the core FastAPI service and must not depend on MCP Server packaging.
- MCP Server must call the self-hosted HTTP API instead of querying OpenSearch directly.
- OpenSearch mapping changes are delivered through a new physical index and a
  controlled cutover, never by changing an existing field's analyzer or type
  in place.

## Documentation

Tracked, project-level documentation:

- `PROJECT_OVERVIEW.md`: 功能、技术组成、架构边界、当前数据演进与版本发布规则。
- `README.md`: current architecture, runtime boundary, and active migration
  status.
- `docs/README.md`: document-versioning boundary.
- `docs/ops/deployment_runbook.md` and `docs/ops/deploy_env_check.md`:
  deployment procedures and prerequisites.
- `mcp_server/README.md`: MCP tools, transports, and authentication.

Historical process and duplicate delivery documents were intentionally removed.
The tracked documents listed in docs/README.md are the only engineering source
of truth. External delivery records, meeting notes, and manual test evidence
are local-only archives under `local/`, not part of the code repository.

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
make check
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Health Check

```bash
curl http://127.0.0.1:8000/health
python3 scripts/smoke_health.py http://127.0.0.1:8000
```

See docs/development.md for the required local check, branch, release, and
index-cutover workflow.

## Production Deployment Shape

The deployed service uses:

- `work` Linux user
- Python venv
- FastAPI/Uvicorn
- systemd
- FastAPI/Uvicorn on port `8000`
- HTTP MCP on port `9000` at `/mcp`
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
python3 scripts/smoke_mcp_server.py http://127.0.0.1:8000 "$API_TOKEN"
```

## MCP Server

Stage 12.4 provides `mcp_server/server.py` for MCP clients. It exposes `patent_search`, `patent_get_detail`, `patent_get_citations`, and `patent_get_legal_history`, and calls the self-hosted HTTP API instead of OpenSearch.

```bash
python3 mcp_server/server.py --transport stdio
```
