# MCP Integration Guide

## 1. Scope

The current delivery mainline is a remote HTTP MCP Server in `mcp_server/`, backed by the self-hosted FastAPI patent search service.

The MCP Server exposes four patent tools and calls the FastAPI service through HTTP. It does not query OpenSearch directly, does not read OpenSearch settings, and does not create an OpenSearch client.

The local DeerFlow Tool plugin path is not part of the current delivery scope.

## 2. Runtime

The MCP Python SDK is pinned in `requirements.txt`:

```text
mcp==1.28.1
```

This SDK requires Python `>=3.10`. If a target host still uses Python 3.9, upgrade the MCP runtime before deploying this server.

Project-level HTTP startup uses `--transport http`; internally this maps to the SDK Streamable HTTP transport.

## 3. Environment Variables

```bash
PATENT_SEARCH_BASE_URL=http://127.0.0.1:8000
PATENT_SEARCH_API_TOKEN=<provided securely>
PATENT_SEARCH_TIMEOUT_SECONDS=30
PATENT_SEARCH_PAGE_SIZE_LIMIT=50
MCP_ACCESS_TOKEN=<provided securely>
```

`PATENT_SEARCH_API_TOKEN` is for MCP-to-FastAPI calls. `MCP_ACCESS_TOKEN` is for DeerFlow/workspace-to-MCP calls. Do not document or deploy them as interchangeable values.

Do not commit real API tokens or OpenSearch credentials.

## 4. Start

Local stdio verification:

```bash
python3 mcp_server/server.py --transport stdio
```

Remote HTTP MCP service:

```bash
export MCP_ACCESS_TOKEN=<provided securely>
python3 mcp_server/server.py --transport http --host 0.0.0.0 --port 9000
```

The HTTP MCP endpoint is:

```text
http://<server-public-ip>:9000/mcp
```

HTTP MCP requests must include:

```text
Authorization: Bearer <MCP_ACCESS_TOKEN>
```

## 5. Company Workspace Config Example

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

## 6. Tools

| Tool | Parameters | Return shape |
|---|---|---|
| `patent_search` | `q`, `ds`, `page`, `page_size`, `sort`, `highlight` | `total`, `page`, `page_size`, `total_pages`, `next_page`, `took_ms`, `patents` |
| `patent_get_detail` | `patent_id`, `include_description` | Detail object with `id`, `patent_id`, `claims`, optional `description` |
| `patent_get_citations` | `patent_id` | `patent_id`, `cited_by`, `patent_references`, `non_patent_references` |
| `patent_get_legal_history` | `patent_id` | `patent_id`, `transaction_count`, `transactions` |

Tool errors are returned as:

```json
{
  "error": "q 查询语法错误",
  "code": 40001
}
```

HTTP MCP authentication errors are returned as:

```json
{
  "error": "unauthorized",
  "code": 40101
}
```

## 7. Smoke

Start the FastAPI patent search service first:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Run stdio smoke:

```bash
python3 scripts/smoke_mcp_server.py http://127.0.0.1:8000 "$API_TOKEN"
```

Start HTTP MCP:

```bash
export PATENT_SEARCH_BASE_URL=http://127.0.0.1:8000
export PATENT_SEARCH_API_TOKEN="$API_TOKEN"
export MCP_ACCESS_TOKEN=<provided securely>
python3 mcp_server/server.py --transport http --host 0.0.0.0 --port 9000
```

Run HTTP smoke:

```bash
python3 scripts/smoke_mcp_http.py http://127.0.0.1:9000/mcp "$MCP_ACCESS_TOKEN"
```

HTTP MCP authentication checks:

```bash
curl -i -X POST http://127.0.0.1:9000/mcp
curl -i -X POST http://127.0.0.1:9000/mcp -H "Authorization: Bearer wrong"
```

Both unauthenticated and wrong-token requests should return HTTP 401 with `{error, code}`.

## 8. Deployment

The systemd template is:

```text
deployment/patent-mcp.service
```

Recommended public deployment shape:

1. Bind FastAPI patent API to `127.0.0.1:8000` when only MCP needs public access.
2. Bind MCP HTTP service to `0.0.0.0:9000` or proxy it through Nginx/HTTPS.
3. Keep `MCP_ACCESS_TOKEN` private and rotate it when the workspace configuration changes.
4. Restrict firewall access to known company workspace egress addresses when possible.

## 9. Rollback

1. Disable or remove the workspace MCP server configuration for `patent-search`.
2. Stop the MCP service:

```bash
sudo systemctl stop patent-mcp.service
```

3. Keep the FastAPI patent search service running unless the rollback specifically targets the core API.
4. No OpenSearch mapping, index, or data rollback is required for MCP service rollback.
