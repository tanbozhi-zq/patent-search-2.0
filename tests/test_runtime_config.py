"""验证运行时配置快照、原子应用/回滚、验证失败恢复与并发幂等行为。"""

import asyncio
from dataclasses import replace
from pathlib import Path
import sqlite3
from threading import Event

import pytest
from opensearchpy.exceptions import ConnectionError as OpenSearchConnectionError

import app.core.admin_config.runtime as runtime_module
from app.core.admin_config import (
    AdminConfigDraftStore,
    RuntimeConfigApplyError,
    RuntimeConfigConflictError,
    RuntimeConfigController,
    RuntimeConfigProvider,
    RuntimeConfigRateLimitedError,
    new_runtime_snapshot,
    runtime_config_scope,
    runtime_snapshot_from_settings,
)
from app.core.admin_config.fingerprint import baseline_fingerprint
from app.core.config import Settings
from app.core.deadline import request_deadline
from app.repositories.opensearch_repo import OpenSearchRepository


EMPTY_SEARCH_RESPONSE = {"hits": {"total": {"value": 0}, "hits": []}}


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "enable_auth": False,
        "opensearch_timeout_seconds": 9,
        "opensearch_max_retries": 1,
        "opensearch_retry_backoff_seconds": 0.5,
        "patent_search_deadline_seconds": 20,
        "patent_search_bulkhead_capacity": 4,
        "patent_search_heavy_bulkhead_capacity": 3,
        "patent_search_bulkhead_acquire_timeout_seconds": 0.01,
        "service_release_commit": "68ae664",
        "service_release_tag": "v0.10.0",
        "service_instance_id": "instance-a",
    }
    values.update(overrides)
    return Settings(**values)


def _store(tmp_path: Path) -> AdminConfigDraftStore:
    return AdminConfigDraftStore(tmp_path / "admin-state" / "admin-config.sqlite3")


def _create_draft(
    *,
    store: AdminConfigDraftStore,
    settings: Settings,
    provider: RuntimeConfigProvider,
    candidate_values: dict[str, int | float],
):
    snapshot = provider.snapshot()
    return store.create(
        settings=settings,
        operator="admin",
        reason="验证单实例运行时快照替换",
        candidate_values=candidate_values,
        current_values=snapshot.as_dict(),
        current_fingerprint=snapshot.version,
    )


class _Verifier:
    def __init__(self, *, rejected_backoff: float | None = None):
        self.rejected_backoff = rejected_backoff
        self.snapshots = []

    async def verify(self, snapshot) -> None:
        self.snapshots.append(snapshot)
        if (
            self.rejected_backoff is not None
            and snapshot.value_for("opensearch.retry_backoff_seconds")
            == self.rejected_backoff
        ):
            raise RuntimeConfigApplyError(
                "verification failed",
                failure_code="verification_failed",
            )


def _controller(
    *,
    settings: Settings,
    provider: RuntimeConfigProvider,
    store: AdminConfigDraftStore,
    verifier: _Verifier,
    mutation_min_interval_seconds: float = 0,
) -> RuntimeConfigController:
    return RuntimeConfigController(
        settings=settings,
        provider=provider,
        store=store,
        verifier=verifier,
        mutation_min_interval_seconds=mutation_min_interval_seconds,
    )


def _snapshot_with_value(snapshot, key: str, value: int | float):
    values = snapshot.as_dict()
    values[key] = value
    return replace(snapshot, values=tuple(sorted(values.items())))


@pytest.mark.parametrize(
    ("key", "settings_attribute"),
    (
        ("opensearch.timeout_seconds", "opensearch_timeout_seconds"),
        ("request.deadline_seconds", "patent_search_deadline_seconds"),
    ),
)
def test_runtime_snapshot_rejects_unsafe_timing_even_if_settings_validation_is_bypassed(
    key,
    settings_attribute,
):
    unsafe_settings = _settings().model_copy(update={settings_attribute: 300})

    with pytest.raises(ValueError, match=key):
        runtime_snapshot_from_settings(unsafe_settings)


def test_runtime_provider_rejects_unsafe_initial_replacement_and_restore_snapshots():
    settings = _settings()
    initial = runtime_snapshot_from_settings(settings)
    unsafe = _snapshot_with_value(initial, "opensearch.timeout_seconds", 300)

    with pytest.raises(ValueError, match="opensearch.timeout_seconds"):
        RuntimeConfigProvider(unsafe)

    provider = RuntimeConfigProvider(initial)
    with pytest.raises(ValueError, match="opensearch.timeout_seconds"):
        provider.replace(expected_version=initial.version, replacement=unsafe)
    assert provider.snapshot() == initial

    with pytest.raises(ValueError, match="opensearch.timeout_seconds"):
        provider.restore(
            expected_version=initial.version,
            previous=unsafe,
            rollback_target=None,
        )
    assert provider.snapshot() == initial


