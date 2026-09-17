"""验证业务 API Key 鉴权及其统一 HTTP 错误响应契约。"""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.exceptions import ErrorCode, ServiceError
from app.main import app
from app.core.security import require_api_key


def test_require_api_key_allows_requests_when_auth_disabled():
    settings = Settings(enable_auth=False, api_token="")

    assert require_api_key(x_api_key=None, settings=settings) is None


def test_require_api_key_allows_matching_token():
    settings = Settings(enable_auth=True, api_token="secret-token")

    assert require_api_key(x_api_key="secret-token", settings=settings) is None


def test_require_api_key_rejects_missing_token():
    settings = Settings(enable_auth=True, api_token="secret-token")

    with pytest.raises(ServiceError) as exc:
        require_api_key(x_api_key=None, settings=settings)

    assert exc.value.code == ErrorCode.AUTHENTICATION_FAILED


def test_require_api_key_rejects_wrong_token():
    settings = Settings(enable_auth=True, api_token="secret-token")

    with pytest.raises(ServiceError) as exc:
        require_api_key(x_api_key="wrong-token", settings=settings)

    assert exc.value.code == ErrorCode.AUTHENTICATION_FAILED


def test_http_auth_error_uses_the_shared_error_contract():
    app.dependency_overrides[get_settings] = lambda: Settings(enable_auth=True, api_token="secret-token")
    try:
        response = TestClient(app).post("/api/patent/search", json={"q": "阀门"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["code"] == 40101
    assert response.json()["retryable"] is False
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_http_api_does_not_accept_console_basic_credentials():
    app.dependency_overrides[get_settings] = lambda: Settings(
        enable_auth=True,
        api_token="backend-token",
        console_username="console-user",
        console_password="console-password",
    )
    try:
        response = TestClient(app).post(
            "/api/patent/search",
            json={"q": "阀门"},
            auth=("console-user", "console-password"),
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["code"] == 40101
