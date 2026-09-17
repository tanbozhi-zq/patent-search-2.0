"""验证运行时配置 API 的能力开关、幂等写入、审计关联、取消与限流语义。"""

import asyncio
from hashlib import sha256
import logging
from pathlib import Path
from threading import Event

import pytest
from fastapi import Response
from fastapi.testclient import TestClient

import app.core.admin_config.runtime as runtime_module
from app.api.admin_runtime_config import (
    _request_fingerprint,
    apply_runtime_config,
    rollback_runtime_config,
)
from app.core.admin_config import (
    AdminConfigDraftStore,
    RuntimeConfigApplyError,
    RuntimeConfigConflictError,
    RuntimeConfigController,
    RuntimeConfigProvider,
    runtime_snapshot_from_settings,
)
from app.core.config import Settings, get_settings
from app.core.request_context import bind_request_id, reset_request_id
from app.core.security import AdminPrincipal
from app.main import app
from app.schemas.admin import RuntimeConfigApplyRequest, RuntimeConfigRollbackRequest


AUTH = ("admin", "admin-password")
APPLY_HEADERS = {
    "X-Admin-Intent": "apply-runtime-config",
    "Idempotency-Key": "runtime-apply-key-0001",
}
ROLLBACK_HEADERS = {
    "X-Admin-Intent": "rollback-runtime-config",
    "Idempotency-Key": "runtime-rollback-key-0001",
}


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "_env_file": None,
        "admin_enabled": True,
        "admin_viewer_username": AUTH[0],
        "admin_viewer_password": AUTH[1],
        "admin_config_drafts_enabled": True,
        "admin_runtime_config_enabled": True,
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


class _Verifier:
    def __init__(self):
        self.snapshots = []

    async def verify(self, snapshot) -> None:
        self.snapshots.append(snapshot)


@pytest.fixture
def runtime_client(tmp_path):
    settings = _settings(tmp_path)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            provider = RuntimeConfigProvider(runtime_snapshot_from_settings(settings))
            store = AdminConfigDraftStore(settings.admin_config_database_path)
            verifier = _Verifier()
            controller = RuntimeConfigController(
                settings=settings,
                provider=provider,
                store=store,
                verifier=verifier,
                mutation_min_interval_seconds=0,
            )
            app.state.runtime_config_provider = provider
            app.state.admin_config_store = store
            app.state.runtime_config_controller = controller
            yield client, settings, provider, store, verifier
    finally:
        app.dependency_overrides.clear()


def _create_draft(client: TestClient, *, candidate_values: dict[str, int | float]):
    schema = client.get("/admin-api/v1/config-schema", auth=AUTH)
    assert schema.status_code == 200
    created = client.post(
        "/admin-api/v1/config-drafts",
        auth=AUTH,
        headers={"X-Admin-Intent": "create-config-draft"},
        json={
            "baseline_fingerprint": schema.json()["baseline_fingerprint"],
            "reason": "验证运行时变更实际值、版本和回滚路径",
            "candidate_values": candidate_values,
        },
    )
    assert created.status_code == 201
    return created.json(), schema.json()


def _apply_payload(draft, schema):
    return {
        "draft_id": draft["id"],
        "expected_version": schema["runtime_version"],
    }


