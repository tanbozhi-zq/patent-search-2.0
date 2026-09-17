"""验证 MCP HTTP 客户端的关联 ID、响应校验与安全错误转换边界。"""

import json
import logging

import httpx
import pytest

from mcp_server.patent_api_client import PatentApiClient
from mcp_server.settings import McpServerSettings


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _search_response():
    return {
        "total": 1,
        "page": 1,
        "page_size": 2,
        "total_pages": 1,
        "accessible_pages": 1,
        "next_page": None,
        "took_ms": 4,
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
                "score": 3.0,
                "future_record_field": "ignored",
            }
        ],
        "future_response_field": "ignored",
    }


def _citation_response():
    return {
        "patent_id": "cn-1",
        "cited_by": [],
        "patent_references": [],
        "non_patent_references": [],
        "referencesCited": [],
        "referencesCitedRaw": "",
        "referencesCitedText": "",
        "relatedDocuments": [],
    }


def _backend_error(**overrides):
    payload = {
        "success": False,
        "code": 50302,
        "message": "搜索依赖暂时不可用",
        "data": None,
        "request_id": "backend-request-id",
        "retryable": True,
        "future_field": "ignored",
    }
    payload.update(overrides)
    return payload


def test_mcp_patent_search_calls_self_hosted_api_and_preserves_success_shape():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["headers"] = request.headers
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json=_search_response())

    client = PatentApiClient(
        settings=McpServerSettings(base_url="http://api", api_token="token"),
        http_client=_client(handler),
    )

    result = client.patent_search(q="阀门", page_size=2)

    assert seen["path"] == "/api/patent/search"
    assert seen["payload"] == {
        "mode": "boolean",
        "q": "阀门",
        "ds": "cn",
        "page": 1,
        "page_size": 2,
        "sort": "relation",
        "highlight": 0,
    }
    assert seen["headers"]["X-API-Key"] == "token"
    assert len(seen["headers"]["X-Request-ID"]) == 32
    assert result.is_error is False
    assert result.payload["total"] == 1
    assert result.payload["total_pages"] == 1
    assert result.payload["accessible_pages"] == 1
    assert result.payload["patents"][0]["id"] == "cn-1"
    assert result.payload["patents"][0]["main_ipc"] == "H05K5/02"
    assert result.payload["patents"][0]["publication_number"] == "CN1A"
    assert "summary" not in result.payload["patents"][0]
    assert "records" not in result.payload
    assert "request_id" not in result.payload


@pytest.mark.parametrize(
    ("mode", "q", "expected_payload", "expected_profile"),
    [
        (
            "vector",
            None,
            {
                "mode": "vector",
                "semantic_text": "流体控制阀",
                "vector_fields": ["abstract", "main_claim"],
                "top_k": 40,
                "ds": "cn",
                "page": 2,
                "page_size": 10,
                "sort": "relation",
                "highlight": 0,
            },
            "patent-vector-rrf-v1-2",
        ),
        (
            "hybrid",
            "ipc:F16K",
            {
                "mode": "hybrid",
                "q": "ipc:F16K",
                "semantic_text": "流体控制阀",
                "vector_fields": ["abstract"],
                "top_k": 100,
                "ds": "cn",
                "page": 1,
                "page_size": 10,
                "sort": "!documentDate",
                "highlight": 0,
            },
            "patent-hybrid-rrf-v1-1",
        ),
    ],
)
def test_mcp_semantic_search_uses_the_same_http_endpoint_and_preserves_context(
    mode,
    q,
    expected_payload,
    expected_profile,
):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.content)
        response = _search_response()
        response["records"][0]["score"] = None
        response["search_context"] = {
            "mode": mode,
            "vector_fields": expected_payload["vector_fields"],
            "top_k": expected_payload["top_k"],
            "ranking_profile": expected_profile,
            "sort": expected_payload["sort"],
        }
        return httpx.Response(200, json=response)

    client = PatentApiClient(
        settings=McpServerSettings(base_url="http://api"),
        http_client=_client(handler),
    )

    result = client.patent_search(
        q=q,
        mode=mode,
        semantic_text="流体控制阀",
        vector_fields=["abstract", "main_claim"] if mode == "vector" else ["abstract"],
        top_k=40 if mode == "vector" else 100,
        page=2 if mode == "vector" else 1,
        sort="relation" if mode == "vector" else "!documentDate",
    )

    assert seen == {"path": "/api/patent/search", "payload": expected_payload}
    assert result.is_error is False
    assert result.payload["search_context"]["ranking_profile"] == expected_profile
    assert result.payload["patents"][0]["score"] is None


