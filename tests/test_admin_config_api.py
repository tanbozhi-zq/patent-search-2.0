"""验证配置草稿管理 API 的开关、鉴权、写入边界、脱敏与故障隔离。"""

import logging
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.api.admin_config import get_admin_config_draft_store
from app.api.search import get_search_service
from app.core.admin_config import (
    AdminConfigStoreBusyError,
    AdminConfigStoreError,
    baseline_fingerprint,
)
from app.core.config import Settings, get_settings
from app.core.security import require_api_key
from app.main import app


AUTH = ("admin", "admin-password")
WRITE_HEADERS = {"X-Admin-Intent": "create-config-draft"}


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "admin_enabled": True,
        "admin_viewer_username": AUTH[0],
        "admin_viewer_password": AUTH[1],
        "admin_config_drafts_enabled": True,
        "admin_config_database_path": str(
            tmp_path / "admin-state" / "admin-config.sqlite3"
        ),
        "api_token": "business-api-token",
        "console_username": "console-user",
        "console_password": "console-password",
        "patent_search_bulkhead_capacity": 4,
        "patent_search_heavy_bulkhead_capacity": 3,
        "patent_search_bulkhead_acquire_timeout_seconds": 0.01,
        "service_release_commit": "68ae664",
        "service_release_tag": "v0.10.0",
        "service_instance_id": "instance-a",
    }
    values.update(overrides)
    return Settings(**values)