async def _cancel_runtime_request_during_idempotency_lookup(
    monkeypatch,
    *,
    settings,
    provider,
    controller,
    store,
    payload,
    idempotency_key,
    request_id,
    endpoint=apply_runtime_config,
):
    """Cancel while the real SQLite lookup is still running in its worker."""

    lookup_started = Event()
    release_lookup = Event()
    lookup_finished = Event()
    original_find = store.find_runtime_operation

    def blocking_find(**kwargs):
        lookup_started.set()
        try:
            assert release_lookup.wait(timeout=5)
            return original_find(**kwargs)
        finally:
            lookup_finished.set()

    monkeypatch.setattr(store, "find_runtime_operation", blocking_find)
    token = bind_request_id(request_id)
    try:
        task = asyncio.create_task(
            endpoint(
                payload=payload,
                http_response=Response(),
                idempotency_key=idempotency_key,
                principal=AdminPrincipal(subject="admin"),
                settings=settings,
                provider=provider,
                controller=controller,
                store=store,
            )
        )
        assert await asyncio.to_thread(lookup_started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        release_lookup.set()
        with pytest.raises(asyncio.CancelledError) as captured:
            await task
        assert await asyncio.to_thread(lookup_finished.wait, 2)
        return captured.value
    finally:
        release_lookup.set()
        reset_request_id(token)


def test_runtime_write_capability_is_fail_closed_but_readback_stays_available(tmp_path):
    settings = _settings(tmp_path, admin_runtime_config_enabled=False)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            read = client.get("/admin-api/v1/runtime-config", auth=AUTH)
            applied = client.post(
                "/admin-api/v1/runtime-config/apply",
                auth=AUTH,
                headers=APPLY_HEADERS,
                json={
                    "draft_id": "00000000-0000-0000-0000-000000000000",
                    "expected_version": "0" * 64,
                },
            )
            rolled_back = client.post(
                "/admin-api/v1/runtime-config/rollback",
                auth=AUTH,
                headers=ROLLBACK_HEADERS,
                json={
                    "expected_version": "0" * 64,
                    "target_version": "0" * 64,
                    "reason": "验证默认关闭",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert read.status_code == 200
    assert read.json()["writes_enabled"] is False
    assert applied.status_code == rolled_back.status_code == 404


def test_runtime_apply_readback_and_manual_rollback_are_visible_in_admin_api(runtime_client):
    client, _settings_value, provider, _store_value, verifier = runtime_client
    draft, schema = _create_draft(
        client,
        candidate_values={"opensearch.max_retries": 0},
    )

    applied = client.post(
        "/admin-api/v1/runtime-config/apply",
        auth=AUTH,
        headers=APPLY_HEADERS,
        json=_apply_payload(draft, schema),
    )

    assert applied.status_code == 200
    applied_body = applied.json()
    assert applied.headers["Cache-Control"] == "no-store"
    assert applied_body["source"] == "runtime_override"
    assert applied_body["values"]["opensearch.max_retries"] == 0
    assert applied_body["rollback_version"] == schema["runtime_version"]
    assert applied_body["recent_operations"][0]["status"] == "applied"
    assert verifier.snapshots[-1].version == applied_body["version"]
    assert provider.snapshot().version == applied_body["version"]

    applied_schema = client.get("/admin-api/v1/config-schema", auth=AUTH)
    applied_items = {item["key"]: item for item in applied_schema.json()["items"]}
    assert applied_items["opensearch.max_retries"]["current_value"] == 0
    assert applied_items["opensearch.max_retries"]["rollback_value"] == 1

    config = client.get("/admin-api/v1/config", auth=AUTH)
    history = client.get("/admin-api/v1/config-drafts?limit=20", auth=AUTH)
    assert config.status_code == history.status_code == 200
    config_items = {item["key"]: item for item in config.json()["items"]}
    assert config.json()["runtime_version"] == applied_body["version"]
    assert config_items["opensearch.max_retries"]["value"] == "0"
    assert history.json()["items"][0]["status"] == "expired"

    rolled_back = client.post(
        "/admin-api/v1/runtime-config/rollback",
        auth=AUTH,
        headers=ROLLBACK_HEADERS,
        json={
            "expected_version": applied_body["version"],
            "target_version": applied_body["rollback_version"],
            "reason": "确认单实例回滚可恢复部署基线值",
        },
    )

    assert rolled_back.status_code == 200
    restored = rolled_back.json()
    assert restored["version"] not in {
        schema["runtime_version"],
        applied_body["version"],
    }
    assert restored["values"]["opensearch.max_retries"] == 1
    assert restored["rollback_version"] == applied_body["version"]
    assert restored["recent_operations"][0]["status"] == "rolled_back"
    restored_schema = client.get("/admin-api/v1/config-schema", auth=AUTH)
    restored_items = {item["key"]: item for item in restored_schema.json()["items"]}
    assert restored_items["opensearch.max_retries"]["current_value"] == 1
    assert restored_items["opensearch.max_retries"]["rollback_value"] == 0


def test_runtime_apply_rejects_replay_stale_and_restart_required_drafts(runtime_client):
    client, _settings_value, _provider, _store_value, _verifier = runtime_client
    draft, schema = _create_draft(
        client,
        candidate_values={"opensearch.max_retries": 0},
    )
    first = client.post(
        "/admin-api/v1/runtime-config/apply",
        auth=AUTH,
        headers=APPLY_HEADERS,
        json=_apply_payload(draft, schema),
    )
    replay = client.post(
        "/admin-api/v1/runtime-config/apply",
        auth=AUTH,
        headers=APPLY_HEADERS,
        json=_apply_payload(draft, schema),
    )
    reused = client.post(
        "/admin-api/v1/runtime-config/apply",
        auth=AUTH,
        headers=APPLY_HEADERS,
        json={
            "draft_id": draft["id"],
            "expected_version": first.json()["version"],
        },
    )
    stale = client.post(
        "/admin-api/v1/runtime-config/apply",
        auth=AUTH,
        headers={
            "X-Admin-Intent": "apply-runtime-config",
            "Idempotency-Key": "runtime-apply-key-0002",
        },
        json=_apply_payload(draft, schema),
    )

    assert first.status_code == replay.status_code == 200
    assert first.json()["version"] == replay.json()["version"]
    assert reused.status_code == stale.status_code == 409
    assert reused.json()["code"] == stale.json()["code"] == 40901

    mixed_draft, mixed_schema = _create_draft(
        client,
        candidate_values={
            "opensearch.max_retries": 1,
            "opensearch.pool_maxsize": 11,
        },
    )
    mixed = client.post(
        "/admin-api/v1/runtime-config/apply",
        auth=AUTH,
        headers={
            "X-Admin-Intent": "apply-runtime-config",
            "Idempotency-Key": "runtime-apply-key-0003",
        },
        json=_apply_payload(mixed_draft, mixed_schema),
    )

    assert mixed.status_code == 400
    assert mixed.json()["code"] == 40002


def test_runtime_conflict_audits_correlate_request_operation_and_draft(
    runtime_client,
    caplog,
):
    client, _settings_value, _provider, store, _verifier = runtime_client
    caplog.set_level(logging.INFO)
    draft, schema = _create_draft(
        client,
        candidate_values={"opensearch.max_retries": 0},
    )
    applied = client.post(
        "/admin-api/v1/runtime-config/apply",
        auth=AUTH,
        headers=APPLY_HEADERS,
        json=_apply_payload(draft, schema),
    )
    assert applied.status_code == 200

    caplog.clear()
    apply_request_id = "issue60.apply-conflict-001"
    stale_apply = client.post(
        "/admin-api/v1/runtime-config/apply",
        auth=AUTH,
        headers={
            "X-Admin-Intent": "apply-runtime-config",
            "Idempotency-Key": "runtime-apply-key-conflict-0002",
            "X-Request-ID": apply_request_id,
        },
        json=_apply_payload(draft, schema),
    )

    assert stale_apply.status_code == 409
    assert stale_apply.headers["X-Request-ID"] == apply_request_id
    assert stale_apply.json()["request_id"] == apply_request_id
    failed_apply = store.list_runtime_operations()[0]
    assert failed_apply.kind == "apply"
    assert failed_apply.status == "failed"
    assert failed_apply.failure_code == "version_conflict"
    apply_audit = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "admin_runtime_config_completed"
        and getattr(record, "action", None) == "runtime_config.apply"
    )
    assert getattr(apply_audit, "request_id") == apply_request_id
    assert getattr(apply_audit, "operation_id") == failed_apply.operation_id
    assert getattr(apply_audit, "draft_id") == draft["id"]

    caplog.clear()
    rollback_request_id = "issue60.rollback-conflict-001"
    stale_rollback = client.post(
        "/admin-api/v1/runtime-config/rollback",
        auth=AUTH,
        headers={
            "X-Admin-Intent": "rollback-runtime-config",
            "Idempotency-Key": "runtime-rollback-key-conflict-0002",
            "X-Request-ID": rollback_request_id,
        },
        json={
            "expected_version": schema["runtime_version"],
            "target_version": applied.json()["rollback_version"],
            "reason": "验证失败审计与持久操作关联",
        },
    )

    assert stale_rollback.status_code == 409
    assert stale_rollback.headers["X-Request-ID"] == rollback_request_id
    failed_rollback = store.list_runtime_operations()[0]
    assert failed_rollback.kind == "rollback"
    assert failed_rollback.status == "failed"
    assert failed_rollback.failure_code == "version_conflict"
    rollback_audit = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "admin_runtime_config_completed"
        and getattr(record, "action", None) == "runtime_config.rollback"
    )
    assert getattr(rollback_audit, "request_id") == rollback_request_id
    assert getattr(rollback_audit, "operation_id") == failed_rollback.operation_id
    assert getattr(rollback_audit, "draft_id") is None


def test_idempotency_mismatch_audit_uses_existing_operation_and_draft(
    runtime_client,
    caplog,
):
    client, _settings_value, _provider, store, _verifier = runtime_client
    caplog.set_level(logging.INFO)
    first_draft, first_schema = _create_draft(
        client,
        candidate_values={"opensearch.max_retries": 0},
    )
    first = client.post(
        "/admin-api/v1/runtime-config/apply",
        auth=AUTH,
        headers=APPLY_HEADERS,
        json=_apply_payload(first_draft, first_schema),
    )
    assert first.status_code == 200
    [existing_operation] = store.list_runtime_operations()

    second_draft, second_schema = _create_draft(
        client,
        candidate_values={"opensearch.retry_backoff_seconds": 0.2},
    )
    caplog.clear()
    mismatch = client.post(
        "/admin-api/v1/runtime-config/apply",
        auth=AUTH,
        headers={
            **APPLY_HEADERS,
            "X-Request-ID": "issue60.idempotency-conflict-001",
        },
        json=_apply_payload(second_draft, second_schema),
    )

    assert mismatch.status_code == 409
    audit = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "admin_runtime_config_completed"
        and getattr(record, "action", None) == "runtime_config.apply"
    )
    assert getattr(audit, "request_id") == "issue60.idempotency-conflict-001"
    assert getattr(audit, "operation_id") == existing_operation.operation_id
    assert getattr(audit, "draft_id") == first_draft["id"]
    assert getattr(audit, "draft_id") != second_draft["id"]
    assert store.list_runtime_operations() == [existing_operation]