def test_runtime_provider_restore_discards_only_unsafe_secondary_target_atomically():
    settings = _settings()
    initial = runtime_snapshot_from_settings(settings)
    attempted = new_runtime_snapshot(
        settings,
        values={
            **initial.as_dict(),
            "opensearch.retry_backoff_seconds": 0.2,
        },
        parent_version=initial.version,
    )
    unsafe_target = _snapshot_with_value(
        initial,
        "opensearch.timeout_seconds",
        300,
    )
    provider = RuntimeConfigProvider(initial)
    provider.replace(expected_version=initial.version, replacement=attempted)

    provider.restore(
        expected_version=attempted.version,
        previous=initial,
        rollback_target=unsafe_target,
    )

    assert provider.snapshots() == (initial, None)

    provider = RuntimeConfigProvider(initial)
    provider._previous = unsafe_target
    before = provider.snapshots()
    with pytest.raises(RuntimeConfigConflictError):
        provider.restore(
            expected_version=attempted.version,
            previous=initial,
            rollback_target=unsafe_target,
        )
    assert provider.snapshots() == before


def test_manual_rollback_rejects_unsafe_legacy_target_before_swap(tmp_path):
    settings = _settings()
    initial = runtime_snapshot_from_settings(settings)
    current = new_runtime_snapshot(
        settings,
        values={**initial.as_dict(), "opensearch.timeout_seconds": 200},
        parent_version=initial.version,
    )
    provider = RuntimeConfigProvider(current)
    unsafe_target = _snapshot_with_value(
        initial,
        "opensearch.timeout_seconds",
        300,
    )
    # Simulate a snapshot retained by a pre-contract process or corrupt test
    # fixture; public provider boundaries reject creating this state.
    provider._previous = unsafe_target
    store = _store(tmp_path)
    controller = _controller(
        settings=settings,
        provider=provider,
        store=store,
        verifier=_Verifier(),
    )

    async def rollback_unsafe_target():
        with pytest.raises(RuntimeConfigApplyError) as captured:
            await controller.rollback(
                expected_version=current.version,
                target_version=unsafe_target.version,
                reason="拒绝旧版本遗留的超限回滚目标",
                idempotency_key_hash="0" * 64,
                request_fingerprint="f" * 64,
                actor="admin",
            )
        return captured.value

    error = asyncio.run(rollback_unsafe_target())

    assert error.failure_code == "operation_failed"
    assert provider.snapshot() == current
    assert provider.previous_snapshot() == unsafe_target
    [operation] = store.list_runtime_operations()
    assert operation.status == "failed"
    assert operation.failure_code == "operation_failed"


def test_runtime_audit_schema_migrates_an_existing_issue59_draft_database(tmp_path):
    settings = _settings()
    database_path = tmp_path / "admin-state" / "admin-config.sqlite3"
    database_path.parent.mkdir(mode=0o700)
    database_path.touch(mode=0o600)
    database_path.chmod(0o600)
    draft_id = "00000000-0000-0000-0000-000000000001"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE config_change_drafts (
                draft_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                operator TEXT NOT NULL,
                reason TEXT NOT NULL,
                registry_version TEXT NOT NULL,
                service_version TEXT NOT NULL,
                release_commit TEXT NOT NULL,
                baseline_fingerprint TEXT NOT NULL,
                baseline_values_json TEXT NOT NULL,
                candidate_values_json TEXT NOT NULL,
                diff_json TEXT NOT NULL,
                validation_status TEXT NOT NULL,
                validation_result_json TEXT NOT NULL
            );
            PRAGMA user_version = 1;
            """
        )
        connection.execute(
            """
            INSERT INTO config_change_drafts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                draft_id,
                "2026-08-21T00:00:00.000Z",
                "2099-08-22T00:00:00.000Z",
                "admin",
                "保留的 Issue 59 草案",
                "2026-08-21.v1",
                "0.10.0",
                settings.service_release_commit,
                baseline_fingerprint(settings),
                "{}",
                "{}",
                "{}",
                "validated",
                "[]",
            ),
        )

    store = AdminConfigDraftStore(database_path)
    [draft] = store.list(settings=settings)

    assert draft.draft_id == draft_id
    assert draft.status == "validated"
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'config_runtime_operation_events'"
        ).fetchone() == ("config_runtime_operation_events",)


