import httpx

from mcp_server.patent_api_client import PatentApiClient
from mcp_server.settings import McpServerSettings


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_mcp_patent_search_calls_self_hosted_api_and_maps_patents():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["headers"] = request.headers
        return httpx.Response(
            200,
            json={
                "total": 1,
                "page": 1,
                "page_size": 2,
                "total_pages": 1,
                "next_page": None,
                "took_ms": 4,
                "records": [{"id": "cn-1", "title": "标题", "summary": "摘要"}],
            },
        )

    client = PatentApiClient(
        settings=McpServerSettings(base_url="http://api", api_token="token"),
        http_client=_client(handler),
    )

    result = client.patent_search(q="阀门", page_size=2)

    assert seen["path"] == "/api/patent/search"
    assert seen["headers"]["X-API-Key"] == "token"
    assert result["total"] == 1
    assert result["total_pages"] == 1
    assert result["patents"][0]["id"] == "cn-1"
    assert "records" not in result


def test_mcp_patent_client_exposes_detail_citations_and_legal_history():
    def handler(request):
        if request.url.path == "/api/patent/detail/cn-1":
            assert request.url.params.get("include_description") == "true"
            return httpx.Response(200, json={"id": "cn-1", "patent_id": "cn-1", "claims": "权利要求"})
        if request.url.path == "/api/patent/citations/cn-1":
            return httpx.Response(
                200,
                json={
                    "patent_id": "cn-1",
                    "cited_by": [],
                    "patent_references": [],
                    "non_patent_references": [],
                },
            )
        if request.url.path == "/api/patent/legal-history/cn-1":
            return httpx.Response(200, json={"patent_id": "cn-1", "transaction_count": 0, "transactions": []})
        return httpx.Response(404, json={"success": False, "code": 40401, "message": "not found"})

    client = PatentApiClient(
        settings=McpServerSettings(base_url="http://api"),
        http_client=_client(handler),
    )

    detail = client.patent_get_detail("cn-1", include_description=True)
    citations = client.patent_get_citations("cn-1")
    legal_history = client.patent_get_legal_history("cn-1")

    assert detail["claims"] == "权利要求"
    assert citations["non_patent_references"] == []
    assert legal_history["transaction_count"] == 0


def test_mcp_patent_client_converts_api_errors_to_tool_errors():
    def handler(request):
        return httpx.Response(400, json={"success": False, "code": 40001, "message": "q 查询语法错误"})

    client = PatentApiClient(
        settings=McpServerSettings(base_url="http://api"),
        http_client=_client(handler),
    )

    result = client.patent_search(q="ipc:H02M AND AND tscd:(均衡)")

    assert result == {"error": "q 查询语法错误", "code": 40001}
