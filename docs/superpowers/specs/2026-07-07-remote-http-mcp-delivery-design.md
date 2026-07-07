# Remote HTTP MCP Delivery Design

Date: 2026-07-07

Status: Draft for project owner review

## 1. Role And Objective

This stage is owned from the project-control and backend delivery perspective. The goal is to converge the current self-hosted patent search service from a validated stdio MCP shape into a remotely callable HTTP MCP service for the company's real DeerFlow / workspace environment.

The delivery target is:

```text
Company DeerFlow / workspace
    -> HTTP MCP over public network
    -> patent-mcp.service
    -> local HTTP call
    -> patent-api.service
    -> OpenSearch patent index
```

The first HTTP MCP version prioritizes stable integration, clear operational boundaries, and basic public-network protection. It does not expand the patent-search product scope.

## 2. Confirmed Decisions

1. `app/` remains the core FastAPI patent search service.
2. `mcp_server/` is responsible only for MCP protocol wrapping.
3. The MCP Server must call the FastAPI service through HTTP and must not access OpenSearch directly.
4. `patent_harness_base_副本/` remains a local reference project and is not a deployment target.
5. The current delivery mainline is FastAPI patent search service plus remote HTTP MCP Server.
6. The local DeerFlow Tool plugin path is no longer part of the current delivery scope.
7. The first HTTP MCP version uses static Header Token authentication. OAuth is a later enhancement, not a blocker for this stage.
8. Real tokens, OpenSearch passwords, server passwords, and production `.env` files must not be committed.

## 3. Scope

### 3.1 In Scope

1. Remove the unused DeerFlow Tool delivery path from the active project tree.
2. Update documentation so the delivery mainline is no longer described as "Tool first, MCP later".
3. Keep the existing stdio MCP mode for local verification.
4. Add HTTP MCP mode under `mcp_server/`.
5. Expose the HTTP MCP endpoint at `/mcp`.
6. Require `Authorization: Bearer <token>` for public HTTP MCP access.
7. Add `deployment/patent-mcp.service`.
8. Add an HTTP MCP smoke script.
9. Update `.env.example` and delivery documentation with non-secret environment variable names and examples.
10. Preserve the four current MCP tools:
    - `patent_search`
    - `patent_get_detail`
    - `patent_get_citations`
    - `patent_get_legal_history`

### 3.2 Out Of Scope

1. FTO service.
2. Asynchronous `task_id` workflows.
3. Novelty-search report generation.
4. Enterprise portrait tools.
5. New MCP prompts.
6. Direct OpenSearch access from MCP Server.
7. OpenSearch mapping changes.
8. Index rebuilds.
9. Production OAuth implementation.
10. Continued maintenance of the local DeerFlow Tool plugin path.

## 4. Recommended Approach

The recommended approach is the minimal remote-HTTP delivery path:

1. Keep the current MCP tool registration and `PatentApiClient` behavior.
2. Add a CLI layer to `mcp_server/server.py`.
3. Accept `--transport stdio` and `--transport http` as project-level startup options.
4. Internally map project-level `http` to the MCP SDK's `streamable-http` transport.
5. Configure FastMCP with `host`, `port`, and `streamable_http_path="/mcp"`.
6. Add HTTP authentication at the MCP HTTP service layer.

This approach keeps the task focused on remote MCP service delivery. It avoids expanding the stage into an adapter-layer refactor. The current `mcp_server/patent_api_client.py` may continue to reuse `app.integrations.patenthub_adapter` because the MCP Server is deployed from the same repository on our server and is not imported by DeerFlow as a Python package.

If a later delivery requires `mcp_server/` to become a standalone package imported by an external host, then the dependency on `app.*` should be removed in a separate refactor.

## 5. Target Runtime Shape

### 5.1 FastAPI Service

Service name:

```text
patent-api.service
```

Default internal URL:

```text
http://127.0.0.1:8000
```

Required API surface:

```text
GET  /health
POST /api/patent/search
GET  /api/patent/detail/{patent_id}
GET  /api/patent/citations/{patent_id}
GET  /api/patent/legal-history/{patent_id}
```

The FastAPI service owns OpenSearch configuration and OpenSearch client construction.

### 5.2 MCP Service

Service name:

```text
patent-mcp.service
```

Default bind address:

```text
0.0.0.0:9000
```

Public MCP URL examples:

```text
http://<server-public-ip>:9000/mcp
https://<domain>/mcp
```

The MCP service owns MCP tool registration, MCP transport startup, request authentication, and HTTP calls to the FastAPI service.

## 6. MCP Startup Design

The server should support these commands:

```bash
python mcp_server/server.py --transport stdio
python mcp_server/server.py --transport http --host 0.0.0.0 --port 9000
```

