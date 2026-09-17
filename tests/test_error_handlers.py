"""验证全局错误处理器的状态码、请求关联 ID、日志与敏感信息收口。"""

import logging

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.core.error_handlers import REQUEST_ID_HEADER, register_error_handlers
from app.core.exceptions import ERROR_REGISTRY, ErrorCode, error_definition, service_error


EXPECTED_REGISTRY = {
    40001: (400, False, None),
    40002: (400, False, None),
    40003: (400, False, None),
    40004: (400, False, None),
    40101: (401, False, None),
    40400: (404, False, None),
    40401: (404, False, None),
    40500: (405, False, None),
    40901: (409, False, None),
    41301: (413, False, None),
    42901: (429, True, 60),
    50001: (502, False, None),
    50002: (500, False, None),
    50301: (503, True, 1),
    50302: (503, True, 5),
    50401: (504, True, None),
}


def _app_with_extra_route() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/raise-service-error")
    def _raise():
        raise service_error(ErrorCode.INVALID_REQUEST)

    @app.get("/raise-plain-http-exception")
    def _raise_plain():
        raise HTTPException(status_code=404, detail="host=internal.example password=leaked")

    @app.get("/raise-unexpected")
    def _raise_unexpected():
        raise RuntimeError("database password leaked")

    @app.get("/needs-q")
    def _needs_q(q: str):
        return {"q": q}

    return app


def _assert_error(response, code: ErrorCode) -> None:
    definition = error_definition(code)
    body = response.json()

    assert response.status_code == definition.status_code
    assert body["success"] is False
    assert body["code"] == int(code)
    assert body["message"] == definition.message
    assert body["data"] is None
    assert body["retryable"] is definition.retryable
    assert len(body["request_id"]) == 32
    assert response.headers[REQUEST_ID_HEADER] == body["request_id"]
    if definition.retry_after_seconds is None:
        assert "Retry-After" not in response.headers
    else:
        assert response.headers["Retry-After"] == str(definition.retry_after_seconds)


def test_error_registry_is_the_complete_code_matrix():
    assert {
        int(code): (
            definition.status_code,
            definition.retryable,
            definition.retry_after_seconds,
        )
        for code, definition in ERROR_REGISTRY.items()
    } == EXPECTED_REGISTRY


def test_every_registered_error_has_the_registered_http_mapping_and_headers():
    app = FastAPI()
    register_error_handlers(app)
    for code in ErrorCode:
        def _raise(code: ErrorCode = code):
            raise service_error(code)

        app.add_api_route(f"/errors/{int(code)}", _raise)

    client = TestClient(app)
    for code in ErrorCode:
        _assert_error(client.get(f"/errors/{int(code)}"), code)


def test_service_error_payload_is_flat_and_uses_the_registry_message():
    response = TestClient(_app_with_extra_route()).get("/raise-service-error")

    _assert_error(response, ErrorCode.INVALID_REQUEST)
    assert "detail" not in response.json()


def test_plain_http_exception_does_not_expose_its_detail():
    response = TestClient(_app_with_extra_route()).get("/raise-plain-http-exception")

    _assert_error(response, ErrorCode.ROUTE_NOT_FOUND)
    assert "internal.example" not in response.text
    assert "password=leaked" not in response.text


def test_route_not_found_and_method_not_allowed_are_flat_json_errors():
    client = TestClient(_app_with_extra_route())

    _assert_error(client.get("/missing-route"), ErrorCode.ROUTE_NOT_FOUND)
    _assert_error(client.post("/needs-q"), ErrorCode.METHOD_NOT_ALLOWED)


def test_request_validation_errors_replace_fastapi_422():
    app = FastAPI()
    register_error_handlers(app)

    class FakeRequest(BaseModel):
        q: str
        page: int = Field(ge=1)

    @app.post("/search")
    def _search(request: FakeRequest):
        return {"ok": True}

    client = TestClient(app)
    _assert_error(client.post("/search", json={"q": "阀门", "page": 0}), ErrorCode.PAGINATION_OUT_OF_RANGE)
    _assert_error(client.post("/search", content="{", headers={"content-type": "application/json"}), ErrorCode.INVALID_REQUEST)


def test_unexpected_exception_does_not_expose_internal_detail_or_log_value(caplog):
    caplog.set_level(logging.ERROR, logger="app.core.error_handlers")
    response = TestClient(_app_with_extra_route(), raise_server_exceptions=False).get("/raise-unexpected")

    _assert_error(response, ErrorCode.INTERNAL_ERROR)
    assert "database password leaked" not in response.text
    assert "database password leaked" not in caplog.text
    assert response.json()["request_id"] in caplog.text


def test_known_error_log_uses_the_same_request_id(caplog):
    caplog.set_level(logging.WARNING, logger="app.core.error_handlers")
    response = TestClient(_app_with_extra_route()).get("/missing-route")

    _assert_error(response, ErrorCode.ROUTE_NOT_FOUND)
    assert response.json()["request_id"] in caplog.text