def test_cancelled_apply_audits_domain_context_before_propagating(
    tmp_path,
    caplog,
):
    settings = _settings(tmp_path)
    initial = runtime_snapshot_from_settings(settings)
    provider = RuntimeConfigProvider(initial)
    store = AdminConfigDraftStore(settings.admin_config_database_path)
    draft = store.create(
        settings=settings,
        operator="admin",
        reason="验证取消仍记录域审计关联",
        candidate_values={"opensearch.max_retries": 0},
        current_values=initial.as_dict(),
        current_fingerprint=initial.version,
    )

    class BlockingVerifier:
        def __init__(self):
            self.started = asyncio.Event()

        async def verify(self, snapshot) -> None:
            if snapshot.version != initial.version:
                self.started.set()
                await asyncio.Event().wait()

    verifier = BlockingVerifier()
    controller = RuntimeConfigController(
        settings=settings,
        provider=provider,
        store=store,
        verifier=verifier,
        mutation_min_interval_seconds=0,
    )
    caplog.set_level(logging.INFO)

    async def cancel_endpoint():
        token = bind_request_id("issue60.cancelled-001")
        try:
            task = asyncio.create_task(
                apply_runtime_config(
                    payload=RuntimeConfigApplyRequest(
                        draft_id=draft.draft_id,
                        expected_version=initial.version,
                    ),
                    http_response=Response(),
                    idempotency_key="runtime-apply-key-cancelled-0001",
                    principal=AdminPrincipal(subject="admin"),
                    settings=settings,
                    provider=provider,
                    controller=controller,
                    store=store,
                )
            )
            await verifier.started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            reset_request_id(token)

    asyncio.run(cancel_endpoint())

    [operation] = store.list_runtime_operations()
    assert operation.status == "failed"
    assert operation.failure_code == "cancelled"
    audit = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "admin_runtime_config_completed"
        and getattr(record, "action", None) == "runtime_config.apply"
    )
    assert getattr(audit, "request_id") == "issue60.cancelled-001"
    assert getattr(audit, "result") == "cancelled"
    assert getattr(audit, "operation_id") == operation.operation_id
    assert getattr(audit, "draft_id") == draft.draft_id