def test_runtime_apply_and_manual_rollback_use_new_versions_and_leave_settings_immutable(
    tmp_path,
):
    settings = _settings()
    initial = runtime_snapshot_from_settings(settings)
    provider = RuntimeConfigProvider(initial)
    store = _store(tmp_path)
    verifier = _Verifier()
    controller = _controller(
        settings=settings,
        provider=provider,
        store=store,
        verifier=verifier,
    )
    draft = _create_draft(
        store=store,
        settings=settings,
        provider=provider,
        candidate_values={"opensearch.max_retries": 0},
    )

    async def run_operations():
        applied = await controller.apply(
            draft_id=draft.draft_id,
            expected_version=initial.version,
            idempotency_key_hash="a" * 64,
            request_fingerprint="b" * 64,
            actor="admin",
        )
        applied_snapshot = provider.snapshot()
        rolled_back = await controller.rollback(
            expected_version=applied_snapshot.version,
            target_version=initial.version,
            reason="验证手工回滚",
            idempotency_key_hash="c" * 64,
            request_fingerprint="d" * 64,
            actor="admin",
        )
        with pytest.raises(RuntimeConfigConflictError):
            await controller.apply(
                draft_id=draft.draft_id,
                expected_version=initial.version,
                idempotency_key_hash="a" * 64,
                request_fingerprint="b" * 64,
                actor="admin",
            )
        return applied, applied_snapshot, rolled_back, provider.snapshot()

    applied, applied_snapshot, rolled_back, restored_snapshot = asyncio.run(run_operations())

    assert applied.status == "applied"
    assert applied.previous_version == initial.version
    assert applied.current_version == applied_snapshot.version
    assert applied_snapshot.source == "runtime_override"
    assert applied_snapshot.version != initial.version
    assert applied_snapshot.value_for("opensearch.max_retries") == 0
    assert settings.opensearch_max_retries == 1

    assert rolled_back.status == "rolled_back"
    assert restored_snapshot.source == "runtime_override"
    assert restored_snapshot.version not in {initial.version, applied_snapshot.version}
    assert restored_snapshot.value_for("opensearch.max_retries") == 1
    assert provider.previous_snapshot() == applied_snapshot
    assert [operation.status for operation in store.list_runtime_operations()] == [
        "rolled_back",
        "applied",
    ]

    with sqlite3.connect(store.database_path) as connection:
        versions = connection.execute(
            "SELECT version FROM config_runtime_versions"
        ).fetchall()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE config_runtime_versions SET source = 'runtime_override'"
            )
    assert {row[0] for row in versions} == {
        initial.version,
        applied_snapshot.version,
        restored_snapshot.version,
    }

    restarted = runtime_snapshot_from_settings(settings)
    assert restarted.source == "deployment_baseline"
    assert restarted.value_for("opensearch.max_retries") == 1


def test_runtime_apply_rejects_mixed_restart_required_draft_without_swapping(tmp_path):
    settings = _settings()
    initial = runtime_snapshot_from_settings(settings)
    provider = RuntimeConfigProvider(initial)
    store = _store(tmp_path)
    controller = _controller(
        settings=settings,
        provider=provider,
        store=store,
        verifier=_Verifier(),
    )
    draft = _create_draft(
        store=store,
        settings=settings,
        provider=provider,
        candidate_values={
            "opensearch.max_retries": 0,
            "opensearch.pool_maxsize": 11,
        },
    )

    async def apply_mixed_draft():
        with pytest.raises(RuntimeConfigApplyError) as captured:
            await controller.apply(
                draft_id=draft.draft_id,
                expected_version=initial.version,
                idempotency_key_hash="e" * 64,
                request_fingerprint="f" * 64,
                actor="admin",
            )
        return captured.value

    error = asyncio.run(apply_mixed_draft())

    assert error.failure_code == "unsupported_apply_mode"
    assert provider.snapshot() == initial
    [operation] = store.list_runtime_operations()
    assert operation.status == "failed"
    assert operation.failure_code == "unsupported_apply_mode"


