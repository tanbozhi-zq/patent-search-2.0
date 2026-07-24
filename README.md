# patent-search-service

Self-hosted patent search backend service based on FastAPI and OpenSearch.

## Current State

The Stage 12 FastAPI + remote HTTP MCP delivery is deployed. The service code
now uses the OpenSearch v2 query semantics, while the production read path
remains unchanged until data, serving settings, fixed samples, and the read
alias have passed the controlled cutover checks.

Implemented capabilities:

- FastAPI app
- `GET /health`
- `.env` configuration loading
- `X-API-Key` auth dependency
- OpenSearch client construction
- `POST /api/patent/search`
- Boolean `q` parser and OpenSearch DSL builder
- v2-specific unified query semantics for text, entities, and keyword fields
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
2. Set an appropriate serving posture for v2, including refresh and replica
   settings, then verify fixed search, detail, citation, and legal-history
   cases.
3. Switch the read target atomically through an OpenSearch read alias; retain
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
- `docs/api.md`: HTTP API、查询语法、错误码与 MCP 对接契约。
- `docs/development.md` and `docs/ops/`: 开发、发布与索引切换手册。

The tracked documents listed in docs/README.md are the only engineering source
of truth. `local/` is not an interface-document archive.

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

HTTP API、MCP 工具、错误码、查询语法和 smoke 方式统一见 [docs/api.md](docs/api.md)。