def test_cancelled_reservation_conflict_audits_existing_operation_and_draft(
    tmp_path,
    caplog,
    monkeypatch,
):
    settings = _settings(tmp_path)
    initial = runtime_snapshot_from_settings(settings)
    provider = RuntimeConfigProvider(initial)
    store = AdminConfigDraftStore(settings.admin_config_database_path)
    existing_draft = store.create(
        settings=settings,
        operator="admin",
        reason="既有幂等操作草案",
        candidate_values={"opensearch.max_retries": 0},
        current_values=initial.as_dict(),
        current_fingerprint=initial.version,
    )
    new_draft = store.create(
        settings=settings,
        operator="admin",
        reason="竞态请求使用的新草案",
        candidate_values={"opensearch.retry_backoff_seconds": 0.2},
        current_values=initial.as_dict(),
        current_fingerprint=initial.version,
    )
    idempotency_key = "runtime-apply-key-cancel-race-0001"
    existing_operation, created = store.reserve_runtime_operation(
        idempotency_key_hash=sha256(idempotency_key.encode("utf-8")).hexdigest(),
        request_fingerprint="e" * 64,
        kind="apply",
        actor="admin",
        reason="existing request",
        expected_version=initial.version,
        draft_id=existing_draft.draft_id,
    )
    assert created is True
    controller = RuntimeConfigController(
        settings=settings,
        provider=provider,
        store=store,
        verifier=_Verifier(),
        mutation_min_interval_seconds=0,
    )
    monkeypatch.setattr(store, "find_runtime_operation", lambda **_kwargs: None)
    original_run_in_threadpool = runtime_module.run_in_threadpool

    async def cancel_conflicting_reservation():
        reserve_started = asyncio.Event()
        release_reserve = asyncio.Event()

        async def controlled_run_in_threadpool(function, *args, **kwargs):
            if (
                getattr(function, "__self__", None) is store
                and getattr(function, "__name__", "") == "reserve_runtime_operation"
            ):
                reserve_started.set()
                await release_reserve.wait()
            return await original_run_in_threadpool(function, *args, **kwargs)

        monkeypatch.setattr(
            runtime_module,
            "run_in_threadpool",
            controlled_run_in_threadpool,
        )
        token = bind_request_id("issue60.cancel-reserve-conflict-001")
        try:
            task = asyncio.create_task(
                apply_runtime_config(
                    payload=RuntimeConfigApplyRequest(
                        draft_id=new_draft.draft_id,
                        expected_version=initial.version,
                    ),
                    http_response=Response(),
                    idempotency_key=idempotency_key,
                    principal=AdminPrincipal(subject="admin"),
                    settings=settings,
                    provider=provider,
                    controller=controller,
                    store=store,
                )
            )
            await reserve_started.wait()
            task.cancel()
            await asyncio.sleep(0)
            release_reserve.set()
            with pytest.raises(asyncio.CancelledError) as captured:
                await task
            return captured.value
        finally:
            reset_request_id(token)

    caplog.set_level(logging.INFO)
    cancellation = asyncio.run(cancel_conflicting_reservation())

    assert isinstance(cancellation, asyncio.CancelledError)
    assert cancellation.operation_id == existing_operation.operation_id
    assert cancellation.draft_id == existing_draft.draft_id
    assert isinstance(cancellation.__cause__, RuntimeConfigConflictError)
    assert cancellation.__cause__.operation_id == existing_operation.operation_id
    assert cancellation.__cause__.draft_id == existing_draft.draft_id
    audit = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "admin_runtime_config_completed"
        and getattr(record, "action", None) == "runtime_config.apply"
    )
    assert getattr(audit, "request_id") == "issue60.cancel-reserve-conflict-001"
    assert getattr(audit, "result") == "cancelled"
    assert getattr(audit, "operation_id") == existing_operation.operation_id
    assert getattr(audit, "draft_id") == existing_draft.draft_id
    assert getattr(audit, "draft_id") != new_draft.draft_id
    assert store.list_runtime_operations() == [existing_operation]


