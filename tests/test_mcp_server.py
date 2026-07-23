import json

import anyio
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from mcp_server.server import BearerTokenAuthApp, build_server, parse_args


class FakePatentApiClient:
    def patent_search(self, **kwargs):
        return {
            "total": 1,
            "page": kwargs["page"],
            "page_size": kwargs["page_size"],
            "total_pages": 1,
            "next_page": None,
            "took_ms": 3,
            "patents": [{"id": "cn-1"}],
        }

    def patent_get_detail(self, patent_id, include_description=False):
        return {"id": patent_id, "patent_id": patent_id, "claims": "权利要求"}

    def patent_get_citations(self, patent_id):
        return {"patent_id": patent_id, "cited_by": [], "patent_references": [], "non_patent_references": []}

    def patent_get_legal_history(self, patent_id):
        return {"patent_id": patent_id, "transaction_count": 0, "transactions": []}


def test_mcp_server_lists_patent_tools():
    async def run():
        server = build_server(client=FakePatentApiClient())
        tools = await server.list_tools()
        names = {tool.name for tool in tools}

        assert names == {
            "patent_search",
            "patent_get_detail",
            "patent_get_citations",
            "patent_get_legal_history",
        }

    anyio.run(run)


def test_mcp_server_calls_patent_search_tool():
    async def run():
        server = build_server(client=FakePatentApiClient())
        result = await server.call_tool("patent_search", {"q": "阀门", "page": 1, "page_size": 2})
        payload = json.loads(result[0].text)

        assert payload["patents"][0]["id"] == "cn-1"
        assert payload["page_size"] == 2
        assert "records" not in payload

    anyio.run(run)


def test_mcp_server_calls_detail_citations_and_legal_history_tools():
    async def run():
        server = build_server(client=FakePatentApiClient())
        detail = json.loads((await server.call_tool("patent_get_detail", {"patent_id": "cn-1"}))[0].text)
        citations = json.loads((await server.call_tool("patent_get_citations", {"patent_id": "cn-1"}))[0].text)
        legal_history = json.loads(
            (await server.call_tool("patent_get_legal_history", {"patent_id": "cn-1"}))[0].text
        )

        assert detail["claims"] == "权利要求"
        assert citations["patent_references"] == []
        assert legal_history["transactions"] == []

    anyio.run(run)


def test_parse_args_defaults_to_stdio():
    args = parse_args([])

    assert args.transport == "stdio"
    assert args.host == "0.0.0.0"
    assert args.port == 9000


def test_parse_args_accepts_http_host_and_port():
    args = parse_args(["--transport", "http", "--host", "127.0.0.1", "--port", "9100"])

    assert args.transport == "http"
    assert args.host == "127.0.0.1"
    assert args.port == 9100


def test_bearer_token_auth_rejects_missing_and_invalid_tokens():
    async def ok(_request):
        return JSONResponse({"ok": True})

    app = BearerTokenAuthApp(
        Starlette(routes=[Route("/mcp", ok, methods=["GET", "POST"])]),
        access_token="secret",
    )
    client = TestClient(app)

    missing = client.post("/mcp")
    invalid = client.post("/mcp", headers={"Authorization": "Bearer wrong"})

    assert missing.status_code == 401
    assert missing.json() == {"error": "unauthorized", "code": 40101}
    assert invalid.status_code == 401
    assert invalid.json() == {"error": "unauthorized", "code": 40101}


def test_bearer_token_auth_allows_valid_token():
    async def ok(_request):
        return JSONResponse({"ok": True})

    app = BearerTokenAuthApp(
        Starlette(routes=[Route("/mcp", ok, methods=["GET", "POST"])]),
        access_token="secret",
    )
    client = TestClient(app)

    response = client.post("/mcp", headers={"Authorization": "Bearer secret"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}
