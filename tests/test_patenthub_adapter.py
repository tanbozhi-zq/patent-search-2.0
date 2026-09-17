"""验证 PatentHub 兼容适配器在自托管与供应商路径下的统一工具契约。"""

import json

import httpx

from app.integrations.patenthub_adapter import PatentHubAdapterConfig, PatentHubToolAdapter


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_adapter_config_defaults_to_240_second_timeout(monkeypatch):
    monkeypatch.delenv("PATENT_SEARCH_TIMEOUT_SECONDS", raising=False)

    config = PatentHubAdapterConfig.from_env()

    assert config.timeout_seconds == 245


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
                "total_pages": 1,
                "accessible_pages": 1,
                "next_page": None,
                "took_ms": 12,
                "records": [
                    {
                        "id": "cn-1",
                        "application_number": "CN1",
                        "publication_number": "CN1A",
                        "title": "标题",
                        "abstract": "摘要",
                        "applicant": "申请人",
                        "current_assignee": "当前权利人",
                        "inventor": "发明人",
                        "main_ipc": "H05K5/02",
                        "ipc_list": ["H05K5/02"],
                        "main_claim": "首权",
                        "application_date": "2020-01-01",
                        "publication_date": "2020-02-01",
                        "legal_status": "授权",
                        "type": "发明专利",
                        "score": 1.2,
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
    assert result["total_pages"] == 1
    assert result["accessible_pages"] == 1
    assert result["next_page"] is None
    assert result["took_ms"] == 12
    assert result["page_size"] == 50
    assert result["patents"][0] == {
        "id": "cn-1",
        "application_number": "CN1",
        "publication_number": "CN1A",
        "title": "标题",
        "abstract": "摘要",
        "applicant": "申请人",
        "current_assignee": "当前权利人",
        "inventor": "发明人",
        "main_ipc": "H05K5/02",
        "ipc_list": ["H05K5/02"],
        "main_claim": "首权",
        "application_date": "2020-01-01",
        "publication_date": "2020-02-01",
        "legal_status": "授权",
        "type": "发明专利",
        "score": 1.2,
    }
    assert "records" not in result


def test_self_hosted_detail_and_citations_return_tool_json():
    def handler(request):
        if request.url.path == "/api/patent/detail/cn-1":
            assert request.url.params.get("include_description") == "true"
            return httpx.Response(
                200,
                json={
                    "id": "cn-1",
                    "application_number": "CN1",
                    "publication_number": "CN1A",
                    "legal_status": "授权",
                    "main_ipc": "H05K5/02",
                    "ipc_list": ["H05K5/02", "B23P15/00"],
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
    assert detail["main_ipc"] == "H05K5/02"
    assert detail["ipc_list"] == ["H05K5/02", "B23P15/00"]
    assert "mainIpc" not in detail
    assert "ipcMainList" not in detail
    assert citations["patent_id"] == "cn-1"
    assert citations["cited_by"] == []


def test_self_hosted_legal_history_returns_tool_json():
    def handler(request):
        assert request.url.path == "/api/patent/legal-history/cn-1"
        return httpx.Response(
            200,
            json={
                "patent_id": "cn-1",
                "transaction_count": 1,
                "transactions": [{"date": "2024-01-01", "type": "公开"}],
            },
        )

    adapter = PatentHubToolAdapter(
        config=PatentHubAdapterConfig(self_hosted_base_url="http://self-hosted"),
        client=_client(handler),
    )

    result = json.loads(adapter.patent_get_legal_history("cn-1"))

    assert result == {
        "patent_id": "cn-1",
        "transaction_count": 1,
        "transactions": [{"date": "2024-01-01", "type": "公开"}],
    }


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
                        "type": "发明专利",
                        "mainIpc": "H05K 5/02 (2006.01)I",
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
    assert result["patents"][0]["publication_number"] == "CN1A"
    assert result["patents"][0]["abstract"] == "摘要"
    assert result["patents"][0]["main_ipc"] == "H05K5/02"
    assert "document_number" not in result["patents"][0]
    assert "summary" not in result["patents"][0]


def test_vendor_search_keeps_mcp_text_and_assignee_semantics():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "success": True,
                "total": 1,
                "totalPages": 1,
                "nextPage": None,
                "patents": [
                    {
                        "id": "vendor-1",
                        "applicant": ["鲁贝里股份公司"],
                        "inventor": ["发明人甲", "发明人乙"],
                    }
                ],
            },
        )

    adapter = PatentHubToolAdapter(
        config=PatentHubAdapterConfig(
            use_self_hosted=False,
            vendor_base_url="http://vendor",
            vendor_api_token="vendor-token",
        ),
        client=_client(handler),
    )

    record = json.loads(adapter.patent_search("阀门"))["patents"][0]

    assert record["applicant"] == "鲁贝里股份公司"
    assert record["inventor"] == "发明人甲;发明人乙"
    assert record["current_assignee"] == ""


def test_vendor_detail_uses_the_same_snake_case_detail_contract():
    def handler(request):
        if request.url.path == "/api/patent/base":
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "patent": {
                        "id": "vendor-1",
                        "title": "标题",
                        "summary": "摘要",
                        "applicationNumber": "CN1",
                        "documentNumber": "CN1A",
                        "mainIpc": "H05K 5/02 (2006.01)I",
                        "ipcMainList": ["H05K 5/02 (2006.01)I"],
                        "type": "发明专利",
                        "imagePath": "https://vendor.invalid/figure.gif",
                    },
                },
            )
        if request.url.path == "/api/patent/claims":
            return httpx.Response(200, json={"success": True, "patent": {"claims": "完整权利要求书"}})
        return httpx.Response(404, json={"success": False, "code": 40400, "error": "not found"})

    adapter = PatentHubToolAdapter(
        config=PatentHubAdapterConfig(
            use_self_hosted=False,
            vendor_base_url="http://vendor",
            vendor_api_token="vendor-token",
        ),
        client=_client(handler),
    )

    detail = json.loads(adapter.patent_get_detail("vendor-1"))

    assert detail == {
        "id": "vendor-1",
        "application_number": "CN1",
        "publication_number": "CN1A",
        "title": "标题",
        "abstract": "摘要",
        "main_ipc": "H05K5/02",
        "ipc_list": ["H05K5/02"],
        "claims": "完整权利要求书",
        "type": "发明专利",
        "image_path": "https://vendor.invalid/figure.gif",
    }


def test_vendor_fallback_without_token_returns_tool_error():
    adapter = PatentHubToolAdapter(
        config=PatentHubAdapterConfig(use_self_hosted=False, vendor_base_url="http://vendor"),
        client=_client(lambda request: httpx.Response(500)),
    )

    result = json.loads(adapter.patent_search("阀门"))

    assert result == {"error": "PATENTHUB_API_TOKEN is not configured", "code": 40101}