def test_cancelled_matching_lookup_audits_existing_terminal_operation(
    tmp_path,
    caplog,
    monkeypatch,
):
    settings = _settings(tmp_path)
    initial = runtime_snapshot_from_settings(settings)
    provider = RuntimeConfigProvider(initial)
    store = AdminConfigDraftStore(settings.admin_config_database_path)
    draft = store.create(
        settings=settings,
        operator="admin",
        reason="验证取消的幂等重放保留既有终态",
        candidate_values={"opensearch.max_retries": 0},
        current_values=initial.as_dict(),
        current_fingerprint=initial.version,
    )
    controller = RuntimeConfigController(
        settings=settings,
        provider=provider,
        store=store,
        verifier=_Verifier(),
        mutation_min_interval_seconds=0,
    )
    payload = RuntimeConfigApplyRequest(
        draft_id=draft.draft_id,
        expected_version=initial.version,
    )
    idempotency_key = "runtime-apply-key-cancel-find-replay-0001"

    async def apply_then_cancel_replay():
        token = bind_request_id("issue60.lookup-replay-first-001")
        try:
            await apply_runtime_config(
                payload=payload,
                http_response=Response(),
                idempotency_key=idempotency_key,
                principal=AdminPrincipal(subject="admin"),
                settings=settings,
                provider=provider,
                controller=controller,
                store=store,
            )
        finally:
            reset_request_id(token)
        [existing_operation] = store.list_runtime_operations()
        cancellation = await _cancel_runtime_request_during_idempotency_lookup(
            monkeypatch,
            settings=settings,
            provider=provider,
            controller=controller,
            store=store,
            payload=payload,
            idempotency_key=idempotency_key,
            request_id="issue60.lookup-replay-cancelled-001",
        )
        return existing_operation, cancellation

    caplog.set_level(logging.INFO)
    existing_operation, cancellation = asyncio.run(apply_then_cancel_replay())

    assert existing_operation.status == "applied"
    assert cancellation.operation_id == existing_operation.operation_id
    assert cancellation.draft_id == draft.draft_id
    assert cancellation.result == "applied"
    audit = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "admin_runtime_config_completed"
        and getattr(record, "request_id", None)
        == "issue60.lookup-replay-cancelled-001"
    )
    assert getattr(audit, "result") == "applied"
    assert getattr(audit, "operation_id") == existing_operation.operation_id
    assert getattr(audit, "draft_id") == draft.draft_id
    assert store.list_runtime_operations() == [existing_operation]


def test_cancelled_conflicting_lookup_audits_existing_operation_and_draft(
    tmp_path,
    caplog,
    monkeypatch,
):
    settings = _settings(tmp_path)
    initial = runtime_snapshot_from_settings(settings)
    provider = RuntimeConfigProvider(initial)
    store = AdminConfigDraftStore(settings.admin_config_database_path)
    existing_draft = store.create(
        settings=settings,
        operator="admin",
        reason="首次查询发现的既有草案",
        candidate_values={"opensearch.max_retries": 0},
        current_values=initial.as_dict(),
        current_fingerprint=initial.version,
    )
    new_draft = store.create(
        settings=settings,
        operator="admin",
        reason="首次查询冲突的新草案",
        candidate_values={"opensearch.retry_backoff_seconds": 0.2},
        current_values=initial.as_dict(),
        current_fingerprint=initial.version,
    )
    idempotency_key = "runtime-apply-key-cancel-find-conflict-0001"
    existing_operation, created = store.reserve_runtime_operation(
        idempotency_key_hash=sha256(idempotency_key.encode("utf-8")).hexdigest(),
        request_fingerprint="e" * 64,
        kind="apply",
        actor="admin",
        reason="existing request",
        expected_version=initial.version,
        draft_id=existing_draft.draft_id,
    )
    assert created is True
    controller = RuntimeConfigController(
        settings=settings,
        provider=provider,
        store=store,
        verifier=_Verifier(),
        mutation_min_interval_seconds=0,
    )

    caplog.set_level(logging.INFO)
    cancellation = asyncio.run(
        _cancel_runtime_request_during_idempotency_lookup(
            monkeypatch,
            settings=settings,
            provider=provider,
            controller=controller,
            store=store,
            payload=RuntimeConfigApplyRequest(
                draft_id=new_draft.draft_id,
                expected_version=initial.version,
            ),
            idempotency_key=idempotency_key,
            request_id="issue60.lookup-conflict-cancelled-001",
        )
    )

    assert cancellation.operation_id == existing_operation.operation_id
    assert cancellation.draft_id == existing_draft.draft_id
    assert isinstance(cancellation.__cause__, RuntimeConfigConflictError)
    assert cancellation.__cause__.operation_id == existing_operation.operation_id
    assert cancellation.__cause__.draft_id == existing_draft.draft_id
    audit = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "admin_runtime_config_completed"
        and getattr(record, "request_id", None)
        == "issue60.lookup-conflict-cancelled-001"
    )
    assert getattr(audit, "result") == "cancelled"
    assert getattr(audit, "operation_id") == existing_operation.operation_id
    assert getattr(audit, "draft_id") == existing_draft.draft_id
    assert getattr(audit, "draft_id") != new_draft.draft_id
    assert store.list_runtime_operations() == [existing_operation]