@pytest.mark.parametrize("code", [40002, 50001, 50302, 50401])
def test_mcp_semantic_search_preserves_unified_backend_error_mapping(code, caplog):
    sentinel = "CONFIDENTIAL-SEMANTIC-TEXT"

    def handler(request):
        request_id = request.headers["X-Request-ID"]
        return httpx.Response(
            400 if code == 40002 else 503,
            json=_backend_error(
                code=code,
                message="受控后端错误",
                request_id=request_id,
                retryable=code in {50302, 50401},
            ),
            headers={"X-Request-ID": request_id},
        )

    client = PatentApiClient(
        settings=McpServerSettings(base_url="http://private-backend", api_token="secret-token"),
        http_client=_client(handler),
    )
    caplog.set_level(logging.WARNING, logger="mcp_server.patent_api_client")

    result = client.patent_search(
        mode="vector",
        semantic_text=sentinel,
        vector_fields=["abstract"],
        top_k=10,
    )

    assert result.is_error is True
    assert result.payload["code"] == code
    assert result.payload["message"] == "受控后端错误"
    assert result.payload["retryable"] is (code in {50302, 50401})
    assert sentinel not in caplog.text
    assert "private-backend" not in caplog.text
    assert "secret-token" not in caplog.text


def test_mcp_business_failure_uses_one_id_for_backend_result_and_log(caplog):
    seen = {}

    def handler(request):
        request_id = request.headers["X-Request-ID"]
        seen["request_id"] = request_id
        return httpx.Response(
            503,
            json=_backend_error(request_id=request_id),
            headers={"X-Request-ID": request_id},
        )

    client = PatentApiClient(
        settings=McpServerSettings(base_url="http://api"),
        http_client=_client(handler),
    )
    caplog.set_level(logging.WARNING, logger="mcp_server.patent_api_client")

    result = client.patent_search(q="sensitive query")

    assert result.is_error is True
    assert result.payload["request_id"] == seen["request_id"]
    completion = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "mcp_tool_completed"
    )
    assert completion.request_id == seen["request_id"]
    assert completion.dependency == "patent_search_api"
    assert completion.operation == "patent_search"
    assert completion.outcome == "backend_error"
    assert completion.code == 50302
    assert completion.retry_count == 0
    assert "sensitive query" not in caplog.text


def test_mcp_patent_client_exposes_detail_citations_and_legal_history():
    def handler(request):
        if request.url.path == "/api/patent/detail/cn-1":
            assert request.url.params.get("include_description") == "true"
            return httpx.Response(200, json={"id": "cn-1", "claims": "权利要求"})
        if request.url.path == "/api/patent/citations/cn-1":
            return httpx.Response(200, json=_citation_response())
        if request.url.path == "/api/patent/legal-history/cn-1":
            return httpx.Response(
                200,
                json={"patent_id": "cn-1", "transaction_count": 0, "transactions": []},
            )
        return httpx.Response(404, json=_backend_error(code=40401, retryable=False))

    client = PatentApiClient(
        settings=McpServerSettings(base_url="http://api"),
        http_client=_client(handler),
    )

    detail = client.patent_get_detail("cn-1", include_description=True)
    citations = client.patent_get_citations("cn-1")
    legal_history = client.patent_get_legal_history("cn-1")

    assert detail.is_error is False
    assert detail.payload["claims"] == "权利要求"
    assert "ipc_list" not in detail.payload
    assert "ipcMainList" not in detail.payload
    assert citations.is_error is False
    assert citations.payload["non_patent_references"] == []
    assert legal_history.is_error is False
    assert legal_history.payload["transaction_count"] == 0


@pytest.mark.parametrize("status_code", [200, 503])
def test_mcp_patent_client_preserves_valid_backend_errors(status_code, caplog):
    seen = {}

    def handler(request):
        seen["request_id"] = request.headers["X-Request-ID"]
        return httpx.Response(
            status_code,
            json=_backend_error(request_id=seen["request_id"]),
        )

    client = PatentApiClient(
        settings=McpServerSettings(base_url="http://api"),
        http_client=_client(handler),
    )
    caplog.set_level(logging.WARNING, logger="mcp_server.patent_api_client")

    result = client.patent_search(q="sensitive query must not be logged")

    assert result.is_error is True
    assert result.payload == {
        "error": "搜索依赖暂时不可用",
        "code": 50302,
        "message": "搜索依赖暂时不可用",
        "request_id": seen["request_id"],
        "retryable": True,
    }
    assert f"request_id={seen['request_id']}" in caplog.text
    assert "sensitive query" not in caplog.text
    assert "future_field" not in result.payload


def test_mcp_rejects_unbound_error_id_when_response_header_is_missing(caplog):
    seen = {}

    def handler(request):
        seen["outbound_request_id"] = request.headers["X-Request-ID"]
        return httpx.Response(
            503,
            json=_backend_error(request_id="different-valid-id"),
        )

    client = PatentApiClient(
        settings=McpServerSettings(base_url="http://api"),
        http_client=_client(handler),
    )
    caplog.set_level(logging.WARNING, logger="mcp_server.patent_api_client")

    result = client.patent_search(q="sensitive query")

    assert result.is_error is True
    assert result.payload["code"] == 50001
    assert result.payload["request_id"] == seen["outbound_request_id"]
    assert result.payload["request_id"] != "different-valid-id"
    completion = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "mcp_tool_completed"
    )
    assert completion.request_id == seen["outbound_request_id"]
    assert "different-valid-id" not in caplog.text


