# DeerFlow Patent Tools

Stage 12.2 adds local DeerFlow / Flow tool wrappers for the self-hosted patent search API.

The tools call the HTTP API exposed by `app/`; they do not read OpenSearch configuration and do not create an OpenSearch client.

## Tools

| Tool | Purpose |
|---|---|
| `patent_search` | Search patents and return tool-layer `patents` |
| `patent_get_detail` | Read patent detail and optional description |
| `patent_get_citations` | Read citation and reference summaries |
| `patent_get_legal_history` | Read the basic legal history structure |

Each plain Python function also has a `*_tool` export for DeerFlow / LangChain-style registration when `langchain` is available.

## Environment

```bash
export PATENT_SEARCH_BASE_URL=http://127.0.0.1:8000
export PATENT_SEARCH_API_TOKEN="$API_TOKEN"
export PATENT_SEARCH_USE_SELF_HOSTED=true
export PATENT_SEARCH_PAGE_SIZE_LIMIT=50
export PATENT_SEARCH_TIMEOUT_SECONDS=30
```

## Local Self-Check

Start the API service first, then run:

```bash
python3 scripts/smoke_deerflow_tool.py http://127.0.0.1:8000 "$API_TOKEN"
```

The script prints JSON lines for search, detail, citations, legal history, and error conversion checks.
