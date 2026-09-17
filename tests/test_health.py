"""验证健康接口、OpenAPI 元数据和框架失败响应的公共契约。"""

from app.core.exceptions import ERROR_REGISTRY, ErrorCode
from app.core.security import require_api_key, require_console_access
from app.main import app


def test_health_returns_service_status(client):
    response = client().get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "code": 0,
        "message": "ok",
        "data": {
            "status": "healthy",
            "service": "patent-search-service",
        },
    }
    assert len(response.headers["X-Request-ID"]) == 32


def test_openapi_uses_the_service_version(client):
    response = client().get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["version"] == "0.11.1"


def test_openapi_declares_shared_error_contract_and_success_models(client):
    schema = client().get("/openapi.json").json()

    assert schema["components"]["securitySchemes"]["ApiKeyAuth"] == {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
    }
    assert schema["components"]["securitySchemes"]["ConsoleBasicAuth"] == {
        "type": "http",
        "scheme": "basic",
    }
    assert set(schema["components"]["schemas"]["ErrorCode"]["enum"]) == {
        int(code) for code in ErrorCode
    }

    business_operations = {
        "/health": ("get", False, "HealthResponse"),
        "/api/patent/search": ("post", True, "SearchResponse"),
        "/api/patent/detail/{patent_id}": ("get", True, "PatentDetailResponse"),
        "/api/patent/citations/{patent_id}": ("get", True, "CitationResponse"),
        "/api/patent/legal-history/{patent_id}": ("get", True, "LegalHistoryResponse"),
        "/console-api/search": ("post", True, "SearchResponse"),
        "/console-api/test/target-rank": ("post", True, "TargetRankResponse"),
        "/console-api/detail/{patent_id}": ("get", True, "PatentDetailResponse"),
        "/console-api/citations/{patent_id}": ("get", True, "CitationResponse"),
        "/console-api/legal-history/{patent_id}": ("get", True, "LegalHistoryResponse"),
    }
    for path, (method, requires_api_key, response_model) in business_operations.items():
        operation = schema["paths"][path][method]
        responses = operation["responses"]
        assert "200" in responses
        assert "application/json" in responses["200"]["content"]
        assert responses["200"]["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{response_model}"
        }
        assert "X-Request-ID" in responses["200"]["headers"]
        assert "422" not in responses
        for status_code in {str(definition.status_code) for definition in ERROR_REGISTRY.values()}:
            error_response = responses[status_code]
            assert error_response["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }
            assert "X-Request-ID" in error_response["headers"]
        if requires_api_key:
            if path.startswith("/console-api/"):
                assert operation["security"] == [
                    {"ApiKeyAuth": []},
                    {"ConsoleBasicAuth": []},
                ]
            else:
                assert operation["security"] == [{"ApiKeyAuth": []}]
        else:
            assert "security" not in operation

    descriptions = " ".join(
        response["description"]
        for response in schema["paths"]["/api/patent/search"]["post"]["responses"].values()
        if "description" in response
    )
    for code in ERROR_REGISTRY:
        assert str(int(code)) in descriptions
    assert "Retry-After" in schema["paths"]["/api/patent/search"]["post"]["responses"]["429"]["headers"]
    assert "Retry-After" in schema["paths"]["/api/patent/search"]["post"]["responses"]["503"]["headers"]

    components = schema["components"]["schemas"]
    assert {
        "total",
        "page",
        "page_size",
        "total_pages",
        "accessible_pages",
        "next_page",
        "took_ms",
        "records",
    } <= set(
        components["SearchResponse"]["properties"]
    )
    assert {"id", "publication_number", "title", "ipc_list", "score"} <= set(
        components["PatentSearchRecord"]["properties"]
    )
    assert {"id", "title", "main_ipc", "priority_numbers", "description"} <= set(
        components["PatentDetailResponse"]["properties"]
    )
    assert {"patent_id", "cited_by", "patent_references", "non_patent_references"} <= set(
        components["CitationResponse"]["properties"]
    )
    assert {"id", "application_number", "main_ipc"} <= set(
        components["PatentCitationRecord"]["properties"]
    )
    assert {"patent_id", "transaction_count", "transactions"} <= set(
        components["LegalHistoryResponse"]["properties"]
    )
    assert {"status", "in_results", "rank", "tied_count", "sort_value", "target"} <= set(
        components["TargetRankResponse"]["properties"]
    )


def test_console_and_framework_failures_use_json_error_contract(client):
    app.dependency_overrides[require_api_key] = lambda: None
    app.dependency_overrides[require_console_access] = lambda: None
    try:
        http_client = client()
        cases = [
            (http_client.get("/does-not-exist"), 40400),
            (http_client.get("/console/does-not-exist"), 40400),
            (http_client.post("/console-api/search", json={"q": ""}), 40002),
            (http_client.post("/console-api/search", json={"q": "阀门", "page": 0}), 40003),
            (http_client.get("/api/patent/search"), 40500),
        ]
    finally:
        app.dependency_overrides.clear()
    for response, expected_code in cases:
        body = response.json()
        assert response.headers["content-type"].startswith("application/json")
        assert body["code"] == expected_code
        assert body["request_id"] == response.headers["X-Request-ID"]