Current dependency verification shows `mcp==1.28.1` supports:

```text
FastMCP.run(transport="stdio")
FastMCP.run(transport="streamable-http")
FastMCP(..., host=..., port=..., streamable_http_path="/mcp")
```

Therefore the project CLI can expose `http` while using SDK transport `streamable-http` internally.

The default transport should remain `stdio` to preserve existing local behavior.

## 7. Authentication Design

### 7.1 Environment Variable

Add:

```bash
MCP_ACCESS_TOKEN=
```

### 7.2 Request Requirement

HTTP MCP requests must include:

```text
Authorization: Bearer <MCP_ACCESS_TOKEN>
```

### 7.3 Behavior

1. If HTTP mode is started and `MCP_ACCESS_TOKEN` is empty, startup should fail with a clear message.
2. Requests without `Authorization` should be rejected.
3. Requests with an invalid bearer token should be rejected.
4. Requests with the correct bearer token should reach MCP handling.
5. stdio mode should not require `MCP_ACCESS_TOKEN`.

### 7.4 Implementation Boundary

The first preference is an in-process ASGI middleware or wrapper around the FastMCP Streamable HTTP app. If the SDK startup path makes that difficult, an acceptable fallback is a small ASGI entrypoint or a documented Nginx/API gateway authorization layer.

Public HTTP MCP must not remain unauthenticated as a long-term state.

## 8. MCP To FastAPI Call Design

The MCP Server continues to call the FastAPI API through HTTP:

```text
POST /api/patent/search
GET  /api/patent/detail/{patent_id}
GET  /api/patent/citations/{patent_id}
GET  /api/patent/legal-history/{patent_id}
```

Environment variables:

```bash
PATENT_SEARCH_BASE_URL=http://127.0.0.1:8000
PATENT_SEARCH_API_TOKEN=
PATENT_SEARCH_TIMEOUT_SECONDS=30
PATENT_SEARCH_PAGE_SIZE_LIMIT=50
```

`PATENT_SEARCH_API_TOKEN` authenticates MCP-to-FastAPI calls. `MCP_ACCESS_TOKEN` authenticates DeerFlow/workspace-to-MCP calls. These tokens have different purposes and should not be documented as interchangeable.

## 9. File Changes Planned

### 9.1 Remove From Active Mainline

Delete:

```text
deerflow_tool/
scripts/smoke_deerflow_tool.py
tests/test_deerflow_tool.py
docs/delivery/deerflow_tool_integration_guide.md
docs/internal/stage12_deerflow_tool_dev_assignment.md
docs/internal/stage12_3_deerflow_integration_plan.md
```

### 9.2 Update Documentation

Update:

```text
README.md
docs/README.md
docs/delivery/stage12_deerflow_tool_mcp_work_plan.md
docs/internal/stage12_mcp_dev_assignment.md
docs/delivery/mcp_integration_guide.md
mcp_server/README.md
.env.example
```

Documentation should say:

```text
Current delivery mainline is the FastAPI patent search service plus remote HTTP MCP Server.
The local DeerFlow Tool plugin path is not part of the current delivery scope.
```

### 9.3 Add HTTP MCP Delivery Files

Add:

```text
deployment/patent-mcp.service
scripts/smoke_mcp_http.py
```

Modify:

```text
mcp_server/server.py
mcp_server/settings.py
tests/test_mcp_server.py
tests/test_mcp_settings.py
```

Additional tests may be added if the authentication wrapper requires a focused unit test file.

## 10. Systemd Design

Add `deployment/patent-mcp.service` with this runtime shape:

```ini
[Unit]
Description=Patent Search MCP Server
After=network.target patent-search-service.service

[Service]
User=work
Group=work
WorkingDirectory=/opt/patent-search-service
EnvironmentFile=/opt/patent-search-service/.env
ExecStart=/opt/patent-search-service/.venv/bin/python mcp_server/server.py --transport http --host 0.0.0.0 --port 9000
Restart=always
RestartSec=5
StandardOutput=append:/var/log/patent-search-service/patent-mcp.log
StandardError=append:/var/log/patent-search-service/patent-mcp.err.log

[Install]
WantedBy=multi-user.target
```

The existing FastAPI service template should remain the FastAPI deployment reference. A documentation note should recommend binding FastAPI to `127.0.0.1:8000` when only the MCP service needs public access.

## 11. Smoke Design

Add:

```bash
python scripts/smoke_mcp_http.py http://<server-public-ip>:9000/mcp "$MCP_ACCESS_TOKEN"
```

The script should verify:

1. MCP HTTP connection succeeds.
2. `tools/list` includes all four required tools.
3. `patent_search` succeeds.
4. The first `id` or `patent_id` from search can be reused.
5. `patent_get_detail` succeeds.
6. `patent_get_citations` succeeds.
7. `patent_get_legal_history` succeeds.
8. An invalid query expression returns a stable `{error, code}` payload and does not crash the MCP Server.

