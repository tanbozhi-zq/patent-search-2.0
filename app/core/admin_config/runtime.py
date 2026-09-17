"""Single-instance immutable runtime configuration snapshots and operations."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from hashlib import sha256
import math
from secrets import token_bytes
from threading import RLock
from time import monotonic
from typing import Awaitable, Callable, Protocol, TypeVar

from starlette.concurrency import run_in_threadpool

from app.core.admin_config.fingerprint import baseline_fingerprint, canonical_json
from app.core.admin_config.models import (
    AdminConfigStoreBusyError,
    AdminConfigStoreError,
    RuntimeConfigApplyError,
    RuntimeConfigConflictError,
    RuntimeConfigOperation,
    RuntimeConfigOperationError,
    RuntimeConfigRateLimitedError,
    RuntimeConfigSnapshot,
)
from app.core.admin_config.registry import (
    CONFIG_PARAMETER_REGISTRY,
    RUNTIME_RELOAD_KEYS,
    config_definition,
    current_config_values,
)
from app.core.admin_config.store import AdminConfigDraftStore
from app.core.config import Settings


_runtime_snapshot_context: ContextVar[RuntimeConfigSnapshot | None] = ContextVar(
    "runtime_config_snapshot",
    default=None,
)
DEFAULT_RUNTIME_MUTATION_MIN_INTERVAL_SECONDS = 10.0
_AwaitedValue = TypeVar("_AwaitedValue")


def _validate_runtime_timing_values(values: dict[str, int | float]) -> None:
    """Defend timing snapshots with the same hard limits used at startup."""

    for key in ("opensearch.timeout_seconds", "request.deadline_seconds"):
        definition = config_definition(key)
        value = values.get(key)
        if definition.value_type == "integer":
            valid_type = type(value) is int
        else:
            valid_type = (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
            )
            if valid_type:
                try:
                    valid_type = math.isfinite(value)
                except (TypeError, ValueError, OverflowError):
                    valid_type = False
        if (
            not valid_type
            or value is None
            or value < definition.minimum
            or value > definition.maximum
        ):
            raise ValueError(f"runtime configuration value is invalid: {key}")


def _with_cancellation_context(
    cancellation: asyncio.CancelledError,
    *,
    operation: RuntimeConfigOperation,
    result: str,
) -> asyncio.CancelledError:
    """Keep cancellation semantics while carrying domain audit correlation."""

    cancellation.operation_id = operation.operation_id
    cancellation.draft_id = operation.draft_id
    cancellation.result = result
    return cancellation


def _with_operation_error_context(
    cancellation: asyncio.CancelledError,
    error: RuntimeConfigOperationError,
) -> asyncio.CancelledError:
    """Copy only safe correlation when cancellation wins an inner race."""

    if error.operation_id is not None:
        cancellation.operation_id = error.operation_id
    if error.draft_id is not None:
        cancellation.draft_id = error.draft_id
    return cancellation


async def _complete_despite_cancellation(
    awaitable: Awaitable[_AwaitedValue],
) -> tuple[_AwaitedValue, asyncio.CancelledError | None]:
    """Finish one critical awaitable while remembering caller cancellation.

    Shielding alone would let the caller leave while the durable operation kept
    running. This helper instead waits for the protected task to reach a known
    outcome, then lets the controller restore/audit before cancellation escapes.
    """

    task = asyncio.ensure_future(awaitable)
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            if task.done() and task.cancelled():
                raise
            cancellation = cancellation or exc
        except Exception:
            break
    try:
        result = task.result()
    except Exception as exc:
        if cancellation is not None:
            if isinstance(exc, RuntimeConfigOperationError):
                _with_operation_error_context(cancellation, exc)
            raise cancellation from exc
        raise
    return result, cancellation


def runtime_snapshot_from_settings(settings: Settings) -> RuntimeConfigSnapshot:
    values = current_config_values(settings)
    _validate_runtime_timing_values(values)
    fingerprint = baseline_fingerprint(settings, values=values)
    return RuntimeConfigSnapshot(
        # Keep the original #59 fingerprint as the first runtime version. Existing
        # validated drafts therefore remain usable until an actual runtime change.
        version=fingerprint,
        fingerprint=fingerprint,
        source="deployment_baseline",
        created_at=datetime.now(timezone.utc),
        values=tuple(sorted(values.items())),
    )


def new_runtime_snapshot(
    settings: Settings,
    *,
    values: dict[str, int | float],
    parent_version: str,
) -> RuntimeConfigSnapshot:
    registered = {definition.key for definition in CONFIG_PARAMETER_REGISTRY}
    if set(values) != registered:
        raise ValueError("runtime configuration must contain every registered key")
    _validate_runtime_timing_values(values)
    created_at = datetime.now(timezone.utc)
    fingerprint = baseline_fingerprint(settings, values=values)
    # A new generation is required even when a manual rollback restores identical
    # values. CAS therefore rejects stale requests that only match an old content
    # fingerprint.
    version = sha256(
        canonical_json(
            {
                "fingerprint": fingerprint,
                "parent_version": parent_version,
                "nonce": token_bytes(16).hex(),
            }
        ).encode("utf-8")
    ).hexdigest()
    return RuntimeConfigSnapshot(
        version=version,
        fingerprint=fingerprint,
        source="runtime_override",
        created_at=created_at,
        values=tuple(sorted(values.items())),
        parent_version=parent_version,
    )


class RuntimeConfigProvider:
    """One process-local pointer to a complete immutable configuration snapshot."""

    def __init__(self, initial: RuntimeConfigSnapshot):
        _validate_runtime_timing_values(initial.as_dict())
        self._current = initial
        self._previous: RuntimeConfigSnapshot | None = None
        self._lock = RLock()

    def snapshot(self) -> RuntimeConfigSnapshot:
        with self._lock:
            return self._current

    def previous_snapshot(self) -> RuntimeConfigSnapshot | None:
        with self._lock:
            return self._previous

    def snapshots(
        self,
    ) -> tuple[RuntimeConfigSnapshot, RuntimeConfigSnapshot | None]:
        """Read the current and rollback snapshots under the same lock."""

        with self._lock:
            return self._current, self._previous

    def replace(
        self,
        *,
        expected_version: str,
        replacement: RuntimeConfigSnapshot,
    ) -> RuntimeConfigSnapshot:
        _validate_runtime_timing_values(replacement.as_dict())
        with self._lock:
            if self._current.version != expected_version:
                raise RuntimeConfigConflictError("runtime configuration version changed")
            previous = self._current
            self._current = replacement
            self._previous = previous
            return previous

    def restore(
        self,
        *,
        expected_version: str,
        previous: RuntimeConfigSnapshot,
        rollback_target: RuntimeConfigSnapshot | None,
    ) -> None:
        _validate_runtime_timing_values(previous.as_dict())
        if rollback_target is not None:
            try:
                _validate_runtime_timing_values(rollback_target.as_dict())
            except ValueError:
                # Restoring the verified current snapshot is the safety
                # invariant. An unsafe historical pointer must never block it.
                rollback_target = None
        with self._lock:
            if self._current.version != expected_version:
                raise RuntimeConfigConflictError("runtime configuration rollback version changed")
            self._current = previous
            # A failed replacement must not erase the manual rollback target
            # that existed before the attempted apply.
            self._previous = rollback_target


@contextmanager
def runtime_config_scope(snapshot: RuntimeConfigSnapshot):
    token: Token[RuntimeConfigSnapshot | None] = _runtime_snapshot_context.set(snapshot)
    try:
        yield
    finally:
        _runtime_snapshot_context.reset(token)


def current_runtime_config(provider: RuntimeConfigProvider) -> RuntimeConfigSnapshot:
    return _runtime_snapshot_context.get() or provider.snapshot()


class RuntimeConfigVerifier(Protocol):
    async def verify(self, snapshot: RuntimeConfigSnapshot) -> None:
        """Raise RuntimeConfigApplyError if the new snapshot is not safe to retain."""


class CallbackRuntimeConfigVerifier:
    """Small adapter that keeps health verification outside configuration state."""

    def __init__(
        self,
        check: Callable[[RuntimeConfigSnapshot], Awaitable[bool]],
    ) -> None:
        self._check = check

    async def verify(self, snapshot: RuntimeConfigSnapshot) -> None:
        if not await self._check(snapshot):
            raise RuntimeConfigApplyError(
                "runtime configuration verification failed",
                failure_code="verification_failed",
            )


class RuntimeConfigController:
    """Serialize apply/rollback, audit each operation, and restore on failure."""

    def __init__(
        self,
        *,
        settings: Settings,
        provider: RuntimeConfigProvider,
        store: AdminConfigDraftStore,
        verifier: RuntimeConfigVerifier,
        mutation_min_interval_seconds: float = DEFAULT_RUNTIME_MUTATION_MIN_INTERVAL_SECONDS,
        clock: Callable[[], float] = monotonic,
    ):
        self._settings = settings
        self._provider = provider
        self._store = store
        self._verifier = verifier
        self._mutation_min_interval_seconds = mutation_min_interval_seconds
        self._clock = clock
        self._lock = asyncio.Lock()
        self._last_mutation_at: float | None = None

    async def apply(
        self,
        *,
        draft_id: str,
        expected_version: str,
        idempotency_key_hash: str,
        request_fingerprint: str,
        actor: str,
    ) -> RuntimeConfigOperation:
        async with self._lock:
            operation, created = await self._reserve_or_replay(
                idempotency_key_hash=idempotency_key_hash,
                request_fingerprint=request_fingerprint,
                kind="apply",
                actor=actor,
                reason="runtime configuration apply",
                expected_version=expected_version,
                draft_id=draft_id,
            )
            if not created:
                return operation
            current = self._provider.snapshot()
            replacement: RuntimeConfigSnapshot | None = None
            previous: RuntimeConfigSnapshot | None = None
            rollback_target: RuntimeConfigSnapshot | None = None
            terminal: RuntimeConfigOperation | None = None
            terminal_committed = False
            try:
                if current.version != expected_version:
                    raise RuntimeConfigConflictError("runtime configuration version changed")
                draft = await self._await_critical(
                    run_in_threadpool(
                        self._store.get,
                        settings=self._settings,
                        draft_id=draft_id,
                        current_fingerprint=current.version,
                    )
                )
                if draft is None:
                    raise RuntimeConfigApplyError(
                        "runtime configuration draft does not exist",
                        failure_code="draft_not_found",
                    )
                if draft.status == "expired":
                    raise RuntimeConfigConflictError("runtime configuration draft expired")
                if draft.status != "validated":
                    raise RuntimeConfigApplyError(
                        "runtime configuration draft is not validated",
                        failure_code="draft_not_validated",
                    )
                if not draft.diff or set(draft.diff) - RUNTIME_RELOAD_KEYS:
                    raise RuntimeConfigApplyError(
                        "runtime configuration draft contains restart-only values",
                        failure_code="unsupported_apply_mode",
                    )

                values = current.as_dict()
                values.update(draft.candidate_values)
                replacement = new_runtime_snapshot(
                    self._settings,
                    values=values,
                    parent_version=current.version,
                )
                # Both sides of the swap are persisted before the in-memory
                # pointer changes. The store is audit-only: it is never read
                # at startup to replay a stale runtime override.
                await self._await_critical(
                    run_in_threadpool(self._store.record_runtime_version, current)
                )
                await self._await_critical(
                    run_in_threadpool(self._store.record_runtime_version, replacement)
                )
                rollback_target = self._provider.previous_snapshot()
                previous = self._provider.replace(
                    expected_version=current.version,
                    replacement=replacement,
                )
                await self._verify_readback(replacement)
                terminal, cancellation = await _complete_despite_cancellation(
                    run_in_threadpool(
                        self._store.append_runtime_event,
                        operation_id=operation.operation_id,
                        event_type="applied",
                        previous_version=previous.version,
                        current_version=replacement.version,
                    )
                )
                terminal_committed = True
                if cancellation is not None:
                    raise cancellation
                return terminal
            except asyncio.CancelledError as cancellation:
                if terminal_committed:
                    if terminal is None:
                        terminal = operation
                    raise _with_cancellation_context(
                        cancellation,
                        operation=terminal,
                        result=terminal.status,
                    )
                contextual_cancellation = _with_cancellation_context(
                    cancellation,
                    operation=operation,
                    result="cancelled",
                )
                await self._recover_cancelled_operation(
                    cancellation=contextual_cancellation,
                    operation=operation,
                    replacement=replacement,
                    previous=previous,
                    rollback_target=rollback_target,
                )
                raise contextual_cancellation
            except Exception as exc:
                error = self._operation_error(exc, operation=operation)
                try:
                    await self._await_critical(
                        self._finish_failed_operation(
                            operation=operation,
                            replacement=replacement,
                            previous=previous,
                            rollback_target=rollback_target,
                            failure_code=self._failure_code(error),
                        )
                    )
                except RuntimeConfigApplyError as recovery_error:
                    raise recovery_error.with_operation_context(operation)
                raise error

    async def rollback(
        self,
        *,
        expected_version: str,
        target_version: str,
        reason: str,
        idempotency_key_hash: str,
        request_fingerprint: str,
        actor: str,
    ) -> RuntimeConfigOperation:
        async with self._lock:
            operation, created = await self._reserve_or_replay(
                idempotency_key_hash=idempotency_key_hash,
                request_fingerprint=request_fingerprint,
                kind="rollback",
                actor=actor,
                reason=reason,
                expected_version=expected_version,
                draft_id=None,
            )
            if not created:
                return operation
            current = self._provider.snapshot()
            replacement: RuntimeConfigSnapshot | None = None
            previous: RuntimeConfigSnapshot | None = None
            rollback_target: RuntimeConfigSnapshot | None = None
            terminal: RuntimeConfigOperation | None = None
            terminal_committed = False
            try:
                if current.version != expected_version:
                    raise RuntimeConfigConflictError("runtime configuration version changed")
                target = self._provider.previous_snapshot()
                if target is None or target.version != target_version:
                    raise RuntimeConfigConflictError("runtime configuration rollback target changed")
                replacement = new_runtime_snapshot(
                    self._settings,
                    values=target.as_dict(),
                    parent_version=current.version,
                )
                await self._await_critical(
                    run_in_threadpool(self._store.record_runtime_version, current)
                )
                await self._await_critical(
                    run_in_threadpool(self._store.record_runtime_version, replacement)
                )
                rollback_target = self._provider.previous_snapshot()
                previous = self._provider.replace(
                    expected_version=current.version,
                    replacement=replacement,
                )
                await self._verify_readback(replacement)
                terminal, cancellation = await _complete_despite_cancellation(
                    run_in_threadpool(
                        self._store.append_runtime_event,
                        operation_id=operation.operation_id,
                        event_type="rolled_back",
                        previous_version=previous.version,
                        current_version=replacement.version,
                    )
                )
                terminal_committed = True
                if cancellation is not None:
                    raise cancellation
                return terminal
            except asyncio.CancelledError as cancellation:
                if terminal_committed:
                    if terminal is None:
                        terminal = operation
                    raise _with_cancellation_context(
                        cancellation,
                        operation=terminal,
                        result=terminal.status,
                    )
                contextual_cancellation = _with_cancellation_context(
                    cancellation,
                    operation=operation,
                    result="cancelled",
                )
                await self._recover_cancelled_operation(
                    cancellation=contextual_cancellation,
                    operation=operation,
                    replacement=replacement,
                    previous=previous,
                    rollback_target=rollback_target,
                )
                raise contextual_cancellation
            except Exception as exc:
                error = self._operation_error(exc, operation=operation)
                try:
                    await self._await_critical(
                        self._finish_failed_operation(
                            operation=operation,
                            replacement=replacement,
                            previous=previous,
                            rollback_target=rollback_target,
                            failure_code=self._failure_code(error),
                        )
                    )
                except RuntimeConfigApplyError as recovery_error:
                    raise recovery_error.with_operation_context(operation)
                raise error

    async def _reserve_or_replay(
        self,
        *,
        idempotency_key_hash: str,
        request_fingerprint: str,
        kind: str,
        actor: str,
        reason: str,
        expected_version: str,
        draft_id: str | None,
    ) -> tuple[RuntimeConfigOperation, bool]:
        existing, cancellation = await _complete_despite_cancellation(
            run_in_threadpool(
                self._store.find_runtime_operation,
                idempotency_key_hash=idempotency_key_hash,
                request_fingerprint=request_fingerprint,
            )
        )
        if cancellation is not None:
            if existing is not None:
                try:
                    existing = self._repeat_or_raise(existing)
                except RuntimeConfigOperationError as exc:
                    raise _with_operation_error_context(
                        cancellation,
                        exc,
                    ) from exc
                raise _with_cancellation_context(
                    cancellation,
                    operation=existing,
                    result=existing.status,
                )
            raise cancellation
        if existing is not None:
            return self._repeat_or_raise(existing), False

        # Admission happens before any durable request/event row is created.
        # Otherwise a caller can bypass the mutation cooldown with fresh keys
        # and turn rejected requests into unbounded SQLite audit writes. A
        # matching completed key was handled above and remains replayable.
        self._begin_mutation(draft_id=draft_id)
        reservation, cancellation = await _complete_despite_cancellation(
            run_in_threadpool(
                self._store.reserve_runtime_operation,
                idempotency_key_hash=idempotency_key_hash,
                request_fingerprint=request_fingerprint,
                kind=kind,
                actor=actor,
                reason=reason,
                expected_version=expected_version,
                draft_id=draft_id,
            )
        )
        operation, created = reservation
        if cancellation is not None:
            contextual_cancellation = _with_cancellation_context(
                cancellation,
                operation=operation,
                result="cancelled",
            )
            if created:
                try:
                    await _complete_despite_cancellation(
                        self._record_failed_without_swap(
                            operation.operation_id,
                            current_version=self._provider.snapshot().version,
                            failure_code="cancelled",
                        )
                    )
                except (asyncio.CancelledError, Exception) as audit_error:
                    raise contextual_cancellation from audit_error
            raise contextual_cancellation
        if not created:
            return self._repeat_or_raise(operation), False
        return operation, True

    def _begin_mutation(self, *, draft_id: str | None) -> None:
        self._enforce_rate_limit(draft_id=draft_id)
        # Count rejected and failed mutations too; otherwise a failing health
        # check could be used to create an unbounded probe/audit workload.
        self._last_mutation_at = self._clock()

    def _enforce_rate_limit(self, *, draft_id: str | None) -> None:
        if self._last_mutation_at is None:
            return
        if self._clock() - self._last_mutation_at < self._mutation_min_interval_seconds:
            raise RuntimeConfigRateLimitedError(
                "runtime configuration mutation rate limited",
                draft_id=draft_id,
            )

    async def _verify_readback(self, expected: RuntimeConfigSnapshot) -> None:
        observed = self._provider.snapshot()
        if observed.version != expected.version or observed.values != expected.values:
            raise RuntimeConfigApplyError(
                "runtime configuration readback did not match replacement",
                failure_code="readback_mismatch",
            )
        await self._verifier.verify(expected)

    @staticmethod
    async def _await_critical(
        awaitable: Awaitable[_AwaitedValue],
    ) -> _AwaitedValue:
        result, cancellation = await _complete_despite_cancellation(awaitable)
        if cancellation is not None:
            raise cancellation
        return result

    async def _finish_failed_operation(
        self,
        *,
        operation: RuntimeConfigOperation,
        replacement: RuntimeConfigSnapshot | None,
        previous: RuntimeConfigSnapshot | None,
        rollback_target: RuntimeConfigSnapshot | None,
        failure_code: str,
    ) -> None:
        if (
            replacement is not None
            and previous is not None
            and self._provider.snapshot().version == replacement.version
        ):
            await self._restore_after_failure(
                operation_id=operation.operation_id,
                attempted=replacement,
                previous=previous,
                rollback_target=rollback_target,
                failure_code=failure_code,
            )
            return
        await self._record_failed_without_swap(
            operation.operation_id,
            current_version=self._provider.snapshot().version,
            failure_code=failure_code,
        )

    async def _recover_cancelled_operation(
        self,
        *,
        cancellation: asyncio.CancelledError,
        operation: RuntimeConfigOperation,
        replacement: RuntimeConfigSnapshot | None,
        previous: RuntimeConfigSnapshot | None,
        rollback_target: RuntimeConfigSnapshot | None,
    ) -> None:
        try:
            await _complete_despite_cancellation(
                self._finish_failed_operation(
                    operation=operation,
                    replacement=replacement,
                    previous=previous,
                    rollback_target=rollback_target,
                    failure_code="cancelled",
                )
            )
        except (asyncio.CancelledError, Exception) as recovery_error:
            raise cancellation from recovery_error

    async def _restore_after_failure(
        self,
        *,
        operation_id: str,
        attempted: RuntimeConfigSnapshot,
        previous: RuntimeConfigSnapshot,
        rollback_target: RuntimeConfigSnapshot | None,
        failure_code: str,
    ) -> None:
        try:
            self._provider.restore(
                expected_version=attempted.version,
                previous=previous,
                rollback_target=rollback_target,
            )
        except Exception as rollback_exc:
            await self._append_failed_event(
                operation_id=operation_id,
                previous_version=attempted.version,
                current_version=self._provider.snapshot().version,
                failure_code="rollback_failed",
            )
            raise RuntimeConfigApplyError(
                "runtime configuration rollback failed",
                failure_code="rollback_failed",
            ) from rollback_exc
        try:
            await self._verify_readback(previous)
        except Exception as rollback_exc:
            await self._append_failed_event(
                operation_id=operation_id,
                previous_version=attempted.version,
                current_version=self._provider.snapshot().version,
                failure_code="rollback_failed",
            )
            raise RuntimeConfigApplyError(
                "runtime configuration rollback failed",
                failure_code="rollback_failed",
            ) from rollback_exc
        await self._append_failed_event(
            operation_id=operation_id,
            previous_version=attempted.version,
            current_version=previous.version,
            failure_code=failure_code,
        )

    async def _append_failed_event(
        self,
        *,
        operation_id: str,
        previous_version: str,
        current_version: str,
        failure_code: str,
    ) -> None:
        try:
            await run_in_threadpool(
                self._store.append_runtime_event,
                operation_id=operation_id,
                event_type="failed",
                previous_version=previous_version,
                current_version=current_version,
                failure_code=failure_code,
            )
        except AdminConfigStoreBusyError as exc:
            raise RuntimeConfigApplyError(
                "runtime configuration audit is busy",
                failure_code="audit_store_busy",
            ) from exc
        except AdminConfigStoreError as exc:
            # The pointer may already be restored, but a missing terminal
            # audit event is still a control-plane failure and must not look
            # successful to the caller.
            raise RuntimeConfigApplyError(
                "runtime configuration audit could not be completed",
                failure_code="audit_store_failed",
            ) from exc

    async def _record_failed_without_swap(
        self,
        operation_id: str,
        *,
        current_version: str,
        failure_code: str,
    ) -> None:
        try:
            await run_in_threadpool(
                self._store.append_runtime_event,
                operation_id=operation_id,
                event_type="failed",
                previous_version=current_version,
                current_version=current_version,
                failure_code=failure_code,
            )
        except AdminConfigStoreError:
            # The existing started event remains an honest incomplete audit record.
            return

    @staticmethod
    def _operation_error(
        exc: Exception,
        *,
        operation: RuntimeConfigOperation,
    ) -> RuntimeConfigConflictError | RuntimeConfigRateLimitedError | RuntimeConfigApplyError:
        if isinstance(
            exc,
            (RuntimeConfigConflictError, RuntimeConfigRateLimitedError, RuntimeConfigApplyError),
        ):
            error = exc
        elif isinstance(exc, AdminConfigStoreBusyError):
            error = RuntimeConfigApplyError(str(exc), failure_code="audit_store_busy")
        elif isinstance(exc, AdminConfigStoreError):
            error = RuntimeConfigApplyError(str(exc), failure_code="audit_store_failed")
        else:
            error = RuntimeConfigApplyError(
                "runtime configuration operation failed",
                failure_code="operation_failed",
            )
        error.with_operation_context(operation)
        return error

    @staticmethod
    def _failure_code(
        error: RuntimeConfigConflictError | RuntimeConfigRateLimitedError | RuntimeConfigApplyError,
    ) -> str:
        if isinstance(error, RuntimeConfigConflictError):
            return "version_conflict"
        if isinstance(error, RuntimeConfigRateLimitedError):
            return "rate_limited"
        return error.failure_code

    def _repeat_or_raise(self, operation: RuntimeConfigOperation) -> RuntimeConfigOperation:
        if operation.status in {"applied", "rolled_back"}:
            if operation.current_version != self._provider.snapshot().version:
                raise RuntimeConfigConflictError(
                    "runtime configuration version changed since the original request",
                    operation_id=operation.operation_id,
                    draft_id=operation.draft_id,
                )
            return operation
        if operation.failure_code == "version_conflict":
            raise RuntimeConfigConflictError(
                "runtime configuration version changed",
                operation_id=operation.operation_id,
                draft_id=operation.draft_id,
            )
        if operation.failure_code == "rate_limited":
            raise RuntimeConfigRateLimitedError(
                "runtime configuration mutation rate limited",
                operation_id=operation.operation_id,
                draft_id=operation.draft_id,
            )
        raise RuntimeConfigApplyError(
            "runtime configuration operation did not complete",
            failure_code=operation.failure_code or "operation_incomplete",
            operation_id=operation.operation_id,
            draft_id=operation.draft_id,
        )
