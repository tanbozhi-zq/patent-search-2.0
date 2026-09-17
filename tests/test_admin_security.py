"""验证管理面只读鉴权、缓存策略、路由边界与业务指标隔离。"""

import logging

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.api.admin_config import (
    require_admin_config_drafts,
    require_admin_config_write,
)
from app.api.admin_runtime_config import (
    require_admin_runtime_config_apply,
    require_admin_runtime_config_rollback,
)
from app.core.security import require_admin
from app.main import app


ADMIN_PATHS = {
    "/admin",
    "/admin/",
    "/admin/admin.css",
    "/admin/admin.js",
    "/admin/favicon.svg",
    "/admin-api/v1/status",
    "/admin-api/v1/metrics",
    "/admin-api/v1/config",
    "/admin-api/v1/config-schema",
    "/admin-api/v1/config-drafts",
    "/admin-api/v1/config-drafts/export",
    "/admin-api/v1/runtime-config",
    "/admin-api/v1/runtime-config/apply",
    "/admin-api/v1/runtime-config/rollback",
    "/admin-api/v1/logs",
}
RUNTIME_WRITE_PATHS = {
    "/admin-api/v1/runtime-config/apply",
    "/admin-api/v1/runtime-config/rollback",
}


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "admin_enabled": True,
        "admin_viewer_username": "admin-viewer",
        "admin_viewer_password": "admin-viewer-password",
        "api_token": "business-api-token",
        "console_username": "console-user",
        "console_password": "console-password",
        "service_release_commit": "7a0468c",
        "service_release_tag": "v0.10.0",
        "service_instance_id": "instance-a",
    }
    values.update(overrides)
    return Settings(**values)


def _request(client: TestClient, path: str, **kwargs):
    if path not in RUNTIME_WRITE_PATHS:
        return client.get(path, **kwargs)
    request_kwargs = dict(kwargs)
    headers = dict(request_kwargs.pop("headers", {}))
    if path.endswith("/apply"):
        headers.setdefault("X-Admin-Intent", "apply-runtime-config")
        payload = {
            "draft_id": "00000000-0000-0000-0000-000000000000",
            "expected_version": "0" * 64,
        }
    else:
        headers.setdefault("X-Admin-Intent", "rollback-runtime-config")
        payload = {
            "expected_version": "0" * 64,
            "target_version": "0" * 64,
            "reason": "验证鉴权边界",
        }
    headers.setdefault("Idempotency-Key", "runtime-security-test-key")
    return client.post(path, headers=headers, json=payload, **request_kwargs)


def test_admin_routes_share_one_admin_identity_and_only_guarded_control_plane_routes_can_post():
    routes = [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and (
            route.path.startswith("/admin-api/")
            or route.path in {path for path in ADMIN_PATHS if path.startswith("/admin")}
        )
    ]

    assert {route.path for route in routes} == ADMIN_PATHS
    post_routes = {route.path: route for route in routes if "POST" in route.methods}
    assert {path: route.methods for path, route in post_routes.items()} == {
        "/admin-api/v1/config-drafts": {"POST"},
        "/admin-api/v1/runtime-config/apply": {"POST"},
        "/admin-api/v1/runtime-config/rollback": {"POST"},
    }
    for route in routes:
        if route.path == "/admin-api/v1/config-drafts" and "POST" in route.methods:
            expected_dependency = require_admin_config_write
        elif route.path == "/admin-api/v1/runtime-config/apply":
            expected_dependency = require_admin_runtime_config_apply
        elif route.path == "/admin-api/v1/runtime-config/rollback":
            expected_dependency = require_admin_runtime_config_rollback
        elif route.path in {
            "/admin-api/v1/config-schema",
            "/admin-api/v1/config-drafts",
            "/admin-api/v1/config-drafts/export",
            "/admin-api/v1/runtime-config",
        }:
            expected_dependency = require_admin_config_drafts
        else:
            assert route.methods <= {"GET", "HEAD"}
            expected_dependency = require_admin
        dependency_calls = {
            dependency.call for dependency in route.dependant.dependencies
        }
        assert expected_dependency in dependency_calls


def test_admin_routes_reject_anonymous_business_and_console_credentials():
    app.dependency_overrides[get_settings] = lambda: _settings()
    try:
        with TestClient(app) as client:
            for path in ADMIN_PATHS:
                responses = (
                    _request(client, path),
                    _request(client, path, headers={"X-API-Key": "business-api-token"}),
                    _request(client, path, auth=("console-user", "console-password")),
                    _request(client, path, auth=("admin-viewer", "wrong-password")),
                )
                for response in responses:
                    assert response.status_code == 401
                    assert response.json()["code"] == 40101
                    assert response.headers["WWW-Authenticate"] == (
                        'Basic realm="patent-admin"'
                    )
    finally:
        app.dependency_overrides.clear()


