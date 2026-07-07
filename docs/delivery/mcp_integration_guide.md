# MCP Integration Guide

## 1. Scope

Stage 12.4 provides a stdio MCP Server in `mcp_server/`.

The MCP Server exposes the same four patent tools as the DeerFlow Tool layer and calls the self-hosted HTTP API. It does not query OpenSearch directly.

Streamable HTTP is reserved for a later deployment pass after the stdio path is validated.

## 2. Files

| Path | Purpose |
|---|---|
| `mcp_server/server.py` | FastMCP stdio server and tool registration |
| `mcp_server/patent_api_client.py` | HTTP API client wrapper |
| `mcp_server/settings.py` | `PATENT_SEARCH_*` environment settings |
| `mcp_server/README.md` | Local usage |
| `mcp_server/examples/stdio_config.example.json` | MCP client config draft |
| `scripts/smoke_mcp_server.py` | Local stdio smoke self-check |

## 3. Runtime

The MCP Python SDK is pinned in `requirements.txt`:

```text
mcp==1.28.1
```

This SDK requires Python `>=3.10`. If a target host still uses Python 3.9, upgrade the MCP runtime before deploying this server.

## 4. Environment Variables

```bash
PATENT_SEARCH_BASE_URL=http://127.0.0.1:8000
PATENT_SEARCH_API_TOKEN=<provided securely>
PATENT_SEARCH_TIMEOUT_SECONDS=30
PATENT_SEARCH_PAGE_SIZE_LIMIT=50
```

Do not commit real API tokens or OpenSearch credentials.

## 5. Tools

| Tool | Parameters | Return shape |
|---|---|---|
| `patent_search` | `q`, `ds`, `page`, `page_size`, `sort`, `highlight` | `total`, `page`, `page_size`, `total_pages`, `next_page`, `took_ms`, `patents` |
| `patent_get_detail` | `patent_id`, `include_description` | Detail object with `id`, `patent_id`, `claims`, optional `description` |
| `patent_get_citations` | `patent_id` | `patent_id`, `cited_by`, `patent_references`, `non_patent_references` |
| `patent_get_legal_history` | `patent_id` | `patent_id`, `transaction_count`, `transactions` |

Errors are returned as:

```json
{
  "error": "q 查询语法错误",
  "code": 40001
}
```

## 6. Start

Install dependencies:

```bash
pip install -r requirements.txt
```

Start the stdio server:

```bash
python3 mcp_server/server.py
```

## 7. MCP Client Config Draft

See `mcp_server/examples/stdio_config.example.json`.

```json
{
  "mcpServers": {
    "patent-search": {
      "command": "python3",
      "args": ["mcp_server/server.py"]
    }
  }
}
```

## 8. Local Smoke

Start the patent search API first:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Run:

```bash
python3 scripts/smoke_mcp_server.py http://127.0.0.1:8000 "$API_TOKEN"
```

The smoke self-check covers:

1. MCP tools list.
2. `patent_search`.
3. `patent_get_detail`.
4. `patent_get_citations`.
5. `patent_get_legal_history`.
6. Query syntax error conversion to `{error, code}`.

## 9. Streamable HTTP Plan

`FastMCP` supports Streamable HTTP, but Stage 12.4 ships stdio first. Streamable HTTP should be enabled in a later deployment pass after port, auth, reverse proxy, and process supervision requirements are confirmed.

## 10. Rollback

Remove the MCP client registration for `patent-search`. No OpenSearch mapping, index, or core API rollback is required for this stage.

## 11. Integration Result

Date: 2026-07-07

Result: Stage 12.4 stdio MCP integration passed.

Project owner confirmed that the MCP path is connected successfully. This guide is the delivery reference for the Stage 12.4 stdio MCP version.

The Streamable HTTP path remains a later deployment item and is not part of this version's release boundary.