def test_runtime_apply_is_idempotent_and_rejects_stale_or_reused_requests(tmp_path):
    settings = _settings()
    initial = runtime_snapshot_from_settings(settings)
    provider = RuntimeConfigProvider(initial)
    store = _store(tmp_path)
    controller = _controller(
        settings=settings,
        provider=provider,
        store=store,
        verifier=_Verifier(),
    )
    draft = _create_draft(
        store=store,
        settings=settings,
        provider=provider,
        candidate_values={"opensearch.max_retries": 0},
    )

    async def apply_and_replay():
        first = await controller.apply(
            draft_id=draft.draft_id,
            expected_version=initial.version,
            idempotency_key_hash="1" * 64,
            request_fingerprint="2" * 64,
            actor="admin",
        )
        repeated = await controller.apply(
            draft_id=draft.draft_id,
            expected_version=initial.version,
            idempotency_key_hash="1" * 64,
            request_fingerprint="2" * 64,
            actor="admin",
        )
        with pytest.raises(RuntimeConfigConflictError):
            await controller.apply(
                draft_id=draft.draft_id,
                expected_version=initial.version,
                idempotency_key_hash="1" * 64,
                request_fingerprint="3" * 64,
                actor="admin",
            )
        with pytest.raises(RuntimeConfigConflictError):
            await controller.apply(
                draft_id=draft.draft_id,
                expected_version=initial.version,
                idempotency_key_hash="4" * 64,
                request_fingerprint="5" * 64,
                actor="admin",
            )
        return first, repeated

    first, repeated = asyncio.run(apply_and_replay())

    assert first == repeated
    assert first.status == "applied"
    assert len(store.list_runtime_operations()) == 2
    failed = next(
        operation
        for operation in store.list_runtime_operations()
        if operation.status == "failed"
    )
    assert failed.failure_code == "version_conflict"


def test_reserve_idempotency_race_conflict_keeps_existing_operation_context(tmp_path):
    store = _store(tmp_path)
    draft_id = "00000000-0000-0000-0000-000000000001"
    first, created = store.reserve_runtime_operation(
        idempotency_key_hash="1" * 64,
        request_fingerprint="2" * 64,
        kind="apply",
        actor="admin",
        reason="first request",
        expected_version="3" * 64,
        draft_id=draft_id,
    )

    with pytest.raises(RuntimeConfigConflictError) as captured:
        store.reserve_runtime_operation(
            idempotency_key_hash="1" * 64,
            request_fingerprint="4" * 64,
            kind="apply",
            actor="admin",
            reason="racing request",
            expected_version="5" * 64,
            draft_id="00000000-0000-0000-0000-000000000002",
        )

    assert created is True
    assert captured.value.operation_id == first.operation_id
    assert captured.value.draft_id == draft_id


def test_concurrent_runtime_apply_serializes_to_one_complete_snapshot(tmp_path):
    settings = _settings()
    initial = runtime_snapshot_from_settings(settings)
    provider = RuntimeConfigProvider(initial)
    store = _store(tmp_path)
    controller = _controller(
        settings=settings,
        provider=provider,
        store=store,
        verifier=_Verifier(),
    )
    draft = _create_draft(
        store=store,
        settings=settings,
        provider=provider,
        candidate_values={"opensearch.max_retries": 0},
    )

    async def apply_concurrently():
        return await asyncio.gather(
            *(
                controller.apply(
                    draft_id=draft.draft_id,
                    expected_version=initial.version,
                    idempotency_key_hash=key * 64,
                    request_fingerprint=(key.upper()) * 64,
                    actor="admin",
                )
                for key in ("a", "b")
            ),
            return_exceptions=True,
        )

    outcomes = asyncio.run(apply_concurrently())

    assert sum(getattr(outcome, "status", None) == "applied" for outcome in outcomes) == 1
    assert sum(isinstance(outcome, RuntimeConfigConflictError) for outcome in outcomes) == 1
    assert provider.snapshot().value_for("opensearch.max_retries") == 0
    assert {operation.status for operation in store.list_runtime_operations()} == {
        "applied",
        "failed",
    }


def test_failed_runtime_verification_restores_prior_snapshot_and_audit_once(tmp_path):
    settings = _settings()
    initial = runtime_snapshot_from_settings(settings)
    provider = RuntimeConfigProvider(initial)
    store = _store(tmp_path)
    verifier = _Verifier(rejected_backoff=0.2)
    controller = _controller(
        settings=settings,
        provider=provider,
        store=store,
        verifier=verifier,
    )
    first_draft = _create_draft(
        store=store,
        settings=settings,
        provider=provider,
        candidate_values={"opensearch.max_retries": 0},
    )

    async def apply_then_fail():
        first = await controller.apply(
            draft_id=first_draft.draft_id,
            expected_version=initial.version,
            idempotency_key_hash="6" * 64,
            request_fingerprint="7" * 64,
            actor="admin",
        )
        stable = provider.snapshot()
        second_draft = _create_draft(
            store=store,
            settings=settings,
            provider=provider,
            candidate_values={"opensearch.retry_backoff_seconds": 0.2},
        )
        with pytest.raises(RuntimeConfigApplyError) as captured:
            await controller.apply(
                draft_id=second_draft.draft_id,
                expected_version=stable.version,
                idempotency_key_hash="8" * 64,
                request_fingerprint="9" * 64,
                actor="admin",
            )
        return first, stable, captured.value

    first, stable, error = asyncio.run(apply_then_fail())

    assert first.status == "applied"
    assert error.failure_code == "verification_failed"
    assert provider.snapshot() == stable
    assert provider.previous_snapshot() == initial
    failed = next(
        operation
        for operation in store.list_runtime_operations()
        if operation.status == "failed"
    )
    assert failed.failure_code == "verification_failed"
    with sqlite3.connect(store.database_path) as connection:
        event_types = [
            row[0]
            for row in connection.execute(
                "SELECT event_type FROM config_runtime_operation_events "
                "WHERE operation_id = ? ORDER BY event_sequence",
                (failed.operation_id,),
            )
        ]
    assert event_types == ["started", "failed"]


