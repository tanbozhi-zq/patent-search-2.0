# Patent Search MCP Server

Stage 12.4 exposes the self-hosted patent search API as MCP tools.

The MCP server calls the HTTP API through `PATENT_SEARCH_BASE_URL`; it does not read OpenSearch settings and does not create an OpenSearch client.

## Tools

| Tool | Purpose |
|---|---|
| `patent_search` | Search patents and return `patents` |
| `patent_get_detail` | Read patent detail and optional description |
| `patent_get_citations` | Read citation summaries |
| `patent_get_legal_history` | Read legal history base structure |

## Environment

```bash
export PATENT_SEARCH_BASE_URL=http://127.0.0.1:8000
export PATENT_SEARCH_API_TOKEN="$API_TOKEN"
export PATENT_SEARCH_TIMEOUT_SECONDS=30
export PATENT_SEARCH_PAGE_SIZE_LIMIT=50
export MCP_ACCESS_TOKEN=<provided securely>
```

## Start With stdio

```bash
python3 mcp_server/server.py --transport stdio
```

## Start With HTTP

```bash
export MCP_ACCESS_TOKEN=<provided securely>
python3 mcp_server/server.py --transport http --host 0.0.0.0 --port 9000
```

The HTTP MCP endpoint is:

```text
http://<server>:9000/mcp
```

HTTP callers must send:

```text
Authorization: Bearer <MCP_ACCESS_TOKEN>
```

## Local Smoke

Start the patent search API first, then run:

```bash
python3 scripts/smoke_mcp_server.py http://127.0.0.1:8000 "$API_TOKEN"
python3 scripts/smoke_mcp_http.py http://127.0.0.1:9000/mcp "$MCP_ACCESS_TOKEN"
```