def test_cancelled_empty_lookup_stops_before_reservation(
    tmp_path,
    caplog,
    monkeypatch,
):
    settings = _settings(tmp_path)
    initial = runtime_snapshot_from_settings(settings)
    provider = RuntimeConfigProvider(initial)
    store = AdminConfigDraftStore(settings.admin_config_database_path)
    draft = store.create(
        settings=settings,
        operator="admin",
        reason="首次查询为空时取消",
        candidate_values={"opensearch.max_retries": 0},
        current_values=initial.as_dict(),
        current_fingerprint=initial.version,
    )
    controller = RuntimeConfigController(
        settings=settings,
        provider=provider,
        store=store,
        verifier=_Verifier(),
        mutation_min_interval_seconds=0,
    )
    reserve_calls = 0
    original_reserve = store.reserve_runtime_operation

    def tracked_reserve(**kwargs):
        nonlocal reserve_calls
        reserve_calls += 1
        return original_reserve(**kwargs)

    monkeypatch.setattr(store, "reserve_runtime_operation", tracked_reserve)
    caplog.set_level(logging.INFO)
    cancellation = asyncio.run(
        _cancel_runtime_request_during_idempotency_lookup(
            monkeypatch,
            settings=settings,
            provider=provider,
            controller=controller,
            store=store,
            payload=RuntimeConfigApplyRequest(
                draft_id=draft.draft_id,
                expected_version=initial.version,
            ),
            idempotency_key="runtime-apply-key-cancel-find-empty-0001",
            request_id="issue60.lookup-empty-cancelled-001",
        )
    )

    assert getattr(cancellation, "operation_id", None) is None
    assert getattr(cancellation, "draft_id", None) is None
    assert getattr(cancellation, "result", None) is None
    audit = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "admin_runtime_config_completed"
        and getattr(record, "request_id", None) == "issue60.lookup-empty-cancelled-001"
    )
    assert getattr(audit, "result") == "cancelled"
    assert getattr(audit, "operation_id") is None
    assert getattr(audit, "draft_id") == draft.draft_id
    assert reserve_calls == 0
    assert store.list_runtime_operations() == []
    assert provider.snapshot() == initial


@pytest.mark.parametrize(
    ("kind", "event_type"),
    (("apply", "applied"), ("rollback", "rolled_back")),
)
def test_cancelled_stale_terminal_lookup_preserves_replay_conflict(
    tmp_path,
    caplog,
    monkeypatch,
    kind,
    event_type,
):
    settings = _settings(tmp_path)
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
    idempotency_key = f"runtime-{kind}-key-cancel-find-stale-0001"
    if kind == "apply":
        draft = store.create(
            settings=settings,
            operator="admin",
            reason="验证取消时仍校验过期 apply 重放",
            candidate_values={"opensearch.max_retries": 0},
            current_values=initial.as_dict(),
            current_fingerprint=initial.version,
        )
        payload = RuntimeConfigApplyRequest(
            draft_id=draft.draft_id,
            expected_version=initial.version,
        )
        request_fingerprint = _request_fingerprint(
            kind="apply",
            draft_id=draft.draft_id,
            expected_version=initial.version,
        )
        endpoint = apply_runtime_config
        draft_id = draft.draft_id
    else:
        reason = "验证取消时仍校验过期 rollback 重放"
        payload = RuntimeConfigRollbackRequest(
            expected_version=initial.version,
            target_version="a" * 64,
            reason=reason,
        )
        request_fingerprint = _request_fingerprint(
            kind="rollback",
            expected_version=initial.version,
            target_version=payload.target_version,
            reason=reason,
        )
        endpoint = rollback_runtime_config
        draft_id = None
    operation, created = store.reserve_runtime_operation(
        idempotency_key_hash=sha256(idempotency_key.encode("utf-8")).hexdigest(),
        request_fingerprint=request_fingerprint,
        kind=kind,
        actor="admin",
        reason="existing terminal request",
        expected_version=initial.version,
        draft_id=draft_id,
    )
    assert created is True
    operation = store.append_runtime_event(
        operation_id=operation.operation_id,
        event_type=event_type,
        previous_version=initial.version,
        current_version="f" * 64,
    )
    assert operation.status == event_type

    caplog.set_level(logging.INFO)
    request_id = f"issue60.lookup-stale-{kind}-cancelled-001"
    cancellation = asyncio.run(
        _cancel_runtime_request_during_idempotency_lookup(
            monkeypatch,
            settings=settings,
            provider=provider,
            controller=controller,
            store=store,
            payload=payload,
            idempotency_key=idempotency_key,
            request_id=request_id,
            endpoint=endpoint,
        )
    )

    assert cancellation.operation_id == operation.operation_id
    assert getattr(cancellation, "draft_id", None) == draft_id
    assert getattr(cancellation, "result", None) is None
    assert isinstance(cancellation.__cause__, RuntimeConfigConflictError)
    assert cancellation.__cause__.operation_id == operation.operation_id
    assert cancellation.__cause__.draft_id == draft_id
    audit = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "admin_runtime_config_completed"
        and getattr(record, "request_id", None) == request_id
    )
    assert getattr(audit, "action") == f"runtime_config.{kind}"
    assert getattr(audit, "result") == "cancelled"
    assert getattr(audit, "operation_id") == operation.operation_id
    assert getattr(audit, "draft_id") == draft_id
    assert store.list_runtime_operations() == [operation]
    assert provider.snapshot() == initial