def test_failed_apply_restores_safe_current_and_discards_unsafe_legacy_target(
    tmp_path,
):
    settings = _settings()
    initial = runtime_snapshot_from_settings(settings)
    provider = RuntimeConfigProvider(initial)
    unsafe_target = _snapshot_with_value(
        initial,
        "opensearch.timeout_seconds",
        300,
    )
    provider._previous = unsafe_target
    store = _store(tmp_path)
    controller = _controller(
        settings=settings,
        provider=provider,
        store=store,
        verifier=_Verifier(rejected_backoff=0.2),
    )
    draft = _create_draft(
        store=store,
        settings=settings,
        provider=provider,
        candidate_values={"opensearch.retry_backoff_seconds": 0.2},
    )

    async def apply_rejected_candidate():
        with pytest.raises(RuntimeConfigApplyError) as captured:
            await controller.apply(
                draft_id=draft.draft_id,
                expected_version=initial.version,
                idempotency_key_hash="a" * 64,
                request_fingerprint="b" * 64,
                actor="admin",
            )
        return captured.value

    error = asyncio.run(apply_rejected_candidate())

    assert error.failure_code == "verification_failed"
    assert provider.snapshot() == initial
    assert provider.previous_snapshot() is None
    [operation] = store.list_runtime_operations()
    assert operation.status == "failed"
    assert operation.failure_code == "verification_failed"
    with sqlite3.connect(store.database_path) as connection:
        events = connection.execute(
            "SELECT event_type, failure_code "
            "FROM config_runtime_operation_events "
            "WHERE operation_id = ? ORDER BY event_sequence",
            (operation.operation_id,),
        ).fetchall()
    assert events == [("started", None), ("failed", "verification_failed")]


def test_cancelled_apply_restores_safe_current_and_discards_unsafe_legacy_target(
    tmp_path,
):
    settings = _settings()
    initial = runtime_snapshot_from_settings(settings)
    provider = RuntimeConfigProvider(initial)
    unsafe_target = _snapshot_with_value(
        initial,
        "request.deadline_seconds",
        300,
    )
    provider._previous = unsafe_target
    store = _store(tmp_path)

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
    draft = _create_draft(
        store=store,
        settings=settings,
        provider=provider,
        candidate_values={"opensearch.retry_backoff_seconds": 0.2},
    )

    async def cancel_replacement():
        task = asyncio.create_task(
            controller.apply(
                draft_id=draft.draft_id,
                expected_version=initial.version,
                idempotency_key_hash="c" * 64,
                request_fingerprint="d" * 64,
                actor="admin",
            )
        )
        await verifier.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as captured:
            await task
        return captured.value

    cancellation = asyncio.run(cancel_replacement())

    assert provider.snapshot() == initial
    assert provider.previous_snapshot() is None
    [operation] = store.list_runtime_operations()
    assert operation.status == "failed"
    assert operation.failure_code == "cancelled"
    assert cancellation.operation_id == operation.operation_id
    assert cancellation.draft_id == draft.draft_id
    assert cancellation.result == "cancelled"


def test_runtime_apply_cancellation_during_reserve_finishes_terminal_audit(
    tmp_path,
    monkeypatch,
):
    settings = _settings()
    initial = runtime_snapshot_from_settings(settings)
    provider = RuntimeConfigProvider(initial)
    store = _store(tmp_path)
    controller = _controller(
        settings=settings,
        provider=provider,
        store=store,
        verifier=_Verifier(),
    )
    draft = _create_draft(
        store=store,
        settings=settings,
        provider=provider,
        candidate_values={"opensearch.max_retries": 0},
    )
    original_run_in_threadpool = runtime_module.run_in_threadpool

    async def cancel_during_reserve():
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
        task = asyncio.create_task(
            controller.apply(
                draft_id=draft.draft_id,
                expected_version=initial.version,
                idempotency_key_hash="a" * 64,
                request_fingerprint="b" * 64,
                actor="admin",
            )
        )
        await reserve_started.wait()
        task.cancel()
        await asyncio.sleep(0)
        release_reserve.set()
        with pytest.raises(asyncio.CancelledError) as captured:
            await task
        return captured.value

    cancellation = asyncio.run(cancel_during_reserve())

    assert provider.snapshot() == initial
    [operation] = store.list_runtime_operations()
    assert operation.status == "failed"
    assert operation.failure_code == "cancelled"
    assert cancellation.operation_id == operation.operation_id
    assert cancellation.draft_id == draft.draft_id
    assert cancellation.result == "cancelled"


