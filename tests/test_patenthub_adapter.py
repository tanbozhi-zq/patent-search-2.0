import json

import httpx

from app.integrations.patenthub_adapter import PatentHubAdapterConfig, PatentHubToolAdapter


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_self_hosted_search_maps_records_to_patents_and_caps_page_size():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.path == "/api/patent/search"
        payload = json.loads(request.content.decode("utf-8"))
        assert payload["page_size"] == 50
        assert request.headers["X-API-Key"] == "token"
        return httpx.Response(
            200,
            json={
                "total": 1,
                "page": 1,
                "page_size": 50,
                "records": [
                    {
                        "id": "cn-1",
                        "title": "标题",
                        "summary": "摘要",
                        "application_number": "CN1",
                        "document_number": "CN1A",
                        "application_date": "2020-01-01",
                        "document_date": "2020-02-01",
                        "legal_status": "授权",
                        "main_ipc": "H02M",
                    }
                ],
            },
        )

    adapter = PatentHubToolAdapter(
        config=PatentHubAdapterConfig(
            self_hosted_base_url="http://self-hosted",
            self_hosted_api_token="token",
            page_size_limit=50,
        ),
        client=_client(handler),
    )

    result = json.loads(adapter.patent_search("阀门", page_size=100))

    assert len(requests) == 1
    assert result["total"] == 1
    assert result["patents"][0]["id"] == "cn-1"
    assert result["patents"][0]["summary"] == "摘要"
    assert result["patents"][0]["application_number"] == "CN1"
    assert "records" not in result


def test_self_hosted_detail_and_citations_return_tool_json():
    def handler(request):
        if request.url.path == "/api/patent/detail/cn-1":
            assert request.url.params.get("include_description") == "true"
            return httpx.Response(
                200,
                json={
                    "id": "cn-1",
                    "patent_id": "cn-1",
                    "application_number": "CN1",
                    "document_number": "CN1A",
                    "legal_status": "授权",
                    "main_ipc": "H02M",
                    "claims": "权利要求",
                    "description": "说明书",
                },
            )
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
        return httpx.Response(404, json={"success": False, "code": 40400, "message": "not found", "data": None})

    adapter = PatentHubToolAdapter(
        config=PatentHubAdapterConfig(self_hosted_base_url="http://self-hosted"),
        client=_client(handler),
    )

    detail = json.loads(adapter.patent_get_detail("cn-1", include_description=True))
    citations = json.loads(adapter.patent_get_citations("cn-1"))

    assert detail["description"] == "说明书"
    assert detail["claims"] == "权利要求"
    assert citations["patent_id"] == "cn-1"
    assert citations["cited_by"] == []


def test_self_hosted_error_converts_to_tool_error():
    def handler(request):
        return httpx.Response(
            400,
            json={
                "success": False,
                "code": 40001,
                "message": "q 查询语法错误",
                "data": None,
            },
        )

    adapter = PatentHubToolAdapter(
        config=PatentHubAdapterConfig(self_hosted_base_url="http://self-hosted"),
        client=_client(handler),
    )

    result = json.loads(adapter.patent_search("ipc:H02M AND AND tscd:(均衡)"))

    assert result == {"error": "q 查询语法错误", "code": 40001}


def test_vendor_fallback_uses_patenthub_endpoint_when_self_hosted_disabled():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "success": True,
                "total": 1,
                "totalPages": 1,
                "nextPage": None,
                "took": 3,
                "patents": [
                    {
                        "id": "vendor-1",
                        "title": "标题",
                        "summary": "摘要",
                        "applicationNumber": "CN1",
                        "documentNumber": "CN1A",
                        "legalStatus": "授权",
                        "mainIpc": "H02M",
                    }
                ],
            },
        )

    adapter = PatentHubToolAdapter(
        config=PatentHubAdapterConfig(
            use_self_hosted=False,
            vendor_base_url="http://vendor",
            vendor_api_token="vendor-token",
            page_size_limit=50,
        ),
        client=_client(handler),
    )

    result = json.loads(adapter.patent_search("阀门", page_size=99))

    assert seen["path"] == "/api/s"
    assert seen["params"]["t"] == "vendor-token"
    assert seen["params"]["ps"] == "50"
    assert result["patents"][0]["id"] == "vendor-1"
    assert result["patents"][0]["application_number"] == "CN1"


def test_vendor_fallback_without_token_returns_tool_error():
    adapter = PatentHubToolAdapter(
        config=PatentHubAdapterConfig(use_self_hosted=False, vendor_base_url="http://vendor"),
        client=_client(lambda request: httpx.Response(500)),
    )

    result = json.loads(adapter.patent_search("阀门"))

    assert result == {"error": "PATENTHUB_API_TOKEN is not configured", "code": 40101}