@pytest.mark.parametrize(
    ("event_type", "stored_failure_code", "expected_failure_code"),
    (
        (None, None, "operation_incomplete"),
        ("failed", "verification_failed", "verification_failed"),
    ),
    ids=("incomplete", "failed"),
)
def test_cancelled_nonterminal_lookup_preserves_replay_error(
    tmp_path,
    caplog,
    monkeypatch,
    event_type,
    stored_failure_code,
    expected_failure_code,
):
    settings = _settings(tmp_path)
    initial = runtime_snapshot_from_settings(settings)
    provider = RuntimeConfigProvider(initial)
    store = AdminConfigDraftStore(settings.admin_config_database_path)
    draft = store.create(
        settings=settings,
        operator="admin",
        reason="验证取消时仍校验失败或未完成的重放",
        candidate_values={"opensearch.max_retries": 0},
        current_values=initial.as_dict(),
        current_fingerprint=initial.version,
    )
    controller = RuntimeConfigController(
        settings=settings,
        provider=provider,
        store=store,
        verifier=_Verifier(),
        mutation_min_interval_seconds=0,
    )
    payload = RuntimeConfigApplyRequest(
        draft_id=draft.draft_id,
        expected_version=initial.version,
    )
    idempotency_key = (
        f"runtime-apply-key-cancel-find-{expected_failure_code}-0001"
    )
    operation, created = store.reserve_runtime_operation(
        idempotency_key_hash=sha256(idempotency_key.encode("utf-8")).hexdigest(),
        request_fingerprint=_request_fingerprint(
            kind="apply",
            draft_id=draft.draft_id,
            expected_version=initial.version,
        ),
        kind="apply",
        actor="admin",
        reason="existing nonterminal request",
        expected_version=initial.version,
        draft_id=draft.draft_id,
    )
    assert created is True
    if event_type is not None:
        operation = store.append_runtime_event(
            operation_id=operation.operation_id,
            event_type=event_type,
            previous_version=initial.version,
            current_version=initial.version,
            failure_code=stored_failure_code,
        )

    caplog.set_level(logging.INFO)
    request_id = f"issue60.lookup-{operation.status}-cancelled-001"
    cancellation = asyncio.run(
        _cancel_runtime_request_during_idempotency_lookup(
            monkeypatch,
            settings=settings,
            provider=provider,
            controller=controller,
            store=store,
            payload=payload,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    )

    assert cancellation.operation_id == operation.operation_id
    assert cancellation.draft_id == draft.draft_id
    assert getattr(cancellation, "result", None) is None
    assert isinstance(cancellation.__cause__, RuntimeConfigApplyError)
    assert cancellation.__cause__.failure_code == expected_failure_code
    assert cancellation.__cause__.operation_id == operation.operation_id
    assert cancellation.__cause__.draft_id == draft.draft_id
    audit = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "admin_runtime_config_completed"
        and getattr(record, "request_id", None) == request_id
    )
    assert getattr(audit, "result") == "cancelled"
    assert getattr(audit, "operation_id") == operation.operation_id
    assert getattr(audit, "draft_id") == draft.draft_id
    assert store.list_runtime_operations() == [operation]
    assert provider.snapshot() == initial


def test_runtime_mutations_are_rate_limited_with_the_existing_retry_contract(
    runtime_client,
    caplog,
):
    client, _settings_value, _provider, _store_value, _verifier = runtime_client
    caplog.set_level(logging.INFO)
    app.state.runtime_config_controller._mutation_min_interval_seconds = 60
    first_draft, first_schema = _create_draft(
        client,
        candidate_values={"opensearch.max_retries": 0},
    )
    first = client.post(
        "/admin-api/v1/runtime-config/apply",
        auth=AUTH,
        headers=APPLY_HEADERS,
        json=_apply_payload(first_draft, first_schema),
    )
    second_draft, second_schema = _create_draft(
        client,
        candidate_values={"opensearch.retry_backoff_seconds": 0.2},
    )
    throttled = client.post(
        "/admin-api/v1/runtime-config/apply",
        auth=AUTH,
        headers={
            "X-Admin-Intent": "apply-runtime-config",
            "Idempotency-Key": "runtime-apply-key-rate-0002",
            "X-Request-ID": "issue60.rate-limited-001",
        },
        json=_apply_payload(second_draft, second_schema),
    )

    assert first.status_code == 200
    assert throttled.status_code == 429
    assert throttled.json()["code"] == 42901
    assert throttled.headers["Retry-After"] == "60"
    rate_audit = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "admin_runtime_config_completed"
        and getattr(record, "action", None) == "runtime_config.apply"
        and getattr(record, "result", None) == "rate_limited"
    )
    assert getattr(rate_audit, "request_id") == "issue60.rate-limited-001"
    assert getattr(rate_audit, "operation_id") is None
    assert getattr(rate_audit, "draft_id") == second_draft["id"]