def test_runtime_apply_cancellation_after_reserve_before_swap_keeps_old_snapshot(
    tmp_path,
    monkeypatch,
):
    settings = _settings()
    initial = runtime_snapshot_from_settings(settings)
    provider = RuntimeConfigProvider(initial)
    store = _store(tmp_path)
    controller = _controller(
        settings=settings,
        provider=provider,
        store=store,
        verifier=_Verifier(),
    )
    draft = _create_draft(
        store=store,
        settings=settings,
        provider=provider,
        candidate_values={"opensearch.max_retries": 0},
    )
    original_record_runtime_version = store.record_runtime_version
    replacement_version_written = Event()
    release_version_write = Event()
    write_count = 0

    def blocking_record_runtime_version(snapshot):
        nonlocal write_count
        write_count += 1
        original_record_runtime_version(snapshot)
        if write_count == 2:
            replacement_version_written.set()
            release_version_write.wait(timeout=5)

    monkeypatch.setattr(
        store,
        "record_runtime_version",
        blocking_record_runtime_version,
    )

    async def cancel_before_swap():
        task = asyncio.create_task(
            controller.apply(
                draft_id=draft.draft_id,
                expected_version=initial.version,
                idempotency_key_hash="c" * 64,
                request_fingerprint="d" * 64,
                actor="admin",
            )
        )
        assert await asyncio.to_thread(replacement_version_written.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release_version_write.set()
        with pytest.raises(asyncio.CancelledError) as captured:
            await task
        return captured.value

    cancellation = asyncio.run(cancel_before_swap())

    assert provider.snapshot() == initial
    assert provider.previous_snapshot() is None
    [operation] = store.list_runtime_operations()
    assert operation.status == "failed"
    assert operation.failure_code == "cancelled"
    assert cancellation.operation_id == operation.operation_id
    assert cancellation.draft_id == draft.draft_id
    assert cancellation.result == "cancelled"


def test_runtime_apply_terminal_event_is_the_cancellation_commit_point(
    tmp_path,
    monkeypatch,
):
    settings = _settings()
    initial = runtime_snapshot_from_settings(settings)
    provider = RuntimeConfigProvider(initial)
    store = _store(tmp_path)
    controller = _controller(
        settings=settings,
        provider=provider,
        store=store,
        verifier=_Verifier(),
    )
    draft = _create_draft(
        store=store,
        settings=settings,
        provider=provider,
        candidate_values={"opensearch.max_retries": 0},
    )
    original_run_in_threadpool = runtime_module.run_in_threadpool

    async def cancel_during_terminal_commit():
        terminal_started = asyncio.Event()
        release_terminal = asyncio.Event()

        async def controlled_run_in_threadpool(function, *args, **kwargs):
            if (
                getattr(function, "__self__", None) is store
                and getattr(function, "__name__", "") == "append_runtime_event"
                and kwargs.get("event_type") == "applied"
            ):
                terminal_started.set()
                await release_terminal.wait()
            return await original_run_in_threadpool(function, *args, **kwargs)

        monkeypatch.setattr(
            runtime_module,
            "run_in_threadpool",
            controlled_run_in_threadpool,
        )
        task = asyncio.create_task(
            controller.apply(
                draft_id=draft.draft_id,
                expected_version=initial.version,
                idempotency_key_hash="5" * 64,
                request_fingerprint="6" * 64,
                actor="admin",
            )
        )
        await terminal_started.wait()
        task.cancel()
        await asyncio.sleep(0)
        release_terminal.set()
        with pytest.raises(asyncio.CancelledError) as captured:
            await task
        replayed = await controller.apply(
            draft_id=draft.draft_id,
            expected_version=initial.version,
            idempotency_key_hash="5" * 64,
            request_fingerprint="6" * 64,
            actor="admin",
        )
        return captured.value, replayed

    cancellation, replayed = asyncio.run(cancel_during_terminal_commit())

    assert provider.snapshot().value_for("opensearch.max_retries") == 0
    assert replayed.status == "applied"
    [operation] = store.list_runtime_operations()
    assert operation.status == "applied"
    assert cancellation.operation_id == operation.operation_id
    assert cancellation.draft_id == draft.draft_id
    assert cancellation.result == "applied"


def test_runtime_apply_cancellation_after_swap_restores_and_survives_repeat_cancel(
    tmp_path,
):
    settings = _settings()
    initial = runtime_snapshot_from_settings(settings)
    provider = RuntimeConfigProvider(initial)
    store = _store(tmp_path)

    class BlockingVerifier:
        def __init__(self):
            self.replacement_started = asyncio.Event()
            self.restore_started = asyncio.Event()
            self.restore_release = asyncio.Event()

        async def verify(self, snapshot) -> None:
            if snapshot.version != initial.version:
                self.replacement_started.set()
                await asyncio.Event().wait()
            self.restore_started.set()
            await self.restore_release.wait()

    verifier = BlockingVerifier()
    controller = RuntimeConfigController(
        settings=settings,
        provider=provider,
        store=store,
        verifier=verifier,
        mutation_min_interval_seconds=0,
    )
    draft = _create_draft(
        store=store,
        settings=settings,
        provider=provider,
        candidate_values={"opensearch.max_retries": 0},
    )

    async def cancel_after_swap():
        task = asyncio.create_task(
            controller.apply(
                draft_id=draft.draft_id,
                expected_version=initial.version,
                idempotency_key_hash="e" * 64,
                request_fingerprint="f" * 64,
                actor="admin",
            )
        )
        await verifier.replacement_started.wait()
        assert provider.snapshot().version != initial.version
        task.cancel()
        await verifier.restore_started.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        verifier.restore_release.set()
        with pytest.raises(asyncio.CancelledError) as captured:
            await task
        return captured.value

    cancellation = asyncio.run(cancel_after_swap())

    assert provider.snapshot() == initial
    assert provider.previous_snapshot() is None
    [operation] = store.list_runtime_operations()
    assert operation.status == "failed"
    assert operation.failure_code == "cancelled"
    assert cancellation.operation_id == operation.operation_id
    assert cancellation.draft_id == draft.draft_id
    assert cancellation.result == "cancelled"


def test_runtime_rollback_cancellation_after_swap_restores_manual_rollback_target(
    tmp_path,
):
    settings = _settings()
    initial = runtime_snapshot_from_settings(settings)
    provider = RuntimeConfigProvider(initial)
    store = _store(tmp_path)
    apply_controller = _controller(
        settings=settings,
        provider=provider,
        store=store,
        verifier=_Verifier(),
    )
    draft = _create_draft(
        store=store,
        settings=settings,
        provider=provider,
        candidate_values={"opensearch.max_retries": 0},
    )

    class BlockFirstVerification:
        def __init__(self):
            self.started = asyncio.Event()
            self.calls = 0

        async def verify(self, _snapshot) -> None:
            self.calls += 1
            if self.calls == 1:
                self.started.set()
                await asyncio.Event().wait()

    verifier = BlockFirstVerification()

    async def apply_then_cancel_rollback():
        await apply_controller.apply(
            draft_id=draft.draft_id,
            expected_version=initial.version,
            idempotency_key_hash="1" * 64,
            request_fingerprint="2" * 64,
            actor="admin",
        )
        applied = provider.snapshot()
        rollback_controller = RuntimeConfigController(
            settings=settings,
            provider=provider,
            store=store,
            verifier=verifier,
            mutation_min_interval_seconds=0,
        )
        task = asyncio.create_task(
            rollback_controller.rollback(
                expected_version=applied.version,
                target_version=initial.version,
                reason="验证取消时恢复回滚前快照",
                idempotency_key_hash="3" * 64,
                request_fingerprint="4" * 64,
                actor="admin",
            )
        )
        await verifier.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as captured:
            await task
        return applied, captured.value

    applied, cancellation = asyncio.run(apply_then_cancel_rollback())

    assert provider.snapshot() == applied
    assert provider.previous_snapshot() == initial
    failed = store.list_runtime_operations()[0]
    assert failed.kind == "rollback"
    assert failed.status == "failed"
    assert failed.failure_code == "cancelled"
    assert cancellation.operation_id == failed.operation_id
    assert cancellation.draft_id is None
    assert cancellation.result == "cancelled"


def test_failed_restore_verification_keeps_the_restored_snapshot_and_terminal_audit(tmp_path):
    settings = _settings()
    initial = runtime_snapshot_from_settings(settings)
    provider = RuntimeConfigProvider(initial)
    store = _store(tmp_path)

    class AlwaysFailVerifier:
        async def verify(self, _snapshot) -> None:
            raise RuntimeConfigApplyError(
                "dependency unavailable",
                failure_code="verification_failed",
            )

    controller = RuntimeConfigController(
        settings=settings,
        provider=provider,
        store=store,
        verifier=AlwaysFailVerifier(),
        mutation_min_interval_seconds=0,
    )
    draft = _create_draft(
        store=store,
        settings=settings,
        provider=provider,
        candidate_values={"opensearch.max_retries": 0},
    )

    async def apply_with_failed_restore_verification():
        with pytest.raises(RuntimeConfigApplyError) as captured:
            await controller.apply(
                draft_id=draft.draft_id,
                expected_version=initial.version,
                idempotency_key_hash="f" * 64,
                request_fingerprint="e" * 64,
                actor="admin",
            )
        return captured.value

    error = asyncio.run(apply_with_failed_restore_verification())

    assert error.failure_code == "rollback_failed"
    assert provider.snapshot() == initial
    [operation] = store.list_runtime_operations()
    assert operation.status == "failed"
    assert operation.failure_code == "rollback_failed"


class _Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class _ReplacingClient:
    def __init__(self, *, provider, initial, replacement):
        self.provider = provider
        self.initial = initial
        self.replacement = replacement
        self.calls = []

    def search(self, index, body, params=None):
        self.calls.append(params["request_timeout"].total)
        if len(self.calls) == 1:
            self.provider.replace(
                expected_version=self.initial.version,
                replacement=self.replacement,
            )
            raise OpenSearchConnectionError(
                "N/A",
                "connection failed",
                OSError("test dependency failure"),
            )
        return EMPTY_SEARCH_RESPONSE


def test_runtime_cooldown_rejects_fresh_keys_before_creating_audit_rows(tmp_path):
    settings = _settings()
    initial = runtime_snapshot_from_settings(settings)
    provider = RuntimeConfigProvider(initial)
    store = _store(tmp_path)
    clock = _Clock()
    controller = RuntimeConfigController(
        settings=settings,
        provider=provider,
        store=store,
        verifier=_Verifier(),
        mutation_min_interval_seconds=60,
        clock=clock,
    )
    draft = _create_draft(
        store=store,
        settings=settings,
        provider=provider,
        candidate_values={"opensearch.max_retries": 0},
    )

    async def apply_then_attempt_fresh_keys():
        applied = await controller.apply(
            draft_id=draft.draft_id,
            expected_version=initial.version,
            idempotency_key_hash="a" * 64,
            request_fingerprint="b" * 64,
            actor="admin",
        )
        replayed = await controller.apply(
            draft_id=draft.draft_id,
            expected_version=initial.version,
            idempotency_key_hash="a" * 64,
            request_fingerprint="b" * 64,
            actor="admin",
        )
        rate_limit_errors = []
        for key in ("c", "d", "e"):
            with pytest.raises(RuntimeConfigRateLimitedError) as captured:
                await controller.apply(
                    draft_id=draft.draft_id,
                    expected_version=provider.snapshot().version,
                    idempotency_key_hash=key * 64,
                    request_fingerprint=key.upper() * 64,
                    actor="admin",
                )
            rate_limit_errors.append(captured.value)
        return applied, replayed, rate_limit_errors

    applied, replayed, rate_limit_errors = asyncio.run(apply_then_attempt_fresh_keys())

    assert replayed == applied
    assert all(error.operation_id is None for error in rate_limit_errors)
    assert all(error.draft_id == draft.draft_id for error in rate_limit_errors)
    assert [operation.status for operation in store.list_runtime_operations()] == [
        "applied"
    ]
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM config_runtime_operation_events"
        ).fetchone()[0] == 2


