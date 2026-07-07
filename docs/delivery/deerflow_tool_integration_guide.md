# DeerFlow Tool Integration Guide

## 1. Scope

Stage 12.2 provides local DeerFlow / Flow tool wrappers in `deerflow_tool/`.

This stage only covers local tool packaging and developer self-check. Real Flow / DeerFlow agent loading and end-to-end analysis belong to Stage 12.3. MCP Server packaging belongs to Stage 12.4.

## 2. Tool Files

| Path | Purpose |
|---|---|
| `deerflow_tool/tools.py` | Tool entrypoints and optional LangChain tool exports |
| `deerflow_tool/README.md` | Local usage notes |
| `deerflow_tool/examples/config.example.yaml` | Flow / DeerFlow registration draft |
| `scripts/smoke_deerflow_tool.py` | Local smoke self-check script |

The tool layer calls the self-hosted HTTP API and does not query OpenSearch directly.

## 3. Tools

| Tool | Parameters | Self-hosted API |
|---|---|---|
| `patent_search` | `q`, `ds`, `page`, `page_size`, `sort`, `highlight` | `POST /api/patent/search` |
| `patent_get_detail` | `patent_id`, `include_description` | `GET /api/patent/detail/{patent_id}` |
| `patent_get_citations` | `patent_id` | `GET /api/patent/citations/{patent_id}` |
| `patent_get_legal_history` | `patent_id` | `GET /api/patent/legal-history/{patent_id}` |

## 4. Environment Variables

```bash
PATENT_SEARCH_BASE_URL=http://127.0.0.1:8000
PATENT_SEARCH_API_TOKEN=<provided securely>
PATENT_SEARCH_USE_SELF_HOSTED=true
PATENT_SEARCH_PAGE_SIZE_LIMIT=50
PATENT_SEARCH_TIMEOUT_SECONDS=30
```

Do not commit real API tokens or OpenSearch credentials.

## 5. Return Shape

`patent_search` returns tool-layer search data:

```json
{
  "total": 123,
  "page": 1,
  "page_size": 50,
  "total_pages": 3,
  "next_page": 2,
  "took_ms": 35,
  "patents": []
}
```

The search result uses `patents`; it does not expose API-layer `records`.

`patent_get_detail` returns detail fields including `id`, `patent_id`, `claims`, and `description` when `include_description=true`.

`patent_get_citations` returns `patent_id`, `cited_by`, `patent_references`, and `non_patent_references`.

`patent_get_legal_history` returns `patent_id`, `transaction_count`, and `transactions`.

Errors are converted to:

```json
{
  "error": "q 查询语法错误",
  "code": 40001
}
```

## 6. Local Self-Check

Start the API:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Run the tool smoke self-check:

```bash
python3 scripts/smoke_deerflow_tool.py http://127.0.0.1:8000 "$API_TOKEN"
```

The self-check covers:

1. Keyword search: `阀门`
2. Text query: `tscd:(阀门 AND 密封)`
3. IPC + text query: `ipc:H02M AND tscd:(均衡 OR 平衡)`
4. Applicant query: `applicant:宁德时代新能源科技股份有限公司`
5. Current assignee query: `currentAssignee:华为技术有限公司`
6. `search -> detail -> citations -> legal_history`
7. Invalid query conversion to `{error, code}`

## 7. Flow / DeerFlow Registration Draft

Use `deerflow_tool/examples/config.example.yaml` as the registration draft:

```yaml
tools:
  - name: patent_search
    group: web
    use: deerflow_tool.tools:patent_search_tool
    base_url: ${PATENT_SEARCH_BASE_URL}
    api_token: ${PATENT_SEARCH_API_TOKEN}
```

Actual agent loading and workflow validation are Stage 12.3 tasks.

## 8. Known Limits

1. `enterprise_patent_portrait` is not exposed in Stage 12.2.
2. Legal history returns the stable base structure provided by the HTTP API; complete transaction enrichment is outside this stage.
3. The self-hosted tool chain does not copy PatentHub's 60-minute session-bound patent id behavior.
4. MCP Server packaging is not part of Stage 12.2.

## 9. Rollback

If the tool layer causes integration issues, remove the `deerflow_tool.tools:*` registration entries and continue using the existing HTTP API directly. No OpenSearch mapping or index rollback is required for this stage.
