import json

import httpx

from app.integrations.patenthub_adapter import PatentHubAdapterConfig
from deerflow_tool import tools


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_deerflow_patent_search_uses_adapter_and_returns_patents(monkeypatch):
    def handler(request):
        assert request.url.path == "/api/patent/search"
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["page_size"] == 2
        return httpx.Response(
            200,
            json={
                "total": 1,
                "page": 1,
                "page_size": 2,
                "total_pages": 1,
                "next_page": None,
                "took_ms": 5,
                "records": [{"id": "cn-1", "title": "标题", "summary": "摘要"}],
            },
        )

    adapter = tools.create_adapter(
        config=PatentHubAdapterConfig(self_hosted_base_url="http://self-hosted", page_size_limit=2),
        client=_client(handler),
    )
    monkeypatch.setattr(tools, "create_adapter", lambda: adapter)

    result = json.loads(tools.patent_search("阀门", page_size=100))

    assert result["page_size"] == 2
    assert result["total_pages"] == 1
    assert result["took_ms"] == 5
    assert result["patents"][0]["id"] == "cn-1"
    assert "records" not in result


def test_deerflow_tools_expose_detail_citations_and_legal_history(monkeypatch):
    def handler(request):
        if request.url.path == "/api/patent/detail/cn-1":
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

    adapter = tools.create_adapter(
        config=PatentHubAdapterConfig(self_hosted_base_url="http://self-hosted"),
        client=_client(handler),
    )
    monkeypatch.setattr(tools, "create_adapter", lambda: adapter)

    detail = json.loads(tools.patent_get_detail("cn-1"))
    citations = json.loads(tools.patent_get_citations("cn-1"))
    legal_history = json.loads(tools.patent_get_legal_history("cn-1"))

    assert detail["claims"] == "权利要求"
    assert citations["non_patent_references"] == []
    assert legal_history["transaction_count"] == 0


def test_deerflow_tool_error_response_is_stable(monkeypatch):
    def handler(request):
        return httpx.Response(400, json={"success": False, "code": 40001, "message": "q 查询语法错误"})

    adapter = tools.create_adapter(
        config=PatentHubAdapterConfig(self_hosted_base_url="http://self-hosted"),
        client=_client(handler),
    )
    monkeypatch.setattr(tools, "create_adapter", lambda: adapter)

    result = json.loads(tools.patent_search("ipc:H02M AND AND tscd:(均衡)"))

    assert result == {"error": "q 查询语法错误", "code": 40001}