def test_console_and_admin_accept_shared_credentials_when_configured():
    settings = _settings(
        console_username="admin",
        console_password="shared-browser-password",
        admin_viewer_username="admin",
        admin_viewer_password="shared-browser-password",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    auth = ("admin", "shared-browser-password")
    try:
        with TestClient(app) as client:
            console_response = client.get("/console/", auth=auth)
            admin_response = client.get("/admin/", auth=auth)
    finally:
        app.dependency_overrides.clear()

    assert console_response.status_code == 200
    assert admin_response.status_code == 200


def test_admin_auth_is_not_bypassed_when_business_auth_is_disabled():
    app.dependency_overrides[get_settings] = lambda: _settings(enable_auth=False)
    try:
        with TestClient(app) as client:
            denied = client.get("/admin-api/v1/status")
            accepted = client.get(
                "/admin-api/v1/status",
                auth=("admin-viewer", "admin-viewer-password"),
            )
    finally:
        app.dependency_overrides.clear()

    assert denied.status_code == 401
    assert accepted.status_code == 200
    assert accepted.json()["role"] == "admin"
    assert accepted.json()["config_drafts_enabled"] is False
    assert "/api/patent/search" in accepted.json()["log_routes"]
    assert "__unmatched__" in accepted.json()["log_routes"]
    assert not any(
        route.startswith("/admin") for route in accepted.json()["log_routes"]
    )


def test_disabled_admin_surface_fails_closed_as_not_found():
    app.dependency_overrides[get_settings] = lambda: _settings(admin_enabled=False)
    try:
        with TestClient(app) as client:
            response = client.get(
                "/admin/",
                auth=("admin-viewer", "admin-viewer-password"),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["code"] == 40400


def test_admin_page_and_api_are_no_store_and_page_has_strict_csp():
    app.dependency_overrides[get_settings] = lambda: _settings()
    auth = ("admin-viewer", "admin-viewer-password")
    try:
        with TestClient(app) as client:
            page = client.get("/admin/", auth=auth)
            status = client.get("/admin-api/v1/status", auth=auth)
    finally:
        app.dependency_overrides.clear()

    assert page.status_code == status.status_code == 200
    assert page.headers["Cache-Control"] == "no-store"
    assert status.headers["Cache-Control"] == "no-store"
    csp = page.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


@pytest.mark.parametrize("window_seconds", [300, 900, 3600])
def test_admin_metrics_api_accepts_allowlisted_windows(window_seconds):
    app.dependency_overrides[get_settings] = lambda: _settings()
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/admin-api/v1/metrics?window_seconds={window_seconds}",
                auth=("admin-viewer", "admin-viewer-password"),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["window_seconds"] == window_seconds


@pytest.mark.parametrize("window_seconds", [301, "invalid"])
def test_admin_metrics_api_rejects_non_allowlisted_window(window_seconds):
    app.dependency_overrides[get_settings] = lambda: _settings()
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/admin-api/v1/metrics?window_seconds={window_seconds}",
                auth=("admin-viewer", "admin-viewer-password"),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["code"] == 40002


def test_unknown_admin_prefixed_paths_remain_observed_as_unmatched():
    with TestClient(app) as client:
        responses = [
            client.get("/admin/not-a-route"),
            client.get("/admin-api/garbage"),
        ]
        exposition = client.get("/metrics").text

    assert all(response.status_code == 404 for response in responses)
    assert (
        'patent_search_http_requests_total{code="40400",method="GET",'
        'route="__unmatched__",status="404"}'
    ) in exposition


def test_admin_requests_do_not_pollute_business_metrics_but_keep_logs(caplog):
    app.dependency_overrides[get_settings] = lambda: _settings()
    caplog.set_level(logging.INFO)
    try:
        with TestClient(app) as client:
            response = client.get(
                "/admin-api/v1/status",
                auth=("admin-viewer", "admin-viewer-password"),
            )
            exposition = client.get("/metrics").text
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert 'route="/admin-api/v1/status"' not in exposition
    assert any(
        getattr(record, "event", None) == "http_request_completed"
        and getattr(record, "route", None) == "/admin-api/v1/status"
        for record in caplog.records
    )
    assert any(
        getattr(record, "event", None) == "admin_read_completed"
        and getattr(record, "action", None) == "status.read"
        for record in caplog.records
    )


def test_config_response_uses_positive_allowlist_and_never_exposes_sentinels():
    sentinels = {
        "api_token": "SENTINEL_API_TOKEN",
        "console_password": "SENTINEL_CONSOLE_PASSWORD",
        "admin_viewer_password": "SENTINEL_ADMIN_PASSWORD",
        "opensearch_host": "sentinel-opensearch-host.internal",
        "opensearch_user": "SENTINEL_OS_USER",
        "opensearch_pass": "SENTINEL_OS_PASSWORD",
        "opensearch_index": "SENTINEL_INDEX_ALIAS",
        "admin_prometheus_url": "https://sentinel-prometheus.internal",
    }
    settings = _settings(**sentinels)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            response = client.get(
                "/admin-api/v1/config",
                auth=("admin-viewer", sentinels["admin_viewer_password"]),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    text = response.text
    for sentinel in sentinels.values():
        assert sentinel not in text
    items = {item["key"]: item for item in response.json()["items"]}
    assert items["secret.api_token"]["configured"] is True
    assert items["secret.console_password"]["configured"] is True
    assert items["secret.opensearch_credentials"]["configured"] is True
    assert items["secret.admin_viewer_password"]["configured"] is True
    assert not any("host" in key or "index" in key for key in items)
