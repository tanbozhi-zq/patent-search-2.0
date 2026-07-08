import json
import os
import sys
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEARCH_CASE = {"q": "阀门", "page": 1, "page_size": 1}


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else os.getenv("PATENT_SEARCH_BASE_URL", "http://127.0.0.1:8000")
    api_token = sys.argv[2] if len(sys.argv) > 2 else os.getenv("PATENT_SEARCH_API_TOKEN", "")
    return anyio.run(_run_smoke, base_url, api_token)


async def _run_smoke(base_url: str, api_token: str) -> int:
    env = dict(os.environ)
    env["PATENT_SEARCH_BASE_URL"] = base_url
    env["PATENT_SEARCH_API_TOKEN"] = api_token
    env.setdefault("PATENT_SEARCH_TIMEOUT_SECONDS", "30")
    env.setdefault("PATENT_SEARCH_PAGE_SIZE_LIMIT", "50")

    params = StdioServerParameters(
        command=sys.executable,
        args=["mcp_server/server.py"],
        cwd=PROJECT_ROOT,
        env=env,
    )

    with open(os.devnull, "w") as errlog:
        async with stdio_client(params, errlog=errlog) as (read_stream, write_stream):
            return await _check_tools(read_stream, write_stream)


async def _check_tools(read_stream, write_stream) -> int:
    async with ClientSession(read_stream, write_stream) as session:
        await session.initialize()
        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        required = {
            "patent_search",
            "patent_get_detail",
            "patent_get_citations",
            "patent_get_legal_history",
        }
        tools_ok = required.issubset(names)
        print(json.dumps({"name": "mcp_tools_list", "ok": tools_ok, "tools": sorted(names)}, ensure_ascii=False))
        if not tools_ok:
            return 1

        search = _tool_payload(await session.call_tool("patent_search", SEARCH_CASE))
        patents = search.get("patents", [])
        patent_id = patents[0].get("id") if patents else ""
        search_ok = "error" not in search and bool(patent_id) and "records" not in search
        print(
            json.dumps(
                {
                    "name": "mcp_patent_search",
                    "ok": search_ok,
                    "total": search.get("total"),
                    "patents": len(patents),
                    "metadata": all(field in search for field in ("total_pages", "next_page", "took_ms")),
                },
                ensure_ascii=False,
            )
        )
        if not search_ok:
            return 1

        detail = _tool_payload(
            await session.call_tool(
                "patent_get_detail",
                {"patent_id": patent_id, "include_description": True},
            )
        )
        detail_ok = "error" not in detail and all(field in detail for field in ("id", "patent_id", "claims"))
        print(json.dumps({"name": "mcp_patent_get_detail", "ok": detail_ok}, ensure_ascii=False))
        if not detail_ok:
            return 1

        citations = _tool_payload(await session.call_tool("patent_get_citations", {"patent_id": patent_id}))
        citations_ok = "error" not in citations and all(
            field in citations for field in ("patent_id", "cited_by", "patent_references", "non_patent_references")
        )
        print(json.dumps({"name": "mcp_patent_get_citations", "ok": citations_ok}, ensure_ascii=False))
        if not citations_ok:
            return 1

        legal_history = _tool_payload(await session.call_tool("patent_get_legal_history", {"patent_id": patent_id}))
        legal_ok = "error" not in legal_history and all(
            field in legal_history for field in ("patent_id", "transaction_count", "transactions")
        )
        print(json.dumps({"name": "mcp_patent_get_legal_history", "ok": legal_ok}, ensure_ascii=False))
        if not legal_ok:
            return 1

        error = _tool_payload(
            await session.call_tool(
                "patent_search",
                {"q": "ipc:H02M AND AND tscd:(均衡)", "page": 1, "page_size": 1},
            )
        )
        error_ok = error.get("code") == 40001 and "error" in error
        print(json.dumps({"name": "mcp_error_conversion", "ok": error_ok, "code": error.get("code")}, ensure_ascii=False))
        return 0 if error_ok else 1


def _tool_payload(result) -> dict:
    if not result.content:
        return {"error": "empty MCP tool result", "code": 50002}
    return json.loads(result.content[0].text)


if __name__ == "__main__":
    raise SystemExit(main())