def test_repository_uses_one_request_snapshot_when_runtime_values_change_mid_retry():
    settings = _settings()
    initial = runtime_snapshot_from_settings(settings)
    values = initial.as_dict()
    values.update(
        {
            "opensearch.timeout_seconds": 4,
            "opensearch.max_retries": 0,
            "opensearch.retry_backoff_seconds": 0,
            "request.deadline_seconds": 12,
        }
    )
    replacement = new_runtime_snapshot(
        settings,
        values=values,
        parent_version=initial.version,
    )
    provider = RuntimeConfigProvider(initial)
    client = _ReplacingClient(
        provider=provider,
        initial=initial,
        replacement=replacement,
    )
    clock = _Clock()
    repository = OpenSearchRepository(
        settings=settings,
        client=client,
        clock=clock,
        sleeper=clock.sleep,
        runtime_config_provider=provider,
    )

    with runtime_config_scope(initial), request_deadline(20, clock=clock):
        assert repository.search({"query": {"match_all": {}}}) == EMPTY_SEARCH_RESPONSE

    assert client.calls == [9, 9]
    assert clock.sleeps == [0.5]
    assert provider.snapshot() == replacement
    assert repository.runtime_config_snapshot() == replacement

    assert repository.search({"query": {"match_all": {}}}) == EMPTY_SEARCH_RESPONSE
    assert client.calls[-1] == 4