@pytest.mark.parametrize(
    ("exception_type", "expected_code", "expected_retryable"),
    [
        (httpx.ConnectError, 50302, True),
        (httpx.ReadTimeout, 50401, True),
        (httpx.ConnectTimeout, 50401, True),
        (httpx.RemoteProtocolError, 50001, False),
    ],
)
def test_mcp_patent_client_maps_transport_failures_without_leaking_details(
    exception_type,
    expected_code,
    expected_retryable,
    caplog,
):
    def handler(request):
        raise exception_type("secret backend URL and token", request=request)

    client = PatentApiClient(
        settings=McpServerSettings(base_url="http://private-backend"),
        http_client=_client(handler),
    )
    caplog.set_level(logging.WARNING, logger="mcp_server.patent_api_client")

    result = client.patent_search(q="secret query")

    assert result.is_error is True
    assert result.payload["code"] == expected_code
    assert result.payload["retryable"] is expected_retryable
    assert len(result.payload["request_id"]) == 32
    assert "private-backend" not in str(result.payload)
    assert "token" not in str(result.payload)
    assert "secret query" not in caplog.text
    assert "token" not in caplog.text


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(502, text="internal URL and credential"),
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.Response(400, json=_backend_error(request_id=None)),
        httpx.Response(400, json=_backend_error(request_id="safe\nforged")),
        httpx.Response(400, json=_backend_error(code="50302")),
        httpx.Response(
            503,
            json=_backend_error(request_id="body-request-id"),
            headers={"X-Request-ID": "different-header-id"},
        ),
        httpx.Response(200, json={"total": 1}),
        httpx.Response(
            200,
            json={
                **_search_response(),
                "code": 40001,
                "message": "查询语法错误",
                "request_id": "backend-request-id",
                "retryable": False,
            },
        ),
    ],
)
def test_mcp_patent_client_maps_malformed_responses_to_safe_50001(response):
    client = PatentApiClient(
        settings=McpServerSettings(base_url="http://private-backend"),
        http_client=_client(lambda request: response),
    )

    result = client.patent_search(q="secret query")

    assert result.is_error is True
    assert result.payload["code"] == 50001
    assert result.payload["message"] == "搜索依赖请求失败"
    assert result.payload["retryable"] is False
    assert len(result.payload["request_id"]) == 32
    assert "private-backend" not in str(result.payload)
    assert "credential" not in str(result.payload)


def test_mcp_patent_client_maps_invalid_backend_url_to_safe_50001():
    client = PatentApiClient(
        settings=McpServerSettings(base_url="http://backend:invalid-port"),
    )

    result = client.patent_search(q="query")

    assert result.is_error is True
    assert result.payload["code"] == 50001
    assert result.payload["message"] == "搜索依赖请求失败"
    assert result.payload["retryable"] is False
    assert len(result.payload["request_id"]) == 32


@pytest.mark.parametrize(
    ("path", "payload", "method_name", "arguments"),
    [
        ("/api/patent/detail/cn-1", {}, "patent_get_detail", ("cn-1",)),
        (
            "/api/patent/citations/cn-1",
            {"patent_id": "cn-1"},
            "patent_get_citations",
            ("cn-1",),
        ),
        (
            "/api/patent/legal-history/cn-1",
            {"patent_id": "cn-1", "transaction_count": 0},
            "patent_get_legal_history",
            ("cn-1",),
        ),
    ],
)
def test_mcp_patent_client_rejects_missing_required_success_fields(
    path,
    payload,
    method_name,
    arguments,
):
    def handler(request):
        assert request.url.path == path
        return httpx.Response(200, json=payload)

    client = PatentApiClient(
        settings=McpServerSettings(base_url="http://api"),
        http_client=_client(handler),
    )

    result = getattr(client, method_name)(*arguments)

    assert result.is_error is True
    assert result.payload["code"] == 50001
    assert result.payload["retryable"] is False


def test_mcp_patent_client_maps_unexpected_program_error_to_safe_50002(monkeypatch, caplog):
    client = PatentApiClient(
        settings=McpServerSettings(base_url="http://api"),
        http_client=_client(lambda request: httpx.Response(200, json=_search_response())),
    )

    def fail(*args, **kwargs):
        raise RuntimeError("secret implementation detail")

    monkeypatch.setattr(client._adapter, "patent_search", fail)
    caplog.set_level(logging.ERROR, logger="mcp_server.patent_api_client")

    result = client.patent_search(q="secret query")

    assert result.is_error is True
    assert result.payload["code"] == 50002
    assert result.payload["message"] == "服务内部异常"
    assert result.payload["retryable"] is False
    assert result.payload["request_id"] in caplog.text
    assert "RuntimeError" in caplog.text
    assert "implementation detail" not in caplog.text
    assert "secret query" not in caplog.text