def _create_payload(settings: Settings, **overrides):
    payload = {
        "baseline_fingerprint": baseline_fingerprint(settings),
        "reason": "停用一次重试以观察尾延迟变化",
        "candidate_values": {"opensearch.max_retries": 0},
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def admin_settings(tmp_path):
    settings = _settings(tmp_path)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield settings
    finally:
        app.dependency_overrides.clear()


def test_draft_surface_is_fail_closed_until_explicitly_enabled(tmp_path):
    settings = _settings(tmp_path, admin_config_drafts_enabled=False)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            status = client.get("/admin-api/v1/status", auth=AUTH)
            schema = client.get("/admin-api/v1/config-schema", auth=AUTH)
            drafts = client.get("/admin-api/v1/config-drafts", auth=AUTH)
            exported = client.get(
                "/admin-api/v1/config-drafts/export?id="
                "00000000-0000-0000-0000-000000000000",
                auth=AUTH,
            )
    finally:
        app.dependency_overrides.clear()

    assert status.status_code == 200
    assert status.json()["config_drafts_enabled"] is False
    assert schema.status_code == drafts.status_code == exported.status_code == 404


def test_all_draft_routes_reject_anonymous_business_console_and_wrong_admin(
    admin_settings,
):
    credentials = (
        {},
        {"headers": {"X-API-Key": "business-api-token"}},
        {"auth": ("console-user", "console-password")},
        {"auth": (AUTH[0], "wrong-password")},
    )
    with TestClient(app) as client:
        for path in (
            "/admin-api/v1/config-schema",
            "/admin-api/v1/config-drafts",
            "/admin-api/v1/config-drafts/export?id="
            "00000000-0000-0000-0000-000000000000",
        ):
            for kwargs in credentials:
                response = client.get(path, **kwargs)
                assert response.status_code == 401
                assert response.json()["code"] == 40101

        payload = _create_payload(admin_settings)
        for kwargs in credentials:
            response = client.post(
                "/admin-api/v1/config-drafts",
                json=payload,
                headers={**kwargs.get("headers", {}), **WRITE_HEADERS},
                auth=kwargs.get("auth"),
            )
            assert response.status_code == 401


def test_schema_is_machine_readable_and_contains_no_secrets_targets_or_paths(tmp_path):
    sentinels = {
        "api_token": "SENTINEL_API_TOKEN",
        "console_password": "SENTINEL_CONSOLE_PASSWORD",
        "admin_viewer_password": "SENTINEL_ADMIN_PASSWORD",
        "opensearch_host": "sentinel-opensearch.internal",
        "opensearch_user": "SENTINEL_USER",
        "opensearch_pass": "SENTINEL_PASSWORD",
        "opensearch_index": "SENTINEL_INDEX",
    }
    settings = _settings(tmp_path, **sentinels)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            response = client.get(
                "/admin-api/v1/config-schema",
                auth=(AUTH[0], sentinels["admin_viewer_password"]),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    text = response.text
    for sentinel in (*sentinels.values(), settings.admin_config_database_path):
        assert sentinel not in text
    body = response.json()
    assert len(body["baseline_fingerprint"]) == 64
    assert body["draft_ttl_seconds"] == 86_400
    assert body["apply_mode_contract"] == "issue_60_revalidation_required"
    assert len(body["items"]) == 16
    for item in body["items"]:
        assert item["minimum"] < item["maximum"]
        assert item["current_value"] == item["rollback_value"]
        assert item["apply_mode"] in {"runtime_reload", "restart_required"}


def test_create_validates_and_persists_an_immutable_draft(admin_settings):
    with TestClient(app) as client:
        response = client.post(
            "/admin-api/v1/config-drafts",
            auth=AUTH,
            headers=WRITE_HEADERS,
            json=_create_payload(admin_settings),
        )
        history = client.get(
            "/admin-api/v1/config-drafts?limit=20",
            auth=AUTH,
        )

    assert response.status_code == 201
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body["status"] == body["validation_status"] == "validated"
    assert body["expires_at"] > body["created_at"]
    assert body["operator"] == AUTH[0]
    assert body["diff"] == {
        "opensearch.max_retries": {
            "old": 1,
            "new": 0,
            "apply_mode": "runtime_reload",
        }
    }
    assert history.status_code == 200
    assert [item["id"] for item in history.json()["items"]] == [body["id"]]
    assert client.post(
        f"/admin-api/v1/config-drafts/{body['id']}/apply",
        auth=AUTH,
    ).status_code == 404


def test_unknown_and_sensitive_keys_are_rejected_without_echo_or_audit_row(
    admin_settings,
):
    sentinel_key = "secret.api_token.SENTINEL"
    with TestClient(app) as client:
        response = client.post(
            "/admin-api/v1/config-drafts",
            auth=AUTH,
            headers=WRITE_HEADERS,
            json=_create_payload(
                admin_settings,
                candidate_values={sentinel_key: 123},
            ),
        )
        history = client.get("/admin-api/v1/config-drafts", auth=AUTH)

    assert response.status_code == 400
    assert response.json()["code"] == 40002
    assert sentinel_key not in response.text
    assert history.json()["items"] == []


def test_invalid_known_candidate_is_saved_only_as_sanitized_invalid_result(
    admin_settings,
):
    sentinel = "SENTINEL_RAW_VALUE"
    with TestClient(app) as client:
        response = client.post(
            "/admin-api/v1/config-drafts",
            auth=AUTH,
            headers=WRITE_HEADERS,
            json=_create_payload(
                admin_settings,
                candidate_values={"request.deadline_seconds": sentinel},
            ),
        )
        history = client.get("/admin-api/v1/config-drafts", auth=AUTH)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "invalid"
    assert body["candidate_values"] == {}
    assert body["diff"] == {}
    assert body["validation_errors"][0]["code"] == "invalid_type"
    assert sentinel not in response.text
    assert sentinel not in history.text


def test_stale_client_baseline_returns_a_stable_conflict_without_writing(
    admin_settings,
):
    payload = _create_payload(admin_settings)
    payload["baseline_fingerprint"] = "0" * 64
    with TestClient(app) as client:
        response = client.post(
            "/admin-api/v1/config-drafts",
            auth=AUTH,
            headers=WRITE_HEADERS,
            json=payload,
        )
        history = client.get("/admin-api/v1/config-drafts", auth=AUTH)

    assert response.status_code == 409
    assert response.json()["code"] == 40901
    assert history.json()["items"] == []


@pytest.mark.parametrize(
    "headers",
    (
        {},
        {"X-Admin-Intent": "wrong-intent"},
        {
            **WRITE_HEADERS,
            "Origin": "https://evil.example",
            "Sec-Fetch-Site": "cross-site",
        },
    ),
)
def test_browser_write_boundary_requires_json_same_origin_intent(
    admin_settings,
    headers,
):
    with TestClient(app) as client:
        response = client.post(
            "/admin-api/v1/config-drafts",
            auth=AUTH,
            headers=headers,
            json=_create_payload(admin_settings),
        )

    assert response.status_code == 400
    assert response.json()["code"] == 40002


def test_browser_write_boundary_rejects_non_json_content_type(admin_settings):
    with TestClient(app) as client:
        response = client.post(
            "/admin-api/v1/config-drafts",
            auth=AUTH,
            headers={**WRITE_HEADERS, "Content-Type": "text/plain"},
            content="not-json",
        )

    assert response.status_code == 400
    assert response.json()["code"] == 40002


def test_draft_body_reason_change_count_and_history_limit_are_bounded(admin_settings):
    with TestClient(app) as client:
        too_large = client.post(
            "/admin-api/v1/config-drafts",
            auth=AUTH,
            headers=WRITE_HEADERS,
            content=b"{" + b"x" * (16 * 1024) + b"}",
        )
        long_reason = client.post(
            "/admin-api/v1/config-drafts",
            auth=AUTH,
            headers=WRITE_HEADERS,
            json=_create_payload(admin_settings, reason="x" * 501),
        )
        too_many = client.post(
            "/admin-api/v1/config-drafts",
            auth=AUTH,
            headers=WRITE_HEADERS,
            json=_create_payload(
                admin_settings,
                candidate_values={f"unknown-{index}": index for index in range(17)},
            ),
        )
        history_limit = client.get(
            "/admin-api/v1/config-drafts?limit=101",
            auth=AUTH,
        )

    assert too_large.status_code == 413
    assert too_large.json()["code"] == 41301
    assert long_reason.status_code == too_many.status_code == 400
    assert history_limit.status_code == 400


def test_streamed_draft_body_without_content_length_returns_413(admin_settings):
    def chunks():
        yield b'{' + b'x' * (9 * 1024)
        yield b'x' * (9 * 1024) + b'}'

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/admin-api/v1/config-drafts",
            auth=AUTH,
            headers={**WRITE_HEADERS, "Content-Type": "application/json"},
            content=chunks(),
        )

    assert response.status_code == 413
    assert response.json()["code"] == 41301


def test_history_derives_expired_when_the_current_baseline_changes(tmp_path):
    original = _settings(tmp_path)
    current = original
    app.dependency_overrides[get_settings] = lambda: current
    try:
        with TestClient(app) as client:
            created = client.post(
                "/admin-api/v1/config-drafts",
                auth=AUTH,
                headers=WRITE_HEADERS,
                json=_create_payload(original),
            )
            current = _settings(tmp_path, opensearch_max_retries=0)
            history = client.get("/admin-api/v1/config-drafts", auth=AUTH)
    finally:
        app.dependency_overrides.clear()

    assert created.json()["status"] == "validated"
    [item] = history.json()["items"]
    assert item["status"] == "expired"
    assert item["validation_status"] == "validated"
    assert item["id"] == created.json()["id"]


def test_draft_operations_do_not_touch_runtime_objects_environment_or_metrics(
    admin_settings,
):
    settings_before = admin_settings.model_dump()
    environment_before = dict(os.environ)
    with TestClient(app) as client:
        repository = app.state.opensearch_repository
        query_budget_provider = app.state.query_budget_provider
        global_bulkhead = app.state.search_request_bulkhead
        heavy_bulkhead = app.state.heavy_search_request_bulkhead
        readiness_probe = app.state.readiness_probe
        readiness_indices = app.state.readiness_client.indices

        def runtime_values():
            return {
                "repository_timeout": repository.timeout,
                "repository_settings": repository.settings.model_dump(),
                "repository_client": id(repository.client),
                "repository_slots": id(repository._client_slots),
                "query_budget": query_budget_provider.snapshot(),
                "global_bulkhead": (
                    global_bulkhead.capacity,
                    global_bulkhead.acquire_timeout_seconds,
                    id(global_bulkhead._slots),
                ),
                "heavy_bulkhead": (
                    heavy_bulkhead.capacity,
                    heavy_bulkhead.acquire_timeout_seconds,
                    id(heavy_bulkhead._slots),
                ),
                "readiness_probe": (
                    readiness_probe._timeout_seconds,
                    readiness_probe._success_cache_seconds,
                    readiness_probe._failure_cache_seconds,
                ),
            }

        runtime_objects_before = {
            name: id(getattr(app.state, name))
            for name in (
                "opensearch_repository",
                "query_budget_provider",
                "search_request_bulkhead",
                "heavy_search_request_bulkhead",
                "readiness_probe",
                "readiness_client",
            )
        }
        runtime_values_before = runtime_values()
        with (
            patch.object(
                repository,
                "_perform",
                side_effect=AssertionError("draft operations must not query OpenSearch"),
            ) as repository_perform,
            patch.object(
                readiness_indices,
                "exists",
                side_effect=AssertionError("draft operations must not probe OpenSearch"),
            ) as readiness_exists,
        ):
            response = client.post(
                "/admin-api/v1/config-drafts",
                auth=AUTH,
                headers=WRITE_HEADERS,
                json=_create_payload(admin_settings),
            )
            exported = client.get(
                f"/admin-api/v1/config-drafts/export?id={response.json()['id']}",
                auth=AUTH,
            )
            repository_perform.assert_not_called()
            readiness_exists.assert_not_called()

        runtime_values_after = runtime_values()
        runtime_objects_after = {
            name: id(getattr(app.state, name)) for name in runtime_objects_before
        }
        metrics = client.get("/metrics").text

    assert response.status_code == 201
    assert exported.status_code == 200
    assert exported.headers["Cache-Control"] == "no-store"
    assert "attachment;" in exported.headers["Content-Disposition"]
    assert exported.json() == {
        "format_version": "admin-config-draft.v1",
        "draft": response.json(),
    }
    assert settings_before == admin_settings.model_dump()
    assert environment_before == dict(os.environ)
    assert runtime_objects_before == runtime_objects_after
    assert runtime_values_before == runtime_values_after
    assert 'route="/admin-api/v1/config-drafts"' not in metrics
    assert 'route="/admin-api/v1/config-drafts/export"' not in metrics


class _FailingDraftStore:
    def __init__(self, error: AdminConfigStoreError):
        self.error = error

    def create(self, **_kwargs):
        raise self.error

    def list(self, **_kwargs):
        raise self.error

    def get(self, **_kwargs):
        raise self.error


@pytest.mark.parametrize(
    ("store_error", "expected_status", "expected_code", "retry_after"),
    (
        (
            AdminConfigStoreBusyError("SENTINEL_PRIVATE_DATABASE_PATH is locked"),
            503,
            50301,
            "1",
        ),
        (
            AdminConfigStoreError("SENTINEL_PRIVATE_DATABASE_PATH is corrupt"),
            500,
            50002,
            None,
        ),
    ),
)
def test_create_list_and_export_map_store_faults_without_leaking_details(
    admin_settings,
    store_error,
    expected_status,
    expected_code,
    retry_after,
):
    failing_store = _FailingDraftStore(store_error)
    app.dependency_overrides[get_admin_config_draft_store] = lambda: failing_store
    try:
        with TestClient(app) as client:
            responses = (
                client.post(
                    "/admin-api/v1/config-drafts",
                    auth=AUTH,
                    headers=WRITE_HEADERS,
                    json=_create_payload(admin_settings),
                ),
                client.get("/admin-api/v1/config-drafts", auth=AUTH),
                client.get(
                    "/admin-api/v1/config-drafts/export?id="
                    "00000000-0000-0000-0000-000000000000",
                    auth=AUTH,
                ),
            )
    finally:
        app.dependency_overrides.pop(get_admin_config_draft_store, None)

    for response in responses:
        assert response.status_code == expected_status
        assert response.json()["code"] == expected_code
        assert response.headers.get("Retry-After") == retry_after
        assert "SENTINEL_PRIVATE_DATABASE_PATH" not in response.text


def test_config_store_failures_reuse_response_request_ids(admin_settings, caplog):
    failing_store = _FailingDraftStore(
        AdminConfigStoreError("SENTINEL_PRIVATE_DATABASE_PATH is corrupt")
    )
    app.dependency_overrides[get_admin_config_draft_store] = lambda: failing_store
    request_ids = {
        "config_draft.create": "issue47-store-create-001",
        "config_drafts.read": "issue47-store-list-001",
        "config_draft.export": "issue47-store-export-001",
    }
    try:
        caplog.set_level(logging.ERROR)
        with TestClient(app) as client:
            responses = (
                client.post(
                    "/admin-api/v1/config-drafts",
                    auth=AUTH,
                    headers={
                        **WRITE_HEADERS,
                        "X-Request-ID": request_ids["config_draft.create"],
                    },
                    json=_create_payload(admin_settings),
                ),
                client.get(
                    "/admin-api/v1/config-drafts",
                    auth=AUTH,
                    headers={"X-Request-ID": request_ids["config_drafts.read"]},
                ),
                client.get(
                    "/admin-api/v1/config-drafts/export?id="
                    "00000000-0000-0000-0000-000000000000",
                    auth=AUTH,
                    headers={"X-Request-ID": request_ids["config_draft.export"]},
                ),
            )
    finally:
        app.dependency_overrides.pop(get_admin_config_draft_store, None)

    assert all(response.status_code == 500 for response in responses)
    records = {
        getattr(record, "action"): record
        for record in caplog.records
        if getattr(record, "event", None) == "admin_config_store_failed"
    }
    assert set(records) == set(request_ids)
    for action, request_id in request_ids.items():
        assert getattr(records[action], "request_id") == request_id
        assert getattr(records[action], "result") == "failed"
    assert "SENTINEL_PRIVATE_DATABASE_PATH" not in caplog.text


class _SuccessfulSearchService:
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


def test_corrupt_draft_database_cannot_break_the_public_search_path(tmp_path):
    state_directory = tmp_path / "admin-state"
    state_directory.mkdir(mode=0o700)
    state_directory.chmod(0o700)
    database_path = state_directory / "admin-config.sqlite3"
    corrupt_bytes = b"SENTINEL_CORRUPT_PRIVATE_DATABASE"
    database_path.write_bytes(corrupt_bytes)
    database_path.chmod(0o600)
    settings = _settings(
        tmp_path,
        admin_config_database_path=str(database_path),
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_search_service] = lambda: _SuccessfulSearchService()
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        with TestClient(app) as client:
            public_search = client.post(
                "/api/patent/search",
                json={"q": "阀门"},
            )
            admin_history = client.get("/admin-api/v1/config-drafts", auth=AUTH)
    finally:
        app.dependency_overrides.clear()

    assert public_search.status_code == 200
    assert public_search.json()["records"] == []
    assert admin_history.status_code == 500
    assert admin_history.json()["code"] == 50002
    assert "SENTINEL_CORRUPT_PRIVATE_DATABASE" not in admin_history.text
    assert database_path.read_bytes() == corrupt_bytes


def test_normal_audit_logs_never_include_reason_or_candidate_values(
    admin_settings,
    caplog,
):
    reason = "SENTINEL_REASON_MUST_NOT_ENTER_LOGS"
    caplog.set_level(logging.INFO)
    with TestClient(app) as client:
        response = client.post(
            "/admin-api/v1/config-drafts",
            auth=AUTH,
            headers=WRITE_HEADERS,
            json=_create_payload(admin_settings, reason=reason),
        )

    assert response.status_code == 201
    assert all(reason not in record.getMessage() for record in caplog.records)
    audit = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "admin_config_completed"
        and getattr(record, "action", None) == "config_draft.create"
    )
    assert getattr(audit, "draft_id") == response.json()["id"]
    assert getattr(audit, "changed_parameter_count") == 1
    assert getattr(audit, "validation_error_count") == 0