@pytest.mark.parametrize(
    "request_kwargs",
    (
        {},
        {"headers": {"X-Admin-Intent": "apply-runtime-config"}},
        {
            "headers": {
                **APPLY_HEADERS,
                "Origin": "https://evil.example",
                "Sec-Fetch-Site": "cross-site",
            }
        },
        {
            "headers": {
                "X-Admin-Intent": "apply-runtime-config",
                "Idempotency-Key": "too-short",
            }
        },
    ),
)
def test_runtime_write_boundary_requires_intent_same_origin_and_idempotency_key(
    runtime_client,
    request_kwargs,
):
    client, _settings_value, _provider, _store_value, _verifier = runtime_client
    payload = {
        "draft_id": "00000000-0000-0000-0000-000000000000",
        "expected_version": "0" * 64,
    }
    response = client.post(
        "/admin-api/v1/runtime-config/apply",
        auth=AUTH,
        json=payload,
        **request_kwargs,
    )

    assert response.status_code == 400
    assert response.json()["code"] == 40002


def test_runtime_write_bodies_reuse_the_bounded_admin_json_limit(runtime_client):
    client, _settings_value, _provider, _store_value, _verifier = runtime_client
    response = client.post(
        "/admin-api/v1/runtime-config/apply",
        auth=AUTH,
        headers={**APPLY_HEADERS, "Content-Type": "application/json"},
        content=b"{" + b"x" * (16 * 1024) + b"}",
    )

    assert response.status_code == 413
    assert response.json()["code"] == 41301


def test_runtime_routes_reject_non_admin_credentials_and_do_not_pollute_metrics(
    runtime_client,
):
    client, _settings_value, _provider, _store_value, _verifier = runtime_client
    for path in (
        "/admin-api/v1/runtime-config",
        "/admin-api/v1/runtime-config/apply",
        "/admin-api/v1/runtime-config/rollback",
    ):
        method = client.get if path.endswith("runtime-config") else client.post
        for kwargs in (
            {},
            {"headers": {"X-API-Key": "business-api-token"}},
            {"auth": ("console-user", "console-password")},
            {"auth": (AUTH[0], "wrong-password")},
        ):
            response = method(path, **kwargs)
            assert response.status_code == 401
            assert response.json()["code"] == 40101

    draft, schema = _create_draft(
        client,
        candidate_values={"opensearch.max_retries": 0},
    )
    applied = client.post(
        "/admin-api/v1/runtime-config/apply",
        auth=AUTH,
        headers={
            "X-Admin-Intent": "apply-runtime-config",
            "Idempotency-Key": "runtime-apply-key-metrics",
        },
        json=_apply_payload(draft, schema),
    )
    metrics = client.get("/metrics").text

    assert applied.status_code == 200
    assert 'route="/admin-api/v1/runtime-config"' not in metrics
    assert 'route="/admin-api/v1/runtime-config/apply"' not in metrics
    assert 'route="/admin-api/v1/runtime-config/rollback"' not in metrics


def test_runtime_audit_logs_do_not_include_rollback_reason_or_idempotency_key(
    runtime_client,
    caplog,
):
    client, _settings_value, _provider, _store_value, _verifier = runtime_client
    caplog.set_level(logging.INFO)
    draft, schema = _create_draft(
        client,
        candidate_values={"opensearch.max_retries": 0},
    )
    applied = client.post(
        "/admin-api/v1/runtime-config/apply",
        auth=AUTH,
        headers=APPLY_HEADERS,
        json=_apply_payload(draft, schema),
    )
    reason = "SENTINEL_RUNTIME_ROLLBACK_REASON"
    key = "SENTINEL-RUNTIME-IDEMPOTENCY-KEY-0001"
    rolled_back = client.post(
        "/admin-api/v1/runtime-config/rollback",
        auth=AUTH,
        headers={
            "X-Admin-Intent": "rollback-runtime-config",
            "Idempotency-Key": key,
        },
        json={
            "expected_version": applied.json()["version"],
            "target_version": applied.json()["rollback_version"],
            "reason": reason,
        },
    )

    assert rolled_back.status_code == 200
    assert all(reason not in record.getMessage() for record in caplog.records)
    assert all(key not in record.getMessage() for record in caplog.records)
    audit = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "admin_runtime_config_completed"
        and getattr(record, "action", None) == "runtime_config.rollback"
    )
    assert getattr(audit, "result") == "rolled_back"
    rollback_operation = next(
        item
        for item in rolled_back.json()["recent_operations"]
        if item["status"] == "rolled_back"
    )
    assert getattr(audit, "operation_id") == rollback_operation["operation_id"]
