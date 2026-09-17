"""验证控制台凭据、路由依赖顺序与日志中敏感信息的隔离。"""

from contextlib import asynccontextmanager

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.core.exceptions import QuerySyntaxError
from app.core.security import require_console_access
from app.api.console import get_detail_service, get_search_service
from app.main import app


CONSOLE_REQUESTS = [
    ("GET", "/console", None),
    ("GET", "/console/", None),
    ("POST", "/console-api/search", {"q": "阀门"}),
    (
        "POST",
        "/console-api/test/target-rank",
        {"q": "阀门", "target_identifier": "CN100B"},
    ),
    ("GET", "/console-api/detail/patent-1", None),
    ("GET", "/console-api/citations/patent-1", None),
    ("GET", "/console-api/legal-history/patent-1", None),
]
CONSOLE_ROUTE_PATHS = {
    "/console",
    "/console/",
    "/console-api/search",
    "/console-api/test/target-rank",
    "/console-api/detail/{patent_id}",
    "/console-api/citations/{patent_id}",
    "/console-api/legal-history/{patent_id}",
}


def _authenticated_settings() -> Settings:
    return Settings(
        enable_auth=True,
        api_token="console-test-token",
        console_username="console-user",
        console_password="console-password",
    )


@pytest.mark.parametrize(("method", "path", "payload"), CONSOLE_REQUESTS)
@pytest.mark.parametrize("credential", [None, "wrong-token", "rotated-old-token"])
def test_console_page_and_api_reject_untrusted_credentials(
    method,
    path,
    payload,
    credential,
):
    app.dependency_overrides[get_settings] = _authenticated_settings
    headers = {"X-API-Key": credential} if credential is not None else {}
    try:
        with TestClient(app) as client:
            response = client.request(method, path, json=payload, headers=headers)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["code"] == 40101
    assert response.json()["retryable"] is False
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert response.headers["WWW-Authenticate"] == 'Basic realm="patent-console"'


@pytest.mark.parametrize(("method", "path", "payload"), CONSOLE_REQUESTS)
def test_console_page_and_api_reject_wrong_basic_credentials(method, path, payload):
    app.dependency_overrides[get_settings] = _authenticated_settings
    try:
        with TestClient(app) as client:
            response = client.request(
                method,
                path,
                json=payload,
                auth=("console-user", "wrong-password"),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["code"] == 40101
    assert response.headers["WWW-Authenticate"] == 'Basic realm="patent-console"'


@pytest.mark.parametrize("path", ["/console", "/console/"])
def test_console_page_accepts_the_existing_api_key(path):
    app.dependency_overrides[get_settings] = _authenticated_settings
    try:
        with TestClient(app) as client:
            response = client.get(path, headers={"X-API-Key": "console-test-token"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "base-uri 'none'; frame-ancestors 'none'"
    assert "专利检索" in response.text


@pytest.mark.parametrize("path", ["/console", "/console/"])
def test_console_page_accepts_browser_basic_credentials_without_backend_token(path):
    app.dependency_overrides[get_settings] = _authenticated_settings
    try:
        with TestClient(app) as client:
            response = client.get(
                path,
                auth=("console-user", "console-password"),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "console-test-token" not in response.text
    assert "console-password" not in response.text
    assert "X-API-Key" not in response.text


class SuccessfulSearch:
    def search(self, request):
        return {
            "total": 0,
            "page": request.page,
            "page_size": request.page_size,
            "total_pages": 0,
            "accessible_pages": 0,
            "next_page": None,
            "took_ms": None,
            "records": [],
        }


def test_console_api_accepts_the_existing_api_key():
    app.dependency_overrides[get_settings] = _authenticated_settings
    app.dependency_overrides[get_search_service] = lambda: SuccessfulSearch()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/console-api/search",
                json={"q": "阀门"},
                headers={"X-API-Key": "console-test-token"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["records"] == []


def test_console_api_accepts_browser_basic_credentials():
    app.dependency_overrides[get_settings] = _authenticated_settings
    app.dependency_overrides[get_search_service] = lambda: SuccessfulSearch()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/console-api/search",
                json={"q": "阀门"},
                auth=("console-user", "console-password"),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["records"] == []


def test_console_basic_password_may_contain_a_colon():
    app.dependency_overrides[get_settings] = lambda: Settings(
        enable_auth=True,
        api_token="console-test-token",
        console_username="console-user",
        console_password="console:password",
    )
    app.dependency_overrides[get_search_service] = lambda: SuccessfulSearch()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/console-api/search",
                json={"q": "阀门"},
                auth=("console-user", "console:password"),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200


def test_every_console_route_declares_the_shared_auth_dependency():
    console_routes = {
        route.path: route
        for route in app.routes
        if isinstance(route, APIRoute)
        and (route.path in {"/console", "/console/"} or route.path.startswith("/console-api/"))
    }

    assert set(console_routes) == CONSOLE_ROUTE_PATHS
    for route in console_routes.values():
        dependency_calls = {
            dependency.call for dependency in route.dependant.dependencies
        }
        assert require_console_access in dependency_calls


class ExplodingBulkhead:
    @asynccontextmanager
    async def slot(self):
        raise AssertionError("untrusted Console request must not acquire a bulkhead slot")
        yield


def test_console_authentication_runs_before_bulkhead_admission():
    app.dependency_overrides[get_settings] = _authenticated_settings
    try:
        with TestClient(app) as client:
            app.state.heavy_search_request_bulkhead = ExplodingBulkhead()
            app.state.search_request_bulkhead = ExplodingBulkhead()
            response = client.post("/console-api/search", json={"q": "阀门"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 401
    assert response.json()["code"] == 40101


class SensitiveQueryFailure:
    def search(self, _request):
        raise QuerySyntaxError("invalid query")


def test_console_failure_logs_do_not_include_queries_or_credentials(caplog):
    query_sentinel = "CONFIDENTIAL-QUERY-SENTINEL"
    token_sentinel = "console-test-token"
    app.dependency_overrides[get_settings] = _authenticated_settings
    app.dependency_overrides[get_search_service] = lambda: SensitiveQueryFailure()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/console-api/search",
                json={"q": query_sentinel},
                headers={"X-API-Key": token_sentinel},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["code"] == 40001
    assert query_sentinel not in caplog.text
    assert token_sentinel not in caplog.text


def test_console_basic_password_is_not_logged(caplog):
    password_sentinel = "console-password"
    app.dependency_overrides[get_settings] = _authenticated_settings
    app.dependency_overrides[get_search_service] = lambda: SensitiveQueryFailure()
    try:
        with TestClient(app) as client:
            response = client.post(
                "/console-api/search",
                json={"q": "invalid"},
                auth=("console-user", password_sentinel),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert password_sentinel not in caplog.text


class SensitiveDetailSuccess:
    def __init__(self, claims):
        self.claims = claims

    def get_detail(self, patent_id, include_description=False):
        return {"id": patent_id, "claims": self.claims}


def test_console_success_logs_do_not_include_sensitive_patent_text(caplog):
    claims_sentinel = "CONFIDENTIAL-CLAIMS-SENTINEL"
    app.dependency_overrides[get_settings] = _authenticated_settings
    app.dependency_overrides[get_detail_service] = lambda: SensitiveDetailSuccess(
        claims_sentinel
    )
    try:
        with TestClient(app) as client:
            response = client.get(
                "/console-api/detail/patent-1",
                headers={"X-API-Key": "console-test-token"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["claims"] == claims_sentinel
    assert claims_sentinel not in caplog.text
