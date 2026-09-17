"""验证 Issue 47 运行时配置联合验收的应用、回滚、关联证据与恢复护栏。"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Thread

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.admin_config import (
    AdminConfigDraftStore,
    RuntimeConfigController,
    RuntimeConfigProvider,
    runtime_snapshot_from_settings,
)
from app.core.config import Settings, get_settings
from app.main import app
from scripts import smoke_admin_runtime


AUTH = ("admin", "admin-password")
EXPECTED_RELEASE_COMMIT = "3b151b6"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        admin_enabled=True,
        admin_viewer_username=AUTH[0],
        admin_viewer_password=AUTH[1],
        admin_config_drafts_enabled=True,
        admin_runtime_config_enabled=True,
        admin_config_database_path=str(
            tmp_path / "admin-state" / "admin-config.sqlite3"
        ),
        api_token="business-api-token",
        console_username="console-user",
        console_password="console-password",
        patent_search_bulkhead_capacity=4,
        patent_search_heavy_bulkhead_capacity=3,
        patent_search_bulkhead_acquire_timeout_seconds=0.01,
        service_release_commit="3b151b6",
        service_release_tag="v0.10.0",
        service_instance_id="instance-a",
    )


class _Verifier:
    async def verify(self, _snapshot) -> None:
        return None


def _rewrite_runtime_operation(response, *, kind, status, rewrite):
    body = response.json()
    rewritten = 0
    operations = []
    for operation in body["recent_operations"]:
        if operation.get("kind") == kind and operation.get("status") == status:
            operation = rewrite(operation)
            rewritten += 1
        operations.append(operation)
    assert rewritten == 1
    body["recent_operations"] = operations
    return httpx.Response(
        status_code=response.status_code,
        headers=dict(response.headers),
        json=body,
    )


@pytest.fixture
def live_runtime_client(tmp_path):
    settings = _settings(tmp_path)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            initial = runtime_snapshot_from_settings(settings)
            provider = RuntimeConfigProvider(initial)
            store = AdminConfigDraftStore(settings.admin_config_database_path)
            controller = RuntimeConfigController(
                settings=settings,
                provider=provider,
                store=store,
                verifier=_Verifier(),
                mutation_min_interval_seconds=0,
            )
            app.state.runtime_config_provider = provider
            app.state.admin_config_store = store
            app.state.runtime_config_controller = controller
            yield client, provider, store, initial
    finally:
        app.dependency_overrides.clear()


def test_runtime_acceptance_applies_and_restores_the_complete_snapshot(
    live_runtime_client,
):
    client, provider, store, initial = live_runtime_client

    checks = smoke_admin_runtime.run_runtime_acceptance(
        client,
        username=AUTH[0],
        password=AUTH[1],
        business_api_token="business-api-token",
        expected_release_commit=EXPECTED_RELEASE_COMMIT,
        require_prometheus=False,
        require_journal_audit=False,
        check_probes=False,
        cooldown_seconds=0,
    )

    assert all(checks.values())
    assert checks["invalid_preflight_rejected_without_mutation"] is True
    assert checks["runtime_apply_and_readback"] is True
    assert checks["runtime_values_restored"] is True
    assert provider.snapshot().as_dict() == initial.as_dict()
    assert [
        operation.status for operation in store.list_runtime_operations(limit=10)
    ] == ["rolled_back", "applied"]


def test_runtime_acceptance_allows_only_expected_history_limit_truncation(
    live_runtime_client,
):
    client, provider, store, initial = live_runtime_client
    for index in range(smoke_admin_runtime.RUNTIME_OPERATION_HISTORY_LIMIT):
        response = client.post(
            "/admin-api/v1/runtime-config/apply",
            auth=AUTH,
            headers={
                "X-Admin-Intent": "apply-runtime-config",
                "Sec-Fetch-Site": "same-origin",
                "Idempotency-Key": f"issue47-preexisting-history-{index}",
            },
            json={
                "draft_id": f"{index + 1:08x}-0000-4000-8000-000000000000",
                "expected_version": initial.version,
            },
        )
        assert response.status_code == 400

    checks = smoke_admin_runtime.run_runtime_acceptance(
        client,
        username=AUTH[0],
        password=AUTH[1],
        business_api_token="business-api-token",
        expected_release_commit=EXPECTED_RELEASE_COMMIT,
        require_prometheus=False,
        require_journal_audit=False,
        check_probes=False,
        cooldown_seconds=0,
    )

    assert all(checks.values())
    assert provider.snapshot().as_dict() == initial.as_dict()
    operations = store.list_runtime_operations(limit=30)
    assert len(operations) == smoke_admin_runtime.RUNTIME_OPERATION_HISTORY_LIMIT + 2
    assert sum(operation.status == "failed" for operation in operations) == 20
    assert sum(operation.status == "applied" for operation in operations) == 1
    assert sum(operation.status == "rolled_back" for operation in operations) == 1


def test_runtime_acceptance_rejects_changed_incomplete_operation_hidden_by_limit(
    live_runtime_client,
):
    client, provider, store, initial = live_runtime_client
    clock = [datetime(2026, 8, 24, tzinfo=timezone.utc)]
    store._clock = lambda: clock[0]
    incomplete, created = store.reserve_runtime_operation(
        idempotency_key_hash="a" * 64,
        request_fingerprint="b" * 64,
        kind="apply",
        actor=AUTH[0],
        reason=smoke_admin_runtime.APPLY_OPERATION_REASON,
        expected_version=initial.version,
        draft_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    assert created is True
    assert incomplete.status == "incomplete"

    for index in range(smoke_admin_runtime.RUNTIME_OPERATION_HISTORY_LIMIT - 1):
        clock[0] += timedelta(seconds=1)
        response = client.post(
            "/admin-api/v1/runtime-config/apply",
            auth=AUTH,
            headers={
                "X-Admin-Intent": "apply-runtime-config",
                "Sec-Fetch-Site": "same-origin",
                "Idempotency-Key": f"issue47-visible-failed-history-{index}",
            },
            json={
                "draft_id": f"{index + 1:08x}-0000-4000-8000-100000000000",
                "expected_version": initial.version,
            },
        )
        assert response.status_code == 400
    assert store.list_runtime_operations(limit=20)[-1].operation_id == (
        incomplete.operation_id
    )

    class CompleteHiddenOperationClient:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.completed_hidden = False

        def get(self, path, **kwargs):
            return self.wrapped.get(path, **kwargs)

        def post(self, path, **kwargs):
            if (
                path == "/admin-api/v1/runtime-config/apply"
                and not self.completed_hidden
            ):
                self.completed_hidden = True
                clock[0] += timedelta(seconds=10)
                completed = store.append_runtime_event(
                    operation_id=incomplete.operation_id,
                    event_type="failed",
                    previous_version=initial.version,
                    current_version=initial.version,
                    failure_code="synthetic_hidden_completion",
                )
                assert completed.status == "failed"
            return self.wrapped.post(path, **kwargs)

    drifted_client = CompleteHiddenOperationClient(client)
    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            drifted_client,
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    assert drifted_client.completed_hidden is True
    assert captured.value.code == "runtime_apply_outcome_unknown"
    assert captured.value.restored is None
    assert captured.value.manual_recovery_required is True
    assert provider.snapshot().as_dict()["opensearch.max_retries"] == 0
    hidden = next(
        operation
        for operation in store.list_runtime_operations(limit=30)
        if operation.operation_id == incomplete.operation_id
    )
    assert hidden.status == "failed"
    assert not any(
        operation.kind == "rollback"
        for operation in store.list_runtime_operations(limit=30)
    )


def test_runtime_acceptance_allows_service_valid_noncanonical_history_draft_id(
    live_runtime_client,
):
    client, provider, store, initial = live_runtime_client
    response = client.post(
        "/admin-api/v1/runtime-config/apply",
        auth=AUTH,
        headers={
            "X-Admin-Intent": "apply-runtime-config",
            "Sec-Fetch-Site": "same-origin",
            "Idempotency-Key": "issue47-uppercase-history-draft-id",
        },
        json={
            "draft_id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA",
            "expected_version": initial.version,
        },
    )
    assert response.status_code == 400
    assert store.list_runtime_operations(limit=1)[0].draft_id == (
        "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
    )

    checks = smoke_admin_runtime.run_runtime_acceptance(
        client,
        username=AUTH[0],
        password=AUTH[1],
        business_api_token="business-api-token",
        expected_release_commit=EXPECTED_RELEASE_COMMIT,
        require_prometheus=False,
        require_journal_audit=False,
        check_probes=False,
        cooldown_seconds=0,
    )

    assert all(checks.values())
    assert provider.snapshot().as_dict() == initial.as_dict()
    assert {
        operation.status for operation in store.list_runtime_operations(limit=10)
    } == {"failed", "applied", "rolled_back"}


def test_config_draft_http_audit_reuses_the_response_request_id(
    live_runtime_client,
    caplog,
):
    client, _provider, _store, _initial = live_runtime_client
    request_id = "issue47-http-draft-audit-001"
    schema = client.get("/admin-api/v1/config-schema", auth=AUTH).json()
    caplog.set_level(logging.INFO)

    response = client.post(
        "/admin-api/v1/config-drafts",
        auth=AUTH,
        headers={
            "X-Admin-Intent": "create-config-draft",
            "X-Request-ID": request_id,
        },
        json={
            "baseline_fingerprint": schema["baseline_fingerprint"],
            "reason": "验证 HTTP 草案审计请求关联",
            "candidate_values": {"opensearch.max_retries": 0},
        },
    )

    assert response.status_code == 201
    assert response.headers["X-Request-ID"] == request_id
    record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "admin_config_completed"
        and getattr(record, "action", None) == "config_draft.create"
    )
    assert getattr(record, "request_id") == request_id
    assert getattr(record, "draft_id") == response.json()["id"]


@pytest.mark.parametrize(
    ("actor", "role", "scope", "window_seconds", "accepted"),
    (
        (AUTH[0], "admin", "journal_local", 3600, True),
        ("different-admin", "admin", "journal_local", 3600, False),
        (AUTH[0], "viewer", "journal_local", 3600, False),
        (AUTH[0], "admin", "current_process", 3600, False),
        (AUTH[0], "admin", "journal_local", 300, False),
    ),
)
def test_runtime_acceptance_audit_binds_query_and_identity(
    actor,
    role,
    scope,
    window_seconds,
    accepted,
):
    request_id = "issue47-runtime-audit-request"
    operation_id = "11111111-1111-1111-1111-111111111111"
    draft_id = "22222222-2222-2222-2222-222222222222"
    clock = [0.0]

    class AuditClient:
        def get(self, _path, **_kwargs):
            return httpx.Response(
                status_code=200,
                json={
                    "scope": scope,
                    "available": True,
                    "window_seconds": window_seconds,
                    "items": [
                        {
                            "event": "admin_runtime_config_completed",
                            "request_id": request_id,
                            "actor": actor,
                            "role": role,
                            "action": "runtime_config.apply",
                            "result": "applied",
                            "draft_id": draft_id,
                            "operation_id": operation_id,
                        }
                    ],
                },
            )

    acceptance = smoke_admin_runtime.RuntimeAcceptance(
        AuditClient(),
        username=AUTH[0],
        password=AUTH[1],
        business_api_token="business-api-token",
        expected_release_commit=EXPECTED_RELEASE_COMMIT,
        require_prometheus=False,
        require_journal_audit=True,
        check_probes=False,
        audit_timeout_seconds=0.01,
        cooldown_seconds=0,
        now=lambda: clock[0],
        sleeper=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    apply_response = httpx.Response(
        status_code=200,
        headers={"X-Request-ID": request_id},
    )

    if accepted:
        acceptance.audit(
            apply_response,
            event="admin_runtime_config_completed",
            action="runtime_config.apply",
            result="applied",
            draft_id=draft_id,
            operation_id=operation_id,
        )
        return

    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        acceptance.audit(
            apply_response,
            event="admin_runtime_config_completed",
            action="runtime_config.apply",
            result="applied",
            draft_id=draft_id,
            operation_id=operation_id,
        )

    assert captured.value.code == "runtime_config.apply_audit_missing"


def test_runtime_acceptance_rolls_back_when_post_apply_verification_fails(
    live_runtime_client,
    monkeypatch,
):
    client, provider, store, initial = live_runtime_client
    calls = 0

    def fail_once(_acceptance):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise smoke_admin_runtime.AcceptanceError("forced_post_apply_failure")

    monkeypatch.setattr(
        smoke_admin_runtime.RuntimeAcceptance,
        "probes",
        fail_once,
    )

    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            client,
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=True,
            cooldown_seconds=0,
        )

    assert captured.value.code == "forced_post_apply_failure"
    assert captured.value.restored is True
    assert captured.value.manual_recovery_required is False
    assert provider.snapshot().as_dict() == initial.as_dict()
    assert [
        operation.status for operation in store.list_runtime_operations(limit=10)
    ] == ["rolled_back", "applied"]


def test_runtime_acceptance_replays_unknown_apply_before_deciding_recovery(
    live_runtime_client,
):
    client, provider, store, initial = live_runtime_client

    class LostApplyResponseClient:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.release = Event()
            self.thread: Thread | None = None
            self.background_error: BaseException | None = None
            self.apply_calls: list[tuple[str | None, dict]] = []

        def get(self, path, **kwargs):
            response = self.wrapped.get(path, **kwargs)
            if (
                path == "/admin-api/v1/runtime-config"
                and self.thread is not None
                and not self.release.is_set()
            ):
                # The old one-shot discovery path observed this baseline response
                # before allowing the already-started request to complete.
                self.release.set()
            return response

        def post(self, path, **kwargs):
            if path != "/admin-api/v1/runtime-config/apply":
                return self.wrapped.post(path, **kwargs)
            headers = kwargs.get("headers", {})
            self.apply_calls.append(
                (headers.get("Idempotency-Key"), dict(kwargs.get("json", {})))
            )
            if self.thread is None:
                def finish_first_request():
                    try:
                        self.release.wait(timeout=5)
                        self.wrapped.post(path, **kwargs)
                    except BaseException as exc:  # pragma: no cover - asserted below
                        self.background_error = exc

                self.thread = Thread(target=finish_first_request, daemon=True)
                self.thread.start()
                raise httpx.ReadTimeout("simulated lost apply response")

            self.release.set()
            return self.wrapped.post(path, **kwargs)

        def finish(self):
            assert self.thread is not None
            self.release.set()
            self.thread.join(timeout=5)
            assert not self.thread.is_alive()

    uncertain_client = LostApplyResponseClient(client)
    try:
        with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
            smoke_admin_runtime.run_runtime_acceptance(
                uncertain_client,
                username=AUTH[0],
                password=AUTH[1],
                business_api_token="business-api-token",
                expected_release_commit=EXPECTED_RELEASE_COMMIT,
                require_prometheus=False,
                require_journal_audit=False,
                check_probes=False,
                cooldown_seconds=0,
            )
    finally:
        uncertain_client.finish()

    assert captured.value.code == "runtime_acceptance_failed"
    assert captured.value.restored is True
    assert captured.value.manual_recovery_required is False
    assert uncertain_client.background_error is None
    assert len(uncertain_client.apply_calls) == 2
    assert uncertain_client.apply_calls[0] == uncertain_client.apply_calls[1]
    assert provider.snapshot().as_dict() == initial.as_dict()
    assert [
        operation.status for operation in store.list_runtime_operations(limit=10)
    ] == ["rolled_back", "applied"]


def test_runtime_acceptance_does_not_treat_gateway_replay_error_as_terminal(
    live_runtime_client,
):
    client, provider, store, initial = live_runtime_client

    class GatewayUnknownApplyClient:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.apply_calls = 0

        def get(self, path, **kwargs):
            return self.wrapped.get(path, **kwargs)

        def post(self, path, **kwargs):
            if path != "/admin-api/v1/runtime-config/apply":
                return self.wrapped.post(path, **kwargs)
            self.apply_calls += 1
            if self.apply_calls == 1:
                raise httpx.ReadTimeout("simulated unknown first apply")
            return httpx.Response(status_code=502, json={"error": "gateway"})

    uncertain_client = GatewayUnknownApplyClient(client)
    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            uncertain_client,
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    assert captured.value.code == "runtime_apply_outcome_unknown"
    assert captured.value.restored is None
    assert captured.value.manual_recovery_required is True
    assert uncertain_client.apply_calls == 2
    assert provider.snapshot().as_dict() == initial.as_dict()
    assert store.list_runtime_operations(limit=10) == []


def test_runtime_acceptance_does_not_cleanup_after_failed_replay(
    live_runtime_client,
):
    client, provider, store, _initial = live_runtime_client

    class LostSuccessThenGatewayClient:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.apply_calls = 0

        def get(self, path, **kwargs):
            return self.wrapped.get(path, **kwargs)

        def post(self, path, **kwargs):
            if path != "/admin-api/v1/runtime-config/apply":
                return self.wrapped.post(path, **kwargs)
            self.apply_calls += 1
            if self.apply_calls == 1:
                response = self.wrapped.post(path, **kwargs)
                assert response.status_code == 200
                raise httpx.ReadTimeout("simulated lost successful apply response")
            return httpx.Response(status_code=502, json={"error": "gateway"})

    uncertain_client = LostSuccessThenGatewayClient(client)
    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            uncertain_client,
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    assert captured.value.code == "runtime_apply_outcome_unknown"
    assert captured.value.restored is None
    assert captured.value.manual_recovery_required is True
    assert uncertain_client.apply_calls == 2
    assert provider.snapshot().as_dict()["opensearch.max_retries"] == 0
    assert [
        operation.status for operation in store.list_runtime_operations(limit=10)
    ] == ["applied"]


@pytest.mark.parametrize(
    ("lose_first_response", "drift_kind"),
    (
        (False, "operation"),
        (True, "operation"),
        (False, "created-at"),
        (True, "created-at"),
        (False, "operation-history"),
        (True, "operation-history"),
    ),
    ids=(
        "direct-operation",
        "replayed-operation",
        "direct-created-at",
        "replayed-created-at",
        "direct-operation-history",
        "replayed-operation-history",
    ),
)
def test_runtime_acceptance_requires_matching_response_and_readback_evidence(
    live_runtime_client,
    lose_first_response,
    drift_kind,
):
    client, provider, store, _initial = live_runtime_client

    class OperationMismatchClient:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.apply_calls = 0

        def get(self, path, **kwargs):
            return self.wrapped.get(path, **kwargs)

        def post(self, path, **kwargs):
            if path != "/admin-api/v1/runtime-config/apply":
                return self.wrapped.post(path, **kwargs)
            self.apply_calls += 1
            response = self.wrapped.post(path, **kwargs)
            assert response.status_code == 200
            if lose_first_response and self.apply_calls == 1:
                raise httpx.ReadTimeout("simulated lost successful apply response")

            draft_id = kwargs["json"]["draft_id"]
            mismatched = response.json()
            if drift_kind == "operation":
                mismatched["recent_operations"] = [
                    {
                        **operation,
                        "operation_id": "33333333-3333-3333-3333-333333333333",
                    }
                    if operation.get("kind") == "apply"
                    and operation.get("draft_id") == draft_id
                    else operation
                    for operation in mismatched["recent_operations"]
                ]
            elif drift_kind == "created-at":
                mismatched["created_at"] = "2000-01-01T00:00:00Z"
            else:
                existing_operation = mismatched["recent_operations"][0]
                mismatched["recent_operations"].append(
                    {
                        **existing_operation,
                        "operation_id": "44444444-4444-4444-4444-444444444444",
                        "status": "failed",
                        "current_version": None,
                        "draft_id": None,
                        "failure_code": "synthetic_concurrent_failure",
                    }
                )
            return httpx.Response(
                status_code=200,
                headers=dict(response.headers),
                json=mismatched,
            )

    uncertain_client = OperationMismatchClient(client)
    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            uncertain_client,
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    assert captured.value.code == "runtime_apply_outcome_unknown"
    assert captured.value.restored is None
    assert captured.value.manual_recovery_required is True
    assert uncertain_client.apply_calls == 2
    assert provider.snapshot().as_dict()["opensearch.max_retries"] == 0
    assert [
        operation.status for operation in store.list_runtime_operations(limit=10)
    ] == ["applied"]


@pytest.mark.parametrize(
    "drift_point",
    (
        "before-apply-evidence",
        "before-post-apply-readback",
        "before-rollback",
    ),
)
def test_runtime_acceptance_rejects_concurrent_failed_operation_history_drift(
    live_runtime_client,
    drift_point,
):
    client, provider, store, initial = live_runtime_client

    class ConcurrentFailedOperationClient:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.own_apply_calls = 0
            self.applied_runtime_reads = 0
            self.injected = False

        def inject_failed_operation(self):
            assert self.injected is False
            self.injected = True
            current = self.wrapped.get(
                "/admin-api/v1/runtime-config",
                auth=AUTH,
            ).json()
            response = self.wrapped.post(
                "/admin-api/v1/runtime-config/apply",
                auth=AUTH,
                headers={
                    "X-Admin-Intent": "apply-runtime-config",
                    "Sec-Fetch-Site": "same-origin",
                    "Idempotency-Key": f"issue47-concurrent-failure-{drift_point}",
                },
                json={
                    "draft_id": "88888888-8888-8888-8888-888888888888",
                    "expected_version": current["version"],
                },
            )
            assert response.status_code == 400

        def get(self, path, **kwargs):
            if path == "/admin-api/v1/runtime-config" and self.own_apply_calls:
                self.applied_runtime_reads += 1
                if (
                    drift_point == "before-post-apply-readback"
                    and self.applied_runtime_reads == 2
                    and not self.injected
                ):
                    self.inject_failed_operation()
            return self.wrapped.get(path, **kwargs)

        def post(self, path, **kwargs):
            if path == "/admin-api/v1/runtime-config/rollback":
                if drift_point == "before-rollback" and not self.injected:
                    self.inject_failed_operation()
                return self.wrapped.post(path, **kwargs)
            if path != "/admin-api/v1/runtime-config/apply":
                return self.wrapped.post(path, **kwargs)

            self.own_apply_calls += 1
            response = self.wrapped.post(path, **kwargs)
            assert response.status_code == 200
            if (
                drift_point == "before-apply-evidence"
                and self.own_apply_calls == 1
            ):
                self.inject_failed_operation()
                current = self.wrapped.get(
                    "/admin-api/v1/runtime-config",
                    auth=AUTH,
                )
                return httpx.Response(
                    status_code=200,
                    headers=dict(response.headers),
                    json=current.json(),
                )
            return response

    drifted_client = ConcurrentFailedOperationClient(client)
    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            drifted_client,
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    assert drifted_client.injected is True
    operations = store.list_runtime_operations(limit=10)
    assert any(operation.status == "failed" for operation in operations)
    if drift_point == "before-apply-evidence":
        assert captured.value.code == "runtime_apply_outcome_unknown"
        assert captured.value.restored is None
        assert captured.value.manual_recovery_required is True
        assert provider.snapshot().as_dict()["opensearch.max_retries"] == 0
        assert not any(operation.kind == "rollback" for operation in operations)
    else:
        assert captured.value.code == "runtime_rollback_operation_history_transition"
        assert captured.value.restored is True
        assert captured.value.manual_recovery_required is False
        assert provider.snapshot().as_dict() == initial.as_dict()
        assert any(operation.status == "rolled_back" for operation in operations)


@pytest.mark.parametrize(
    "history_drift",
    ("duplicate-id", "impossible-state", "reverse-order"),
)
def test_runtime_acceptance_rejects_invalid_preexisting_operation_history(
    live_runtime_client,
    history_drift,
):
    client, provider, store, initial = live_runtime_client
    preexisting = client.post(
        "/admin-api/v1/runtime-config/apply",
        auth=AUTH,
        headers={
            "X-Admin-Intent": "apply-runtime-config",
            "Sec-Fetch-Site": "same-origin",
            "Idempotency-Key": f"issue47-preexisting-{history_drift}",
        },
        json={
            "draft_id": "99999999-9999-9999-9999-999999999999",
            "expected_version": initial.version,
        },
    )
    assert preexisting.status_code == 400

    class InvalidHistoryClient:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.distortions = 0

        def get(self, path, **kwargs):
            return self.distort(path, self.wrapped.get(path, **kwargs))

        def post(self, path, **kwargs):
            return self.distort(path, self.wrapped.post(path, **kwargs))

        def distort(self, path, response):
            if path != "/admin-api/v1/runtime-config" and path != (
                "/admin-api/v1/runtime-config/apply"
            ):
                return response
            if response.status_code != 200:
                return response
            body = response.json()
            operations = body.get("recent_operations", [])
            if history_drift == "duplicate-id" and operations:
                body["recent_operations"] = [operations[0], *operations]
            elif history_drift == "impossible-state" and operations:
                body["recent_operations"] = [
                    {
                        **operations[0],
                        "status": "rolled_back",
                        "failure_code": None,
                    },
                    *operations[1:],
                ]
            elif history_drift == "reverse-order" and len(operations) >= 2:
                body["recent_operations"] = list(reversed(operations))
            else:
                return response
            self.distortions += 1
            return httpx.Response(
                status_code=response.status_code,
                headers=dict(response.headers),
                json=body,
            )

    invalid_client = InvalidHistoryClient(client)
    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            invalid_client,
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    assert invalid_client.distortions >= 1
    operations = store.list_runtime_operations(limit=10)
    if history_drift in {"duplicate-id", "impossible-state"}:
        assert captured.value.code == "runtime_initial_response_contract"
        assert provider.snapshot().as_dict() == initial.as_dict()
        assert [operation.status for operation in operations] == ["failed"]
    else:
        assert captured.value.code == "runtime_apply_outcome_unknown"
        assert captured.value.restored is None
        assert captured.value.manual_recovery_required is True
        assert provider.snapshot().as_dict()["opensearch.max_retries"] == 0
        assert {operation.status for operation in operations} == {
            "applied",
            "failed",
        }
        assert not any(operation.kind == "rollback" for operation in operations)


@pytest.mark.parametrize(
    "lose_first_response",
    (False, True),
    ids=("direct", "replayed"),
)
@pytest.mark.parametrize(
    "drift_kind",
    (
        "actor",
        "reason",
        "expected-version",
        "previous-version",
        "failure-code",
        "created-before",
        "created-after",
        "stale-fingerprint",
        "wrong-fingerprint",
        "version-equals-fingerprint",
        "writes-disabled",
        "wrong-values",
        "duplicate",
    ),
)
def test_runtime_acceptance_binds_apply_operation_semantics(
    live_runtime_client,
    lose_first_response,
    drift_kind,
):
    client, provider, store, initial = live_runtime_client

    def rewrite(operation):
        if drift_kind in {
            "duplicate",
            "stale-fingerprint",
            "wrong-fingerprint",
            "version-equals-fingerprint",
            "writes-disabled",
            "wrong-values",
        }:
            return operation
        field, value = {
            "actor": ("actor", "different-admin"),
            "reason": ("reason", "different apply reason"),
            "expected-version": ("expected_version", "f" * 64),
            "previous-version": ("previous_version", "e" * 64),
            "failure-code": ("failure_code", "synthetic_failure"),
            "created-before": ("created_at", "2000-01-01T00:00:00Z"),
            "created-after": ("created_at", "2999-01-01T00:00:00Z"),
        }[drift_kind]
        return {
            **operation,
            field: value,
        }

    def distort(response):
        rewritten = _rewrite_runtime_operation(
            response,
            kind="apply",
            status="applied",
            rewrite=rewrite,
        )
        if drift_kind == "stale-fingerprint":
            body = rewritten.json()
            body["fingerprint"] = initial.fingerprint
            return httpx.Response(
                status_code=rewritten.status_code,
                headers=dict(rewritten.headers),
                json=body,
            )
        if drift_kind == "wrong-fingerprint":
            body = rewritten.json()
            body["fingerprint"] = "0" * 64
            return httpx.Response(
                status_code=rewritten.status_code,
                headers=dict(rewritten.headers),
                json=body,
            )
        if drift_kind == "version-equals-fingerprint":
            body = rewritten.json()
            body["version"] = body["fingerprint"]
            body["recent_operations"] = [
                {
                    **operation,
                    "current_version": body["version"],
                }
                if operation.get("kind") == "apply"
                and operation.get("status") == "applied"
                else operation
                for operation in body["recent_operations"]
            ]
            return httpx.Response(
                status_code=rewritten.status_code,
                headers=dict(rewritten.headers),
                json=body,
            )
        if drift_kind == "writes-disabled":
            body = rewritten.json()
            body["writes_enabled"] = False
            return httpx.Response(
                status_code=rewritten.status_code,
                headers=dict(rewritten.headers),
                json=body,
            )
        if drift_kind == "wrong-values":
            body = rewritten.json()
            body["values"]["opensearch.max_retries"] = 999
            return httpx.Response(
                status_code=rewritten.status_code,
                headers=dict(rewritten.headers),
                json=body,
            )
        if drift_kind != "duplicate":
            return rewritten
        body = rewritten.json()
        duplicate = {
            **next(
                operation
                for operation in body["recent_operations"]
                if operation.get("kind") == "apply"
                and operation.get("status") == "applied"
            ),
            "operation_id": "55555555-5555-5555-5555-555555555555",
        }
        body["recent_operations"].append(duplicate)
        return httpx.Response(
            status_code=rewritten.status_code,
            headers=dict(rewritten.headers),
            json=body,
        )

    class SemanticDriftClient:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.apply_calls = 0

        def get(self, path, **kwargs):
            response = self.wrapped.get(path, **kwargs)
            if (
                path == "/admin-api/v1/runtime-config"
                and response.json().get("source") == "runtime_override"
            ):
                return distort(response)
            return response

        def post(self, path, **kwargs):
            response = self.wrapped.post(path, **kwargs)
            if path != "/admin-api/v1/runtime-config/apply":
                return response
            self.apply_calls += 1
            assert response.status_code == 200
            if lose_first_response and self.apply_calls == 1:
                raise httpx.ReadTimeout("simulated lost successful apply response")
            return distort(response)

    uncertain_client = SemanticDriftClient(client)
    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            uncertain_client,
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    assert captured.value.code == "runtime_apply_outcome_unknown"
    assert captured.value.restored is None
    assert captured.value.manual_recovery_required is True
    assert uncertain_client.apply_calls == 2
    assert provider.snapshot().as_dict()["opensearch.max_retries"] == 0
    assert [
        operation.status for operation in store.list_runtime_operations(limit=10)
    ] == ["applied"]


@pytest.mark.parametrize(
    ("failure_kind", "expected_code", "rollback_reached_server"),
    (
        ("non-success", "runtime_rollback_status", False),
        ("invalid-json", "runtime_rollback_json", True),
        ("missing-cache-header", "runtime_rollback_cache", True),
        ("lost-response", "runtime_rollback_failed", True),
    ),
)
def test_runtime_acceptance_requires_manual_recovery_for_unknown_rollback_outcome(
    live_runtime_client,
    failure_kind,
    expected_code,
    rollback_reached_server,
):
    client, provider, store, initial = live_runtime_client

    class UnknownRollbackOutcomeClient:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def get(self, path, **kwargs):
            return self.wrapped.get(path, **kwargs)

        def post(self, path, **kwargs):
            if path != "/admin-api/v1/runtime-config/rollback":
                return self.wrapped.post(path, **kwargs)
            if failure_kind == "non-success":
                return httpx.Response(502, json={"detail": "rollback unavailable"})

            response = self.wrapped.post(path, **kwargs)
            assert response.status_code == 200
            if failure_kind == "invalid-json":
                return httpx.Response(
                    200,
                    headers={"Cache-Control": "no-store"},
                    content=b"{",
                )
            if failure_kind == "missing-cache-header":
                return httpx.Response(200, json=response.json())
            raise httpx.ReadTimeout("simulated lost successful rollback response")

    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            UnknownRollbackOutcomeClient(client),
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    assert captured.value.code == expected_code
    assert captured.value.restored is None
    assert captured.value.manual_recovery_required is True
    assert provider.snapshot().as_dict() == (
        initial.as_dict()
        if rollback_reached_server
        else {**initial.as_dict(), "opensearch.max_retries": 0}
    )
    assert [
        operation.status for operation in store.list_runtime_operations(limit=10)
    ] == (["rolled_back", "applied"] if rollback_reached_server else ["applied"])


@pytest.mark.parametrize(
    "drift_kind",
    (
        "actor",
        "reason",
        "expected-version",
        "previous-version",
        "failure-code",
        "created-before",
        "created-after",
        "duplicate",
        "reused-generation",
        "missing-generation",
    ),
)
def test_runtime_acceptance_binds_rollback_operation_semantics(
    live_runtime_client,
    drift_kind,
):
    client, provider, store, initial = live_runtime_client

    def rewrite(operation):
        if drift_kind == "duplicate":
            return operation
        if drift_kind == "reused-generation":
            return {**operation, "current_version": initial.version}
        if drift_kind == "missing-generation":
            return {**operation, "current_version": None}
        if drift_kind == "expected-version":
            return {
                **operation,
                "expected_version": "f" * 64,
                "previous_version": "f" * 64,
            }
        if drift_kind == "previous-version":
            return {
                **operation,
                "expected_version": "e" * 64,
                "previous_version": "e" * 64,
            }
        field, value = {
            "actor": ("actor", "different-admin"),
            "reason": ("reason", "different rollback reason"),
            "failure-code": ("failure_code", "synthetic_failure"),
            "created-before": ("created_at", "2000-01-01T00:00:00Z"),
            "created-after": ("created_at", "2999-01-01T00:00:00Z"),
        }[drift_kind]
        return {**operation, field: value}

    def distort(response):
        rewritten = _rewrite_runtime_operation(
            response,
            kind="rollback",
            status="rolled_back",
            rewrite=rewrite,
        )
        body = rewritten.json()
        if drift_kind == "duplicate":
            duplicate = {
                **next(
                    operation
                    for operation in body["recent_operations"]
                    if operation.get("kind") == "rollback"
                    and operation.get("status") == "rolled_back"
                ),
                "operation_id": "66666666-6666-6666-6666-666666666666",
            }
            body["recent_operations"].append(duplicate)
        elif drift_kind == "reused-generation":
            body["version"] = initial.version
        elif drift_kind == "missing-generation":
            body["version"] = None
        if drift_kind in {"created-before", "created-after", "duplicate"}:
            body["recent_operations"].sort(
                key=lambda operation: (
                    operation["created_at"],
                    operation["operation_id"],
                ),
                reverse=True,
            )
        return httpx.Response(
            status_code=rewritten.status_code,
            headers=dict(rewritten.headers),
            json=body,
        )

    class RollbackSemanticDriftClient:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.rollback_completed = False

        def get(self, path, **kwargs):
            response = self.wrapped.get(path, **kwargs)
            if path == "/admin-api/v1/runtime-config" and self.rollback_completed:
                return distort(response)
            return response

        def post(self, path, **kwargs):
            response = self.wrapped.post(path, **kwargs)
            if path == "/admin-api/v1/runtime-config/rollback":
                assert response.status_code == 200
                self.rollback_completed = True
                return distort(response)
            return response

    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            RollbackSemanticDriftClient(client),
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    if drift_kind in {"reused-generation", "missing-generation"}:
        assert captured.value.code == "runtime_values_not_restored"
        assert captured.value.restored is False
        assert captured.value.manual_recovery_required is True
    elif drift_kind == "duplicate":
        assert captured.value.code == "runtime_rollback_operation_not_unique"
        assert captured.value.restored is True
        assert captured.value.manual_recovery_required is False
    elif drift_kind == "failure-code":
        assert captured.value.code == "runtime_rollback_response_contract"
        assert captured.value.restored is True
        assert captured.value.manual_recovery_required is False
    else:
        assert captured.value.code == "runtime_rollback_operation_contract"
        assert captured.value.restored is True
        assert captured.value.manual_recovery_required is False
    assert provider.snapshot().as_dict() == initial.as_dict()
    assert [
        operation.status for operation in store.list_runtime_operations(limit=10)
    ] == ["rolled_back", "applied"]


def test_runtime_acceptance_marks_post_rollback_transport_failure_as_restored(
    live_runtime_client,
    monkeypatch,
):
    client, provider, store, initial = live_runtime_client
    calls = 0

    def fail_after_rollback(_acceptance):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise httpx.ReadTimeout("simulated post-rollback probe timeout")

    monkeypatch.setattr(
        smoke_admin_runtime.RuntimeAcceptance,
        "probes",
        fail_after_rollback,
    )

    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            client,
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=True,
            cooldown_seconds=0,
        )

    assert captured.value.code == "runtime_rollback_postcheck_failed"
    assert captured.value.restored is True
    assert captured.value.manual_recovery_required is False
    assert provider.snapshot().as_dict() == initial.as_dict()
    assert [
        operation.status for operation in store.list_runtime_operations(limit=10)
    ] == ["rolled_back", "applied"]


def test_runtime_acceptance_requires_independent_rollback_readback(
    live_runtime_client,
):
    client, provider, store, initial = live_runtime_client

    class ForgedRollbackClient:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def get(self, path, **kwargs):
            return self.wrapped.get(path, **kwargs)

        def post(self, path, **kwargs):
            if path != "/admin-api/v1/runtime-config/rollback":
                return self.wrapped.post(path, **kwargs)
            applied = self.wrapped.get(
                "/admin-api/v1/runtime-config",
                auth=AUTH,
            ).json()
            fake_version = "f" * 64
            forged = {
                **applied,
                "version": fake_version,
                "fingerprint": initial.fingerprint,
                "source": "runtime_override",
                "rollback_version": applied["version"],
                "values": initial.as_dict(),
                "recent_operations": [
                    {
                        "operation_id": "77777777-7777-7777-7777-777777777777",
                        "kind": "rollback",
                        "status": "rolled_back",
                        "created_at": applied["created_at"],
                        "actor": AUTH[0],
                        "reason": "forged rollback response",
                        "expected_version": applied["version"],
                        "previous_version": applied["version"],
                        "current_version": fake_version,
                        "draft_id": None,
                        "failure_code": None,
                    },
                    *applied["recent_operations"],
                ],
            }
            return httpx.Response(
                status_code=200,
                headers={"Cache-Control": "no-store"},
                json=forged,
            )

    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            ForgedRollbackClient(client),
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    assert captured.value.code == "runtime_values_not_restored"
    assert captured.value.restored is False
    assert captured.value.manual_recovery_required is True
    assert provider.snapshot().as_dict()["opensearch.max_retries"] == 0
    assert [
        operation.status for operation in store.list_runtime_operations(limit=10)
    ] == ["applied"]


def test_runtime_acceptance_rechecks_final_state_after_rollback_probes(
    live_runtime_client,
    monkeypatch,
):
    client, provider, store, _initial = live_runtime_client
    probe_calls = 0

    def apply_concurrent_change_on_rollback_probes(_acceptance):
        nonlocal probe_calls
        probe_calls += 1
        if probe_calls != 2:
            return
        schema = client.get("/admin-api/v1/config-schema", auth=AUTH).json()
        draft_response = client.post(
            "/admin-api/v1/config-drafts",
            auth=AUTH,
            headers={
                "X-Admin-Intent": "create-config-draft",
                "Sec-Fetch-Site": "same-origin",
            },
            json={
                "baseline_fingerprint": schema["runtime_version"],
                "reason": "Concurrent change during rollback probe stage",
                "candidate_values": {"opensearch.max_retries": 0},
            },
        )
        assert draft_response.status_code == 201
        draft = draft_response.json()
        assert draft["status"] == "validated"
        apply_response = client.post(
            "/admin-api/v1/runtime-config/apply",
            auth=AUTH,
            headers={
                "X-Admin-Intent": "apply-runtime-config",
                "Sec-Fetch-Site": "same-origin",
                "Idempotency-Key": "issue47-concurrent-post-rollback-apply",
            },
            json={
                "draft_id": draft["id"],
                "expected_version": schema["runtime_version"],
            },
        )
        assert apply_response.status_code == 200

    monkeypatch.setattr(
        smoke_admin_runtime.RuntimeAcceptance,
        "probes",
        apply_concurrent_change_on_rollback_probes,
    )

    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            client,
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=True,
            cooldown_seconds=0,
        )

    assert probe_calls == 2
    assert captured.value.code == "runtime_final_state_changed"
    assert captured.value.restored is False
    assert captured.value.manual_recovery_required is True
    assert provider.snapshot().as_dict()["opensearch.max_retries"] == 0
    assert [
        operation.status for operation in store.list_runtime_operations(limit=10)
    ] == ["applied", "rolled_back", "applied"]


def test_runtime_acceptance_final_read_overrides_earlier_rollback_evidence_error(
    live_runtime_client,
):
    client, provider, store, _initial = live_runtime_client

    class StaleRollbackReadbackClient:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.rollback_completed = False
            self.stale_readback_returned = False

        def get(self, path, **kwargs):
            response = self.wrapped.get(path, **kwargs)
            if (
                path != "/admin-api/v1/runtime-config"
                or not self.rollback_completed
                or self.stale_readback_returned
            ):
                return response

            self.stale_readback_returned = True
            stale = response.json()
            schema = self.wrapped.get(
                "/admin-api/v1/config-schema",
                auth=AUTH,
            ).json()
            draft_response = self.wrapped.post(
                "/admin-api/v1/config-drafts",
                auth=AUTH,
                headers={
                    "X-Admin-Intent": "create-config-draft",
                    "Sec-Fetch-Site": "same-origin",
                },
                json={
                    "baseline_fingerprint": schema["runtime_version"],
                    "reason": "Concurrent apply behind stale rollback readback",
                    "candidate_values": {"opensearch.max_retries": 0},
                },
            )
            assert draft_response.status_code == 201
            draft = draft_response.json()
            apply_response = self.wrapped.post(
                "/admin-api/v1/runtime-config/apply",
                auth=AUTH,
                headers={
                    "X-Admin-Intent": "apply-runtime-config",
                    "Sec-Fetch-Site": "same-origin",
                    "Idempotency-Key": "issue47-stale-rollback-readback-apply",
                },
                json={
                    "draft_id": draft["id"],
                    "expected_version": schema["runtime_version"],
                },
            )
            assert apply_response.status_code == 200
            stale["recent_operations"] = [
                {
                    **operation,
                    "actor": "different-admin",
                }
                if operation.get("kind") == "rollback"
                and operation.get("status") == "rolled_back"
                else operation
                for operation in stale["recent_operations"]
            ]
            return httpx.Response(
                status_code=response.status_code,
                headers=dict(response.headers),
                json=stale,
            )

        def post(self, path, **kwargs):
            response = self.wrapped.post(path, **kwargs)
            if path == "/admin-api/v1/runtime-config/rollback":
                assert response.status_code == 200
                self.rollback_completed = True
            return response

    stale_client = StaleRollbackReadbackClient(client)
    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            stale_client,
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    assert stale_client.stale_readback_returned is True
    assert captured.value.code == "runtime_final_state_changed"
    assert captured.value.restored is False
    assert captured.value.manual_recovery_required is True
    assert provider.snapshot().as_dict()["opensearch.max_retries"] == 0
    assert [
        operation.status for operation in store.list_runtime_operations(limit=10)
    ] == ["applied", "rolled_back", "applied"]


def test_runtime_acceptance_defers_cooldown_interrupt_until_values_are_restored(
    live_runtime_client,
):
    client, provider, store, initial = live_runtime_client
    settings = app.dependency_overrides[get_settings]()
    clock = [0.0]
    app.state.runtime_config_controller = RuntimeConfigController(
        settings=settings,
        provider=provider,
        store=store,
        verifier=_Verifier(),
        mutation_min_interval_seconds=10,
        clock=lambda: clock[0],
    )
    sleep_calls = 0

    def interrupt_then_finish_cooldown(seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 1:
            raise KeyboardInterrupt
        clock[0] += seconds

    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            client,
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=10.5,
            now=lambda: clock[0],
            sleeper=interrupt_then_finish_cooldown,
        )

    assert captured.value.code == "runtime_rollback_interrupted"
    assert captured.value.restored is True
    assert captured.value.manual_recovery_required is False
    assert sleep_calls == 2
    assert clock[0] == 10.5
    assert provider.snapshot().as_dict() == initial.as_dict()
    assert [
        operation.status for operation in store.list_runtime_operations(limit=10)
    ] == ["rolled_back", "applied"]


def test_runtime_acceptance_recovers_when_apply_success_is_interrupted(
    live_runtime_client,
):
    client, provider, store, initial = live_runtime_client

    class InterruptedApplyClient:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.interrupted = False

        def get(self, path, **kwargs):
            return self.wrapped.get(path, **kwargs)

        def post(self, path, **kwargs):
            response = self.wrapped.post(path, **kwargs)
            if (
                path == "/admin-api/v1/runtime-config/apply"
                and not self.interrupted
            ):
                self.interrupted = True
                raise KeyboardInterrupt
            return response

    interrupted_client = InterruptedApplyClient(client)
    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            interrupted_client,
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    assert captured.value.code == "runtime_acceptance_interrupted"
    assert captured.value.restored is True
    assert captured.value.manual_recovery_required is False
    assert provider.snapshot().as_dict() == initial.as_dict()
    assert [
        operation.status for operation in store.list_runtime_operations(limit=10)
    ] == ["rolled_back", "applied"]


def test_runtime_acceptance_never_rolls_back_an_uncorrelated_apply(
    live_runtime_client,
):
    client, provider, store, initial = live_runtime_client
    schema = client.get("/admin-api/v1/config-schema", auth=AUTH).json()
    other_draft = client.post(
        "/admin-api/v1/config-drafts",
        auth=AUTH,
        headers={
            "X-Admin-Intent": "create-config-draft",
            "Sec-Fetch-Site": "same-origin",
        },
        json={
            "baseline_fingerprint": schema["baseline_fingerprint"],
            "reason": "Independent legitimate runtime change",
            "candidate_values": {"opensearch.retry_backoff_seconds": 0.0},
        },
    ).json()
    assert other_draft["status"] == "validated"

    class UncorrelatedApplyClient:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.replaced = False

        def get(self, path, **kwargs):
            return self.wrapped.get(path, **kwargs)

        def post(self, path, **kwargs):
            if path != "/admin-api/v1/runtime-config/apply" or self.replaced:
                return self.wrapped.post(path, **kwargs)
            self.replaced = True
            headers = dict(kwargs.get("headers", {}))
            headers["Idempotency-Key"] = "issue47-independent-apply-001"
            return self.wrapped.post(
                path,
                auth=kwargs.get("auth"),
                headers=headers,
                json={
                    "draft_id": other_draft["id"],
                    "expected_version": initial.version,
                },
            )

    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            UncorrelatedApplyClient(client),
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    assert captured.value.code == "runtime_apply_outcome_unknown"
    assert captured.value.restored is None
    assert captured.value.manual_recovery_required is True
    assert provider.snapshot().as_dict()["opensearch.retry_backoff_seconds"] == 0.0
    operations = store.list_runtime_operations(limit=10)
    assert any(
        operation.status == "applied" and operation.draft_id == other_draft["id"]
        for operation in operations
    )
    assert not any(operation.kind == "rollback" for operation in operations)


@pytest.mark.parametrize(
    "unknown_first_apply",
    (False, True),
    ids=("direct-apply", "replayed-apply"),
)
def test_runtime_acceptance_requires_independently_correlated_apply_readback(
    live_runtime_client,
    unknown_first_apply,
):
    client, provider, store, initial = live_runtime_client
    schema = client.get("/admin-api/v1/config-schema", auth=AUTH).json()
    other_draft = client.post(
        "/admin-api/v1/config-drafts",
        auth=AUTH,
        headers={
            "X-Admin-Intent": "create-config-draft",
            "Sec-Fetch-Site": "same-origin",
        },
        json={
            "baseline_fingerprint": schema["baseline_fingerprint"],
            "reason": "Independent change during apply reconciliation",
            "candidate_values": {"opensearch.max_retries": 0},
        },
    ).json()
    assert other_draft["status"] == "validated"

    class MisdirectedApplyClient:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.apply_calls = 0

        def get(self, path, **kwargs):
            return self.wrapped.get(path, **kwargs)

        def post(self, path, **kwargs):
            if path != "/admin-api/v1/runtime-config/apply":
                return self.wrapped.post(path, **kwargs)
            self.apply_calls += 1
            if unknown_first_apply and self.apply_calls == 1:
                raise httpx.ReadTimeout("simulated unknown first apply")
            misdirected_call = 2 if unknown_first_apply else 1
            if self.apply_calls != misdirected_call:
                return self.wrapped.post(path, **kwargs)

            smoke_draft_id = kwargs["json"]["draft_id"]
            actual = self.wrapped.post(
                path,
                auth=kwargs.get("auth"),
                headers={
                    **kwargs.get("headers", {}),
                    "Idempotency-Key": "issue47-independent-reconcile-apply",
                },
                json={
                    "draft_id": other_draft["id"],
                    "expected_version": initial.version,
                },
            )
            misdirected = actual.json()
            misdirected["recent_operations"] = [
                {
                    **operation,
                    "draft_id": smoke_draft_id,
                }
                if operation.get("draft_id") == other_draft["id"]
                else operation
                for operation in misdirected["recent_operations"]
            ]
            return httpx.Response(
                status_code=200,
                headers=dict(actual.headers),
                json=misdirected,
            )

    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            MisdirectedApplyClient(client),
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    assert captured.value.code == "runtime_apply_outcome_unknown"
    assert captured.value.restored is None
    assert captured.value.manual_recovery_required is True
    assert provider.snapshot().as_dict()["opensearch.max_retries"] == 0
    operations = store.list_runtime_operations(limit=10)
    assert any(
        operation.status == "applied" and operation.draft_id == other_draft["id"]
        for operation in operations
    )
    assert not any(operation.kind == "rollback" for operation in operations)


def test_runtime_acceptance_rejects_extra_draft_changes_before_apply(
    live_runtime_client,
):
    client, provider, store, initial = live_runtime_client

    class ExtraDraftChangeClient:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.apply_called = False

        def get(self, path, **kwargs):
            return self.wrapped.get(path, **kwargs)

        def post(self, path, **kwargs):
            if path == "/admin-api/v1/runtime-config/apply":
                self.apply_called = True
            response = self.wrapped.post(path, **kwargs)
            if (
                path == "/admin-api/v1/config-drafts"
                and response.status_code == 201
                and response.json().get("status") == "validated"
            ):
                body = response.json()
                body["candidate_values"]["opensearch.retry_backoff_seconds"] = 0.2
                body["diff"]["opensearch.retry_backoff_seconds"] = {
                    "old": 0.1,
                    "new": 0.2,
                    "apply_mode": "runtime_reload",
                }
                return httpx.Response(
                    status_code=response.status_code,
                    headers=response.headers,
                    json=body,
                )
            return response

    tampered_client = ExtraDraftChangeClient(client)
    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            tampered_client,
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    assert captured.value.code == "valid_draft_rejected"
    assert tampered_client.apply_called is False
    assert provider.snapshot().as_dict() == initial.as_dict()
    assert store.list_runtime_operations(limit=10) == []


def test_runtime_acceptance_rejects_wrong_expected_release_before_mutation(
    live_runtime_client,
):
    client, provider, store, initial = live_runtime_client

    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            client,
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit="deadbee",
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    assert captured.value.code == "release_identity_mismatch"
    assert provider.snapshot().as_dict() == initial.as_dict()
    assert store.list_runtime_operations(limit=10) == []


@pytest.mark.parametrize("field", ("service_version", "tag", "instance_id"))
def test_runtime_acceptance_rejects_placeholder_release_identity_before_mutation(
    live_runtime_client,
    field,
):
    client, provider, store, initial = live_runtime_client

    class PlaceholderReleaseClient:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.mutation_calls = []

        def get(self, path, **kwargs):
            response = self.wrapped.get(path, **kwargs)
            if path == "/admin-api/v1/status" and response.status_code == 200:
                body = response.json()
                body["release"][field] = "unknown"
                return httpx.Response(
                    status_code=response.status_code,
                    headers=response.headers,
                    json=body,
                )
            return response

        def post(self, path, **kwargs):
            if path.startswith("/admin-api/v1/config") or path.startswith(
                "/admin-api/v1/runtime-config"
            ):
                self.mutation_calls.append(path)
            return self.wrapped.post(path, **kwargs)

    placeholder_client = PlaceholderReleaseClient(client)
    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            placeholder_client,
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    assert captured.value.code == "release_identity_mismatch"
    assert placeholder_client.mutation_calls == []
    assert provider.snapshot().as_dict() == initial.as_dict()
    assert store.list_runtime_operations(limit=10) == []


@pytest.mark.parametrize("expected_release_commit", ("", "unknown", "not-a-commit"))
def test_runtime_acceptance_rejects_invalid_expected_release_before_any_request(
    expected_release_commit,
):
    class NoRequestClient:
        def get(self, *_args, **_kwargs):  # pragma: no cover - must not run
            raise AssertionError("unexpected request")

        def post(self, *_args, **_kwargs):  # pragma: no cover - must not run
            raise AssertionError("unexpected request")

    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            NoRequestClient(),
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=expected_release_commit,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    assert captured.value.code == "expected_release_commit_invalid"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("registry_version", "unexpected-registry-v999"),
        ("release_commit", "deadbee"),
        ("apply_mode_contract", "unexpected-apply-contract"),
    ),
)
def test_runtime_acceptance_rejects_schema_identity_drift_before_mutation(
    live_runtime_client,
    field,
    value,
):
    client, provider, store, initial = live_runtime_client

    class RegistryDriftClient:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.mutation_calls = []

        def get(self, path, **kwargs):
            response = self.wrapped.get(path, **kwargs)
            if path == "/admin-api/v1/config-schema":
                body = response.json()
                body[field] = value
                return httpx.Response(
                    status_code=response.status_code,
                    headers=response.headers,
                    json=body,
                )
            return response

        def post(self, path, **kwargs):
            if path.startswith("/admin-api/v1/config") or path.startswith(
                "/admin-api/v1/runtime-config"
            ):
                self.mutation_calls.append(path)
            return self.wrapped.post(path, **kwargs)

    drifted_client = RegistryDriftClient(client)
    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            drifted_client,
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    assert captured.value.code == "config_schema_release_contract"
    assert drifted_client.mutation_calls == []
    assert provider.snapshot().as_dict() == initial.as_dict()
    assert store.list_runtime_operations(limit=10) == []


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("minimum", 1),
        ("maximum", 2),
        ("current_value", 0),
        ("current_value", True),
    ),
)
def test_runtime_acceptance_rejects_parameter_contract_drift_before_mutation(
    live_runtime_client,
    field,
    value,
):
    client, provider, store, initial = live_runtime_client

    class ParameterDriftClient:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.mutation_calls = []

        def get(self, path, **kwargs):
            response = self.wrapped.get(path, **kwargs)
            if path == "/admin-api/v1/config-schema":
                body = response.json()
                parameter = next(
                    item
                    for item in body["items"]
                    if item["key"] == "opensearch.max_retries"
                )
                parameter[field] = value
                return httpx.Response(
                    status_code=response.status_code,
                    headers=response.headers,
                    json=body,
                )
            return response

        def post(self, path, **kwargs):
            if path.startswith("/admin-api/v1/config") or path.startswith(
                "/admin-api/v1/runtime-config"
            ):
                self.mutation_calls.append(path)
            return self.wrapped.post(path, **kwargs)

    drifted_client = ParameterDriftClient(client)
    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            drifted_client,
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    assert captured.value.code == "acceptance_parameter_contract"
    assert drifted_client.mutation_calls == []
    assert provider.snapshot().as_dict() == initial.as_dict()
    assert store.list_runtime_operations(limit=10) == []


def test_runtime_acceptance_rejects_boolean_runtime_value_before_mutation(
    live_runtime_client,
):
    client, provider, store, initial = live_runtime_client

    class BooleanRuntimeValueClient:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.mutation_calls = []

        def get(self, path, **kwargs):
            response = self.wrapped.get(path, **kwargs)
            if path == "/admin-api/v1/runtime-config":
                body = response.json()
                body["values"]["opensearch.max_retries"] = True
                return httpx.Response(
                    status_code=response.status_code,
                    headers=response.headers,
                    json=body,
                )
            return response

        def post(self, path, **kwargs):
            if path.startswith("/admin-api/v1/config") or path.startswith(
                "/admin-api/v1/runtime-config"
            ):
                self.mutation_calls.append(path)
            return self.wrapped.post(path, **kwargs)

    tampered_client = BooleanRuntimeValueClient(client)
    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            tampered_client,
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    assert captured.value.code == "schema_runtime_values"
    assert tampered_client.mutation_calls == []
    assert provider.snapshot().as_dict() == initial.as_dict()
    assert store.list_runtime_operations(limit=10) == []


def test_runtime_acceptance_requires_the_business_token_to_be_valid(
    live_runtime_client,
):
    client, provider, store, initial = live_runtime_client

    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            client,
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="expired-business-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    assert captured.value.code == "business_token_probe_status"
    assert provider.snapshot().as_dict() == initial.as_dict()
    assert store.list_runtime_operations(limit=10) == []


def test_runtime_acceptance_rejects_tampered_draft_old_value_before_apply(
    live_runtime_client,
):
    client, provider, store, initial = live_runtime_client

    class TamperedOldValueClient:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.apply_called = False

        def get(self, path, **kwargs):
            return self.wrapped.get(path, **kwargs)

        def post(self, path, **kwargs):
            if path == "/admin-api/v1/runtime-config/apply":
                self.apply_called = True
            response = self.wrapped.post(path, **kwargs)
            if (
                path == "/admin-api/v1/config-drafts"
                and response.status_code == 201
                and response.json().get("status") == "validated"
            ):
                body = response.json()
                body["diff"]["opensearch.max_retries"]["old"] = 999
                return httpx.Response(
                    status_code=response.status_code,
                    headers=response.headers,
                    json=body,
                )
            return response

    tampered_client = TamperedOldValueClient(client)
    with pytest.raises(smoke_admin_runtime.AcceptanceError) as captured:
        smoke_admin_runtime.run_runtime_acceptance(
            tampered_client,
            username=AUTH[0],
            password=AUTH[1],
            business_api_token="business-api-token",
            expected_release_commit=EXPECTED_RELEASE_COMMIT,
            require_prometheus=False,
            require_journal_audit=False,
            check_probes=False,
            cooldown_seconds=0,
        )

    assert captured.value.code == "valid_draft_rejected"
    assert tampered_client.apply_called is False
    assert provider.snapshot().as_dict() == initial.as_dict()
    assert store.list_runtime_operations(limit=10) == []


def test_runtime_acceptance_cli_requires_exact_mutation_confirmation(
    monkeypatch,
    capsys,
):
    monkeypatch.setattr(
        "sys.argv",
        [
            "smoke_admin_runtime",
            "http://127.0.0.1:8000",
            "--confirm-runtime-mutation",
            "not-confirmed",
        ],
    )

    assert smoke_admin_runtime.main() == 2
    assert json.loads(capsys.readouterr().out) == {
        "ok": False,
        "error": "confirmation_required",
    }