The script should pass the bearer token as an HTTP header and should not print the token.

## 12. DeerFlow / Workspace Integration Contract

Provide the company integration owner with:

1. MCP URL:

```text
http://<server-public-ip>:9000/mcp
```

2. Authentication:

```text
Authorization: Bearer <provided securely>
```

3. Tool list:

```text
patent_search
patent_get_detail
patent_get_citations
patent_get_legal_history
```

4. Configuration example:

```json
{
  "mcpServers": {
    "patent-search": {
      "enabled": true,
      "type": "http",
      "url": "http://<server-public-ip>:9000/mcp",
      "headers": {
        "Authorization": "Bearer ${PATENT_MCP_TOKEN}"
      },
      "description": "自研专利检索 MCP 服务"
    }
  },
  "skills": {}
}
```

5. Smoke result summary.
6. Known limits:
   - This is not an FTO report service.
   - This does not provide asynchronous `task_id` workflows.
   - This does not provide complex prompt workflows.
   - This provides search, detail, citations, and legal-history tools only.
7. Rollback:
   - Disable or remove `patent-search` from the workspace MCP configuration.
   - Stop `patent-mcp.service`.
   - Leave `patent-api.service` and OpenSearch unchanged.

## 13. Test And Verification Plan

Local or server-side verification should include:

```bash
.venv/bin/python -m pytest -q
python scripts/smoke_health.py http://127.0.0.1:8000
python scripts/smoke_search.py http://127.0.0.1:8000 "$API_TOKEN"
python scripts/smoke_detail_citations.py http://127.0.0.1:8000 "$API_TOKEN"
python scripts/smoke_mcp_server.py http://127.0.0.1:8000 "$API_TOKEN"
python scripts/smoke_mcp_http.py http://<server-public-ip>:9000/mcp "$MCP_ACCESS_TOKEN"
```

HTTP MCP authentication checks:

1. Missing bearer token is rejected.
2. Invalid bearer token is rejected.
3. Correct bearer token allows `tools/list`.

If a real OpenSearch-backed service is unavailable in the local environment, unit tests must still pass and smoke commands requiring live data should be recorded as not run with the reason.

## 14. Commit Plan

Use two implementation commits after this design is approved:

### Commit 1

```text
chore: remove unused DeerFlow tool integration path
```

Contents:

1. Delete `deerflow_tool/`.
2. Delete unused DeerFlow Tool smoke, tests, and docs.
3. Update README/docs to remove the Tool-first delivery mainline.

### Commit 2

```text
feat: add remote HTTP MCP server deployment support
```

Contents:

1. Add HTTP MCP transport startup.
2. Add MCP Header Token authentication.
3. Add `deployment/patent-mcp.service`.
4. Add `scripts/smoke_mcp_http.py`.
5. Update MCP deployment and integration docs.
6. Add or update focused tests.

## 15. Acceptance Criteria

The project can enter company DeerFlow / workspace integration when:

1. The DeerFlow Tool path is removed from the active delivery mainline.
2. FastAPI unit tests pass.
3. FastAPI smoke checks pass in the target environment.
4. MCP stdio smoke passes.
5. MCP HTTP service starts on the target host.
6. Public MCP URL is reachable from the company environment.
7. Missing or invalid token requests are rejected.
8. Correct token requests can list tools.
9. `patent_search` succeeds.
10. `patent_search -> patent_get_detail -> patent_get_citations` succeeds.
11. `patent_get_legal_history` succeeds.
12. Invalid query syntax returns a stable `{error, code}` payload.
13. `.env` is not committed.
14. Real tokens are not committed.
15. `patent_harness_base_副本/` is not modified.
16. Company workspace integration configuration is documented.

## 16. Risks And Mitigations

| Risk | Mitigation |
|---|---|
| MCP SDK HTTP API behavior differs from assumptions | Verify against installed `mcp==1.28.1` before implementation and cover startup/auth in tests |
| Authentication middleware cannot be attached through `FastMCP.run()` | Use `streamable_http_app()` with an ASGI wrapper or document Nginx/API gateway auth as a fallback |
| Public service is exposed without protection | Require `MCP_ACCESS_TOKEN` for HTTP mode startup and document firewall restrictions |
| Cleanup removes useful historical context | Rely on Git history for the removed DeerFlow Tool path and keep delivery docs focused on current mainline |
| Live smoke cannot run locally | Separate unit tests from live smoke and record unavailable live checks explicitly |

## 17. Review Gate

This design should be reviewed before implementation. After approval, the next step is to write an implementation plan and then execute the two-commit delivery sequence.
