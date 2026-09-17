"""Controlled live acceptance for the single-instance runtime config path."""

from __future__ import annotations

import argparse
from datetime import datetime
from hashlib import sha256
import json
import os
import re
from time import monotonic, sleep
from typing import Any, Callable
from uuid import UUID, uuid4

import httpx

from app.core.admin_config import (
    ADMIN_CONFIG_APPLY_MODE_CONTRACT,
    ADMIN_CONFIG_REGISTRY_VERSION,
    DEFAULT_RUNTIME_MUTATION_MIN_INTERVAL_SECONDS,
    canonical_json,
)
from app.core.request_context import is_valid_request_id
from scripts.smoke_admin_metrics import _bounded_base_url


CONFIRMATION = "issue47-single-instance-runtime-mutation"
ACCEPTANCE_PARAMETER = "opensearch.max_retries"
ACCEPTANCE_CURRENT_VALUE = 1
ACCEPTANCE_CANDIDATE_VALUE = 0
ACCEPTANCE_MINIMUM = 0
ACCEPTANCE_MAXIMUM = 1
APPLY_OPERATION_REASON = "runtime configuration apply"
ROLLBACK_OPERATION_REASON = "Issue #47 controlled acceptance rollback"
RUNTIME_OPERATION_HISTORY_LIMIT = 20
DEFAULT_AUDIT_TIMEOUT_SECONDS = 5.0
DEFAULT_COOLDOWN_SECONDS = DEFAULT_RUNTIME_MUTATION_MIN_INTERVAL_SECONDS + 0.5
_COMMIT_PATTERN = re.compile(r"[0-9A-Fa-f]{7,64}\Z")
_VERSION_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_RUNTIME_RESPONSE_FIELDS = frozenset(
    {
        "version",
        "fingerprint",
        "source",
        "created_at",
        "rollback_version",
        "writes_enabled",
        "values",
        "recent_operations",
    }
)
_RUNTIME_OPERATION_FIELDS = frozenset(
    {
        "operation_id",
        "kind",
        "status",
        "created_at",
        "actor",
        "reason",
        "expected_version",
        "previous_version",
        "current_version",
        "draft_id",
        "failure_code",
    }
)


class AcceptanceError(RuntimeError):
    """Safe failure code without response bodies, URLs, or credentials."""

    def __init__(
        self,
        code: str,
        *,
        restored: bool | None = None,
        manual_recovery_required: bool = False,
    ) -> None:
        self.code = code
        self.restored = restored
        self.manual_recovery_required = manual_recovery_required
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise AcceptanceError(code)


def _exact_mapping(left: Any, right: Any) -> bool:
    return (
        isinstance(left, dict)
        and isinstance(right, dict)
        and set(left) == set(right)
        and all(
            type(left[key]) is type(right[key]) and left[key] == right[key]
            for key in left
        )
    )


def _exact_runtime_payload(left: Any, right: Any) -> bool:
    if not (
        _runtime_snapshot_has_contract(left)
        and _runtime_snapshot_has_contract(right)
    ):
        return False
    try:
        return canonical_json(left) == canonical_json(right)
    except (TypeError, ValueError):
        return False


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _version(value: Any) -> bool:
    return isinstance(value, str) and _VERSION_PATTERN.fullmatch(value) is not None


def _uuid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _parseable_uuid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        UUID(value)
        return True
    except ValueError:
        return False


def _operation_has_contract(operation: Any) -> bool:
    if not (
        isinstance(operation, dict)
        and set(operation) == _RUNTIME_OPERATION_FIELDS
        and _uuid(operation.get("operation_id"))
        and operation.get("kind") in {"apply", "rollback"}
        and operation.get("status")
        in {"applied", "rolled_back", "failed", "incomplete"}
        and _timestamp(operation.get("created_at")) is not None
        and isinstance(operation.get("actor"), str)
        and bool(operation.get("actor"))
        and isinstance(operation.get("reason"), str)
        and bool(operation.get("reason"))
        and _version(operation.get("expected_version"))
        and (
            operation.get("draft_id") is None
            or _parseable_uuid(operation.get("draft_id"))
        )
    ):
        return False

    kind = operation["kind"]
    status = operation["status"]
    previous_version = operation.get("previous_version")
    current_version = operation.get("current_version")
    failure_code = operation.get("failure_code")
    if kind == "apply":
        if (
            operation.get("draft_id") is None
            or operation.get("reason") != APPLY_OPERATION_REASON
            or status == "rolled_back"
        ):
            return False
    elif operation.get("draft_id") is not None or status == "applied":
        return False

    if status == "incomplete":
        return (
            previous_version is None
            and current_version is None
            and failure_code is None
        )
    if not (_version(previous_version) and _version(current_version)):
        return False
    if status == "failed":
        return isinstance(failure_code, str) and bool(failure_code)
    return (
        failure_code is None
        and operation.get("expected_version") == previous_version
        and current_version != previous_version
    )


def _operation_history_has_contract(value: Any) -> bool:
    if not (
        isinstance(value, list)
        and len(value) <= RUNTIME_OPERATION_HISTORY_LIMIT
        and all(_operation_has_contract(item) for item in value)
    ):
        return False
    operation_ids = [item["operation_id"] for item in value]
    ordering = [(_timestamp(item["created_at"]), item["operation_id"]) for item in value]
    return (
        len(operation_ids) == len(set(operation_ids))
        and ordering == sorted(ordering, reverse=True)
    )


def _exact_operation_history(left: Any, right: Any) -> bool:
    return (
        _operation_history_has_contract(left)
        and _operation_history_has_contract(right)
        and len(left) == len(right)
        and all(
            _exact_mapping(left_operation, right_operation)
            for left_operation, right_operation in zip(left, right, strict=True)
        )
    )


def _operation_history_is_single_transition(
    previous: Any,
    current: Any,
    operation: dict[str, Any],
) -> bool:
    if not (
        _operation_history_has_contract(previous)
        and _operation_history_has_contract(current)
        and _operation_has_contract(operation)
    ):
        return False
    operation_id = operation.get("operation_id")
    if any(item.get("operation_id") == operation_id for item in previous):
        return False
    matching_indexes = [
        index
        for index, item in enumerate(current)
        if item.get("operation_id") == operation_id
        and _exact_mapping(item, operation)
    ]
    expected_length = min(
        len(previous) + 1,
        RUNTIME_OPERATION_HISTORY_LIMIT,
    )
    if len(current) != expected_length or len(matching_indexes) != 1:
        return False
    retained = [
        item for index, item in enumerate(current) if index != matching_indexes[0]
    ]
    dropped = previous[len(retained) :]
    return _exact_operation_history(
        retained,
        previous[: len(retained)],
    ) and all(item.get("status") != "incomplete" for item in dropped)


def _runtime_snapshot_has_contract(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == _RUNTIME_RESPONSE_FIELDS
        and _version(value.get("version"))
        and _version(value.get("fingerprint"))
        and value.get("source") in {"deployment_baseline", "runtime_override"}
        and _timestamp(value.get("created_at")) is not None
        and (
            value.get("rollback_version") is None
            or _version(value.get("rollback_version"))
        )
        and type(value.get("writes_enabled")) is bool
        and isinstance(value.get("values"), dict)
        and isinstance(value.get("recent_operations"), list)
    )


def _runtime_response_has_contract(value: Any) -> bool:
    return _runtime_snapshot_has_contract(value) and _operation_history_has_contract(
        value.get("recent_operations")
    )


def _runtime_content_fingerprint(
    values: dict[str, Any],
    *,
    service_version: str,
    release_commit: str,
) -> str:
    return sha256(
        canonical_json(
            {
                "registry_version": ADMIN_CONFIG_REGISTRY_VERSION,
                "service_version": service_version,
                "release_commit": release_commit,
                "values": values,
            }
        ).encode("utf-8")
    ).hexdigest()


def _operation_within_snapshots(
    operation: dict[str, Any],
    previous: dict[str, Any],
    current: dict[str, Any],
) -> bool:
    operation_created_at = _timestamp(operation.get("created_at"))
    previous_created_at = _timestamp(previous.get("created_at"))
    current_created_at = _timestamp(current.get("created_at"))
    return (
        operation_created_at is not None
        and previous_created_at is not None
        and current_created_at is not None
        and previous_created_at <= operation_created_at <= current_created_at
    )


def _release_value_is_deployed(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value.strip().lower() != "unknown"
    )


class RuntimeAcceptance:
    """One invalid preflight plus one apply/readback/rollback cycle."""

    def __init__(
        self,
        client: Any,
        *,
        username: str,
        password: str,
        business_api_token: str,
        expected_release_commit: str,
        require_prometheus: bool,
        require_journal_audit: bool,
        check_probes: bool,
        audit_timeout_seconds: float,
        cooldown_seconds: float,
        now: Callable[[], float],
        sleeper: Callable[[float], None],
    ) -> None:
        self.client = client
        self.auth = (username, password)
        self.business_api_token = business_api_token
        self.expected_release_commit = expected_release_commit
        self.require_prometheus = require_prometheus
        self.require_journal_audit = require_journal_audit
        self.check_probes = check_probes
        self.audit_timeout_seconds = audit_timeout_seconds
        self.cooldown_seconds = cooldown_seconds
        self.now = now
        self.sleeper = sleeper

    def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return self.client.get(path, auth=self.auth, params=params)

    def post(
        self,
        path: str,
        *,
        intent: str,
        payload: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> Any:
        headers = {
            "X-Admin-Intent": intent,
            "Sec-Fetch-Site": "same-origin",
        }
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        return self.client.post(
            path,
            auth=self.auth,
            headers=headers,
            json=payload,
        )

    @staticmethod
    def payload(response: Any, *, status: int, code: str) -> dict[str, Any]:
        _require(response.status_code == status, f"{code}_status")
        try:
            payload = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise AcceptanceError(f"{code}_json") from exc
        _require(isinstance(payload, dict), f"{code}_payload")
        if status < 400:
            _require(
                "no-store" in response.headers.get("Cache-Control", "").lower(),
                f"{code}_cache",
            )
        return payload

    def read_runtime(self) -> tuple[Any, dict[str, Any]]:
        response = self.get("/admin-api/v1/runtime-config")
        return response, self.payload(response, status=200, code="runtime_read")

    @staticmethod
    def parameter(schema: dict[str, Any]) -> dict[str, Any]:
        matches = [
            item
            for item in schema.get("items", [])
            if isinstance(item, dict) and item.get("key") == ACCEPTANCE_PARAMETER
        ]
        _require(len(matches) == 1, "acceptance_parameter_missing")
        item = matches[0]
        minimum, maximum, current = (
            item.get("minimum"),
            item.get("maximum"),
            item.get("current_value"),
        )
        _require(item.get("value_type") == "integer", "acceptance_parameter_type")
        _require(item.get("apply_mode") == "runtime_reload", "acceptance_parameter_mode")
        _require(
            type(minimum) is int
            and minimum == ACCEPTANCE_MINIMUM
            and type(maximum) is int
            and maximum == ACCEPTANCE_MAXIMUM
            and type(current) is int
            and current == ACCEPTANCE_CURRENT_VALUE
            and type(item.get("default_value")) is int
            and item.get("default_value") == ACCEPTANCE_CURRENT_VALUE
            and type(item.get("rollback_value")) is int
            and item.get("rollback_value") == ACCEPTANCE_CURRENT_VALUE,
            "acceptance_parameter_contract",
        )
        return item

    @staticmethod
    def operation(
        runtime: dict[str, Any],
        *,
        kind: str,
        status: str,
        draft_id: str | None,
    ) -> dict[str, Any]:
        operations = runtime.get("recent_operations")
        _require(
            _operation_history_has_contract(operations),
            "runtime_operations_contract",
        )
        matches = [
            operation
            for operation in operations
            if isinstance(operation, dict)
            and operation.get("kind") == kind
            and operation.get("status") == status
            and operation.get("draft_id") == draft_id
            and operation.get("current_version") == runtime.get("version")
        ]
        _require(len(matches) == 1, f"runtime_{kind}_operation_not_unique")
        operation = matches[0]
        _require(
            is_valid_request_id(operation.get("operation_id", "")),
            f"runtime_{kind}_operation_id",
        )
        return operation

    def audit(
        self,
        response: Any,
        *,
        event: str,
        action: str,
        result: str,
        draft_id: str | None,
        operation_id: str | None = None,
    ) -> None:
        if not self.require_journal_audit:
            return
        request_id = response.headers.get("X-Request-ID", "")
        _require(is_valid_request_id(request_id), f"{action}_request_id")
        deadline = self.now() + self.audit_timeout_seconds
        while True:
            logs_response = self.get(
                "/admin-api/v1/logs",
                params={
                    "window_seconds": 3600,
                    "limit": 20,
                    "request_id": request_id,
                    "include_system": "true",
                },
            )
            if logs_response.status_code == 200:
                try:
                    logs = logs_response.json()
                except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
                    logs = {}
                if not isinstance(logs, dict):
                    logs = {}
                if (
                    logs.get("scope") == "journal_local"
                    and logs.get("available") is True
                    and logs.get("window_seconds") == 3600
                    and any(
                        isinstance(item, dict)
                        and item.get("event") == event
                        and item.get("request_id") == request_id
                        and item.get("action") == action
                        and item.get("result") == result
                        and item.get("actor") == self.auth[0]
                        and item.get("role") == "admin"
                        and item.get("draft_id") == draft_id
                        and (
                            operation_id is None
                            or item.get("operation_id") == operation_id
                        )
                        for item in logs.get("items", [])
                    )
                ):
                    return
            remaining = deadline - self.now()
            _require(remaining > 0, f"{action}_audit_missing")
            self.sleeper(min(0.2, remaining))

    def probes(self) -> None:
        if not self.check_probes:
            return
        for path in ("/live", "/startup", "/ready"):
            _require(self.client.get(path).status_code == 200, f"probe_{path[1:]}")

    @staticmethod
    def runtime_state_matches(
        left: dict[str, Any],
        right: dict[str, Any],
    ) -> bool:
        return (
            _runtime_response_has_contract(left)
            and _runtime_response_has_contract(right)
            and _exact_mapping(left.get("values"), right.get("values"))
            and _exact_operation_history(
                left.get("recent_operations"),
                right.get("recent_operations"),
            )
            and all(
                type(left.get(field)) is type(right.get(field))
                and left.get(field) == right.get(field)
                for field in (
                    "version",
                    "fingerprint",
                    "source",
                    "created_at",
                    "rollback_version",
                    "writes_enabled",
                )
            )
        )

    @staticmethod
    def runtime_values_are_restored(
        current: dict[str, Any],
        *,
        initial: dict[str, Any],
        applied: dict[str, Any],
    ) -> bool:
        return (
            _runtime_snapshot_has_contract(current)
            and _exact_mapping(current.get("values"), initial.get("values"))
            and current.get("version")
            not in {initial.get("version"), applied.get("version")}
            and current.get("fingerprint") == initial.get("fingerprint")
            and current.get("source") == "runtime_override"
            and current.get("rollback_version") == applied.get("version")
            and current.get("writes_enabled") is True
        )

    def correlated_apply_operation(
        self,
        current: dict[str, Any],
        *,
        initial: dict[str, Any],
        draft_id: str,
        expected_values: dict[str, Any],
        expected_fingerprint: str,
    ) -> dict[str, Any]:
        _require(
            _runtime_response_has_contract(current),
            "runtime_apply_response_contract",
        )
        _require(current.get("source") == "runtime_override", "runtime_apply_source")
        _require(current.get("writes_enabled") is True, "runtime_apply_writes_disabled")
        _require(current.get("version") != initial.get("version"), "runtime_apply_version")
        _require(
            current.get("fingerprint") == expected_fingerprint
            and expected_fingerprint != initial.get("fingerprint")
            and current.get("version") != expected_fingerprint,
            "runtime_apply_fingerprint",
        )
        _require(
            _exact_mapping(current.get("values"), expected_values),
            "runtime_apply_values",
        )
        _require(
            current.get("rollback_version") == initial.get("version"),
            "runtime_apply_target",
        )
        _require(isinstance(current.get("values"), dict), "runtime_apply_values")
        operation = self.operation(
            current,
            kind="apply",
            status="applied",
            draft_id=draft_id,
        )
        _require(
            operation.get("actor") == self.auth[0]
            and operation.get("reason") == APPLY_OPERATION_REASON
            and operation.get("expected_version") == initial.get("version")
            and operation.get("previous_version") == initial.get("version")
            and operation.get("current_version") == current.get("version")
            and operation.get("failure_code") is None
            and _operation_within_snapshots(operation, initial, current),
            "runtime_apply_operation_contract",
        )
        _require(
            _operation_history_is_single_transition(
                initial.get("recent_operations"),
                current.get("recent_operations"),
                operation,
            ),
            "runtime_apply_operation_history_transition",
        )
        return operation

    def correlated_rollback_operation(
        self,
        current: dict[str, Any],
        *,
        initial: dict[str, Any],
        applied: dict[str, Any],
    ) -> dict[str, Any]:
        _require(
            _runtime_response_has_contract(current),
            "runtime_rollback_response_contract",
        )
        operation = self.operation(
            current,
            kind="rollback",
            status="rolled_back",
            draft_id=None,
        )
        _require(
            operation.get("actor") == self.auth[0]
            and operation.get("reason") == ROLLBACK_OPERATION_REASON
            and operation.get("expected_version") == applied.get("version")
            and operation.get("previous_version") == applied.get("version")
            and operation.get("current_version") == current.get("version")
            and current.get("version")
            not in {initial.get("version"), applied.get("version")}
            and operation.get("failure_code") is None
            and _operation_within_snapshots(operation, applied, current),
            "runtime_rollback_operation_contract",
        )
        _require(
            _operation_history_is_single_transition(
                applied.get("recent_operations"),
                current.get("recent_operations"),
                operation,
            ),
            "runtime_rollback_operation_history_transition",
        )
        return operation

    def reconcile_apply_outcome(
        self,
        *,
        initial: dict[str, Any],
        draft_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
        expected_values: dict[str, Any],
        expected_fingerprint: str,
    ) -> tuple[dict[str, Any], float]:
        """Require a replay/readback pair before authorizing uncertain cleanup."""

        try:
            terminal_response = self.post(
                "/admin-api/v1/runtime-config/apply",
                intent="apply-runtime-config",
                idempotency_key=idempotency_key,
                payload=payload,
            )
        except Exception as exc:
            raise AcceptanceError(
                "runtime_apply_outcome_unknown",
                manual_recovery_required=True,
            ) from exc

        if terminal_response.status_code == 200:
            try:
                replayed = self.payload(
                    terminal_response,
                    status=200,
                    code="runtime_apply_replay",
                )
                response_operation = self.correlated_apply_operation(
                    replayed,
                    initial=initial,
                    draft_id=draft_id,
                    expected_values=expected_values,
                    expected_fingerprint=expected_fingerprint,
                )
                _unused, readback = self.read_runtime()
                readback_operation = self.correlated_apply_operation(
                    readback,
                    initial=initial,
                    draft_id=draft_id,
                    expected_values=expected_values,
                    expected_fingerprint=expected_fingerprint,
                )
                _require(
                    _exact_mapping(response_operation, readback_operation),
                    "runtime_apply_replay_operation_readback_mismatch",
                )
                _require(
                    self.runtime_state_matches(replayed, readback),
                    "runtime_apply_replay_readback_mismatch",
                )
                return readback, self.now()
            except Exception as exc:
                raise AcceptanceError(
                    "runtime_apply_outcome_unknown",
                    manual_recovery_required=True,
                ) from exc

        # A non-successful replay can coexist with a durable first apply. A GET
        # alone does not provide the response/readback pair required to authorize
        # automatic cleanup, even when it appears to carry this smoke's draft.
        raise AcceptanceError(
            "runtime_apply_outcome_unknown",
            manual_recovery_required=True,
        )

    def rollback(
        self,
        *,
        initial: dict[str, Any],
        applied: dict[str, Any],
        cooldown_started_at: float,
    ) -> bool:
        cooldown_deadline = cooldown_started_at + self.cooldown_seconds
        deferred_interruption: KeyboardInterrupt | SystemExit | None = None
        while True:
            try:
                remaining = cooldown_deadline - self.now()
                if remaining <= 0:
                    break
                self.sleeper(remaining)
            except (KeyboardInterrupt, SystemExit) as exc:
                # Once apply may have succeeded, local interruption must not
                # shorten the server-side mutation cooldown. Remember the first
                # one and continue to the original deadline before rollback.
                deferred_interruption = deferred_interruption or exc
        response = self.post(
            "/admin-api/v1/runtime-config/rollback",
            intent="rollback-runtime-config",
            idempotency_key=f"issue47-rollback-{uuid4().hex}",
            payload={
                "expected_version": applied["version"],
                "target_version": initial["version"],
                "reason": ROLLBACK_OPERATION_REASON,
            },
        )
        restored = self.payload(response, status=200, code="runtime_rollback")
        try:
            _unused, readback = self.read_runtime()
        except (Exception, KeyboardInterrupt, SystemExit) as exc:
            raise AcceptanceError(
                "runtime_rollback_readback_failed",
                manual_recovery_required=True,
            ) from exc
        readback_restored = self.runtime_values_are_restored(
            readback,
            initial=initial,
            applied=applied,
        )
        if not readback_restored:
            raise AcceptanceError(
                "runtime_values_not_restored",
                restored=False,
                manual_recovery_required=True,
            )
        operation: dict[str, Any] | None = None
        postcheck_error: BaseException | None = None
        try:
            _require(
                _runtime_response_has_contract(restored),
                "runtime_rollback_response_contract",
            )
            _require(
                _runtime_response_has_contract(readback),
                "runtime_rollback_readback_contract",
            )
            _require(
                self.runtime_state_matches(readback, restored),
                "runtime_rollback_response_readback_mismatch",
            )
            operation = self.correlated_rollback_operation(
                readback,
                initial=initial,
                applied=applied,
            )
        except (Exception, KeyboardInterrupt, SystemExit) as exc:
            postcheck_error = exc

        if postcheck_error is None:
            try:
                _require(operation is not None, "runtime_rollback_operation_missing")
                self.audit(
                    response,
                    event="admin_runtime_config_completed",
                    action="runtime_config.rollback",
                    result="rolled_back",
                    draft_id=None,
                    operation_id=operation["operation_id"],
                )
                self.probes()
            except (Exception, KeyboardInterrupt, SystemExit) as exc:
                postcheck_error = exc

        try:
            _unused, final_readback = self.read_runtime()
        except (Exception, KeyboardInterrupt, SystemExit) as exc:
            raise AcceptanceError(
                "runtime_final_readback_failed",
                restored=None,
                manual_recovery_required=True,
            ) from exc
        if not _exact_runtime_payload(final_readback, readback):
            final_restored = (
                self.runtime_values_are_restored(
                    final_readback,
                    initial=initial,
                    applied=applied,
                )
                if _runtime_snapshot_has_contract(final_readback)
                else None
            )
            raise AcceptanceError(
                "runtime_final_state_changed",
                restored=final_restored,
                manual_recovery_required=True,
            )
        if postcheck_error is None:
            try:
                final_operation = self.correlated_rollback_operation(
                    final_readback,
                    initial=initial,
                    applied=applied,
                )
                _require(
                    operation is not None
                    and _exact_mapping(final_operation, operation),
                    "runtime_final_rollback_operation_mismatch",
                )
            except (Exception, KeyboardInterrupt, SystemExit) as exc:
                raise AcceptanceError(
                    getattr(exc, "code", "runtime_final_postcheck_failed"),
                    restored=True,
                    manual_recovery_required=True,
                ) from exc
        if postcheck_error is not None:
            raise AcceptanceError(
                getattr(
                    postcheck_error,
                    "code",
                    "runtime_rollback_postcheck_failed",
                ),
                restored=True,
            ) from postcheck_error
        if deferred_interruption is not None:
            raise AcceptanceError(
                "runtime_rollback_interrupted",
                restored=True,
            ) from deferred_interruption
        return True

    def run(self) -> dict[str, bool]:
        checks: dict[str, bool] = {}
        anonymous_business = self.client.post("/api/patent/search", json={})
        token_business = self.client.post(
            "/api/patent/search",
            headers={"X-API-Key": self.business_api_token},
            json={},
        )
        anonymous_business_payload = self.payload(
            anonymous_business,
            status=401,
            code="business_anonymous_probe",
        )
        token_business_payload = self.payload(
            token_business,
            status=400,
            code="business_token_probe",
        )
        _require(
            anonymous_business_payload.get("code") == 40101,
            "business_anonymous_probe_code",
        )
        _require(
            token_business_payload.get("code") == 40002,
            "business_token_probe_code",
        )
        checks["business_token_valid"] = True

        anonymous = self.client.get("/admin-api/v1/status")
        token_only = self.client.get(
            "/admin-api/v1/status",
            headers={"X-API-Key": self.business_api_token},
        )
        _require(anonymous.status_code == token_only.status_code == 401, "admin_auth_isolation")
        checks["admin_auth_isolated"] = True

        status_response = self.get("/admin-api/v1/status")
        status = self.payload(status_response, status=200, code="admin_status")
        _require(status.get("role") == "admin", "admin_role")
        _require(status.get("config_drafts_enabled") is True, "config_drafts_disabled")
        _require(status.get("runtime_config_enabled") is True, "runtime_config_disabled")
        if self.require_prometheus:
            _require(status.get("metrics_source") == "prometheus", "prometheus_unavailable")
        if self.require_journal_audit:
            _require(status.get("log_scope") == "journal_local", "journal_audit_unavailable")
        release = status.get("release", {})
        _require(
            isinstance(release, dict)
            and release.get("commit") == self.expected_release_commit
            and _release_value_is_deployed(release.get("service_version"))
            and _release_value_is_deployed(release.get("tag"))
            and _release_value_is_deployed(release.get("instance_id")),
            "release_identity_mismatch",
        )
        checks["control_plane_preconditions"] = True

        schema_response = self.get("/admin-api/v1/config-schema")
        schema = self.payload(schema_response, status=200, code="config_schema")
        _require(
            schema.get("registry_version") == ADMIN_CONFIG_REGISTRY_VERSION
            and schema.get("apply_mode_contract") == ADMIN_CONFIG_APPLY_MODE_CONTRACT
            and schema.get("release_commit") == self.expected_release_commit
            and schema.get("service_version") == release.get("service_version"),
            "config_schema_release_contract",
        )
        _runtime_response, initial = self.read_runtime()
        _require(
            _runtime_response_has_contract(initial),
            "runtime_initial_response_contract",
        )
        _require(initial.get("writes_enabled") is True, "runtime_writes_disabled")
        _require(
            initial.get("source") == "deployment_baseline",
            "runtime_not_at_deployment_baseline",
        )
        _require(initial.get("rollback_version") is None, "existing_rollback_target")
        _require(schema.get("runtime_version") == initial.get("version"), "schema_runtime_version")
        _require(schema.get("baseline_fingerprint") == initial.get("version"), "schema_baseline")
        _require(initial.get("fingerprint") == initial.get("version"), "baseline_fingerprint")
        initial_values = initial.get("values")
        _require(isinstance(initial_values, dict), "runtime_values_missing")
        schema_items = schema.get("items")
        _require(isinstance(schema_items, list), "config_schema_items")
        parameter = self.parameter(schema)
        current = parameter["current_value"]
        schema_current_values = {
            item.get("key"): item.get("current_value")
            for item in schema_items
            if isinstance(item, dict) and isinstance(item.get("key"), str)
        }
        schema_rollback_values = {
            item.get("key"): item.get("rollback_value")
            for item in schema_items
            if isinstance(item, dict) and isinstance(item.get("key"), str)
        }
        _require(
            len(schema_items) == len(initial_values)
            and _exact_mapping(schema_current_values, initial_values)
            and _exact_mapping(schema_rollback_values, initial_values),
            "schema_runtime_values",
        )
        initial_parameter = initial_values.get(ACCEPTANCE_PARAMETER)
        _require(
            type(initial_parameter) is int and initial_parameter == current,
            "runtime_parameter_readback",
        )
        _require(
            initial.get("fingerprint")
            == _runtime_content_fingerprint(
                initial_values,
                service_version=release["service_version"],
                release_commit=self.expected_release_commit,
            ),
            "runtime_initial_fingerprint",
        )
        _require(
            any(
                isinstance(item, dict)
                and item.get("apply_mode") == "restart_required"
                for item in schema_items
            ),
            "restart_required_contract_missing",
        )
        candidate = ACCEPTANCE_CANDIDATE_VALUE
        expected_applied_values = dict(initial_values)
        expected_applied_values[ACCEPTANCE_PARAMETER] = candidate
        expected_applied_fingerprint = _runtime_content_fingerprint(
            expected_applied_values,
            service_version=release["service_version"],
            release_commit=self.expected_release_commit,
        )
        checks["runtime_contract_consistent"] = True

        invalid_response = self.post(
            "/admin-api/v1/config-drafts",
            intent="create-config-draft",
            payload={
                "baseline_fingerprint": initial["version"],
                "reason": "Issue #47 server-side invalid preflight acceptance",
                "candidate_values": {ACCEPTANCE_PARAMETER: ACCEPTANCE_MAXIMUM + 1},
            },
        )
        invalid = self.payload(invalid_response, status=201, code="invalid_draft")
        _require(
            invalid.get("status") == "invalid"
            and invalid.get("validation_status") == "invalid"
            and invalid.get("registry_version") == ADMIN_CONFIG_REGISTRY_VERSION
            and invalid.get("service_version") == release.get("service_version")
            and invalid.get("release_commit") == self.expected_release_commit
            and invalid.get("baseline_fingerprint") == initial.get("version")
            and invalid.get("operator") == self.auth[0],
            "invalid_draft_contract",
        )
        validation_errors = invalid.get("validation_errors")
        _require(
            isinstance(validation_errors, list)
            and any(
                isinstance(error, dict)
                and error.get("code") == "out_of_range"
                and error.get("key") == ACCEPTANCE_PARAMETER
                for error in validation_errors
            ),
            "invalid_draft_error_missing",
        )
        self.audit(
            invalid_response,
            event="admin_config_completed",
            action="config_draft.create",
            result="invalid",
            draft_id=invalid.get("id"),
        )
        _unused, after_invalid = self.read_runtime()
        _require(
            self.runtime_state_matches(after_invalid, initial),
            "invalid_draft_mutated_runtime",
        )
        checks["invalid_preflight_rejected_without_mutation"] = True

        draft_response = self.post(
            "/admin-api/v1/config-drafts",
            intent="create-config-draft",
            payload={
                "baseline_fingerprint": initial["version"],
                "reason": "Issue #47 controlled single-instance runtime acceptance",
                "candidate_values": {ACCEPTANCE_PARAMETER: candidate},
            },
        )
        draft = self.payload(draft_response, status=201, code="valid_draft")
        draft_id = draft.get("id")
        draft_candidates = draft.get("candidate_values")
        draft_diff = draft.get("diff")
        diff = (
            draft_diff.get(ACCEPTANCE_PARAMETER, {})
            if isinstance(draft_diff, dict)
            else {}
        )
        _require(
            draft.get("status") == "validated"
            and draft.get("validation_status") == "validated"
            and draft.get("validation_errors") == []
            and draft.get("registry_version") == ADMIN_CONFIG_REGISTRY_VERSION
            and draft.get("service_version") == release.get("service_version")
            and draft.get("release_commit") == self.expected_release_commit
            and draft.get("baseline_fingerprint") == initial.get("version")
            and draft.get("operator") == self.auth[0]
            and isinstance(draft_candidates, dict)
            and set(draft_candidates) == {ACCEPTANCE_PARAMETER}
            and type(draft_candidates.get(ACCEPTANCE_PARAMETER)) is int
            and draft_candidates.get(ACCEPTANCE_PARAMETER) == candidate
            and isinstance(draft_diff, dict)
            and set(draft_diff) == {ACCEPTANCE_PARAMETER}
            and type(diff.get("old")) is int
            and diff.get("old") == current
            and type(diff.get("new")) is int
            and diff.get("new") == candidate
            and diff.get("apply_mode") == "runtime_reload",
            "valid_draft_rejected",
        )
        self.audit(
            draft_response,
            event="admin_config_completed",
            action="config_draft.create",
            result="validated",
            draft_id=draft_id,
        )
        checks["validated_draft_audited"] = True

        applied: dict[str, Any] | None = None
        cooldown_started_at: float | None = None
        primary_error: BaseException | None = None
        restored = False
        apply_idempotency_key = f"issue47-apply-{uuid4().hex}"
        apply_payload = {
            "draft_id": draft_id,
            "expected_version": initial["version"],
        }
        try:
            apply_response = self.post(
                "/admin-api/v1/runtime-config/apply",
                intent="apply-runtime-config",
                idempotency_key=apply_idempotency_key,
                payload=apply_payload,
            )
            apply_result = self.payload(apply_response, status=200, code="runtime_apply")
            response_operation = self.correlated_apply_operation(
                apply_result,
                initial=initial,
                draft_id=draft_id,
                expected_values=expected_applied_values,
                expected_fingerprint=expected_applied_fingerprint,
            )
            _unused, apply_readback = self.read_runtime()
            readback_operation = self.correlated_apply_operation(
                apply_readback,
                initial=initial,
                draft_id=draft_id,
                expected_values=expected_applied_values,
                expected_fingerprint=expected_applied_fingerprint,
            )
            _require(
                _exact_mapping(response_operation, readback_operation),
                "runtime_apply_operation_readback_mismatch",
            )
            _require(
                self.runtime_state_matches(apply_result, apply_readback),
                "runtime_apply_response_readback_mismatch",
            )
            # Only an independent runtime readback that still carries this
            # smoke's apply operation may authorize automatic cleanup.
            applied = apply_readback
            cooldown_started_at = self.now()
            self.audit(
                apply_response,
                event="admin_runtime_config_completed",
                action="runtime_config.apply",
                result="applied",
                draft_id=draft_id,
                operation_id=readback_operation["operation_id"],
            )
            _unused, readback = self.read_runtime()
            _require(
                self.runtime_state_matches(readback, applied),
                "runtime_readback_state",
            )
            self.correlated_apply_operation(
                readback,
                initial=initial,
                draft_id=draft_id,
                expected_values=expected_applied_values,
                expected_fingerprint=expected_applied_fingerprint,
            )
            applied_schema_response = self.get("/admin-api/v1/config-schema")
            applied_schema = self.payload(
                applied_schema_response,
                status=200,
                code="applied_schema",
            )
            _require(
                applied_schema.get("registry_version")
                == ADMIN_CONFIG_REGISTRY_VERSION
                and applied_schema.get("apply_mode_contract")
                == ADMIN_CONFIG_APPLY_MODE_CONTRACT
                and applied_schema.get("release_commit")
                == self.expected_release_commit
                and applied_schema.get("service_version")
                == release.get("service_version")
                and applied_schema.get("runtime_version") == applied.get("version")
                and applied_schema.get("baseline_fingerprint")
                == applied.get("version"),
                "applied_schema_release_contract",
            )
            applied_items = applied_schema.get("items")
            _require(isinstance(applied_items, list), "applied_schema_items")
            schema_current_values = {
                item.get("key"): item.get("current_value")
                for item in applied_items
                if isinstance(item, dict) and isinstance(item.get("key"), str)
            }
            schema_rollback_values = {
                item.get("key"): item.get("rollback_value")
                for item in applied_items
                if isinstance(item, dict) and isinstance(item.get("key"), str)
            }
            _require(
                len(applied_items) == len(expected_applied_values)
                and _exact_mapping(
                    schema_current_values,
                    expected_applied_values,
                ),
                "applied_schema_current",
            )
            _require(
                _exact_mapping(schema_rollback_values, initial_values),
                "applied_schema_rollback",
            )
            self.probes()
            checks["runtime_apply_and_readback"] = True
        except (Exception, KeyboardInterrupt, SystemExit) as exc:
            primary_error = exc
        finally:
            try:
                if applied is None:
                    applied, cooldown_started_at = self.reconcile_apply_outcome(
                        initial=initial,
                        draft_id=draft_id,
                        idempotency_key=apply_idempotency_key,
                        payload=apply_payload,
                        expected_values=expected_applied_values,
                        expected_fingerprint=expected_applied_fingerprint,
                    )
                if applied is not None:
                    _require(
                        cooldown_started_at is not None,
                        "runtime_cooldown_start_missing",
                    )
                    restored = self.rollback(
                        initial=initial,
                        applied=applied,
                        cooldown_started_at=cooldown_started_at,
                    )
                    checks["runtime_values_restored"] = True
                    checks["rollback_audited"] = True
            except (Exception, KeyboardInterrupt, SystemExit) as rollback_error:
                restored_value = getattr(rollback_error, "restored", None)
                manual_recovery_required = (
                    restored_value is not True
                    or bool(
                        getattr(
                            rollback_error,
                            "manual_recovery_required",
                            False,
                        )
                    )
                )
                error_code = getattr(rollback_error, "code", None)
                if error_code is None and isinstance(
                    rollback_error,
                    (KeyboardInterrupt, SystemExit),
                ):
                    error_code = "runtime_rollback_interrupted"
                raise AcceptanceError(
                    error_code or "runtime_rollback_failed",
                    restored=restored_value,
                    manual_recovery_required=manual_recovery_required,
                ) from rollback_error

        if primary_error is not None:
            error_code = getattr(primary_error, "code", None)
            if error_code is None and isinstance(
                primary_error,
                (KeyboardInterrupt, SystemExit),
            ):
                error_code = "runtime_acceptance_interrupted"
            raise AcceptanceError(
                error_code or "runtime_acceptance_failed",
                restored=restored,
                manual_recovery_required=not restored,
            ) from primary_error
        _require(restored, "runtime_rollback_not_executed")
        return checks


def run_runtime_acceptance(
    client: Any,
    *,
    username: str,
    password: str,
    business_api_token: str,
    expected_release_commit: str,
    require_prometheus: bool = True,
    require_journal_audit: bool = True,
    check_probes: bool = True,
    audit_timeout_seconds: float = DEFAULT_AUDIT_TIMEOUT_SECONDS,
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    now: Callable[[], float] = monotonic,
    sleeper: Callable[[float], None] = sleep,
) -> dict[str, bool]:
    _require(
        _COMMIT_PATTERN.fullmatch(expected_release_commit) is not None,
        "expected_release_commit_invalid",
    )
    return RuntimeAcceptance(
        client,
        username=username,
        password=password,
        business_api_token=business_api_token,
        expected_release_commit=expected_release_commit,
        require_prometheus=require_prometheus,
        require_journal_audit=require_journal_audit,
        check_probes=check_probes,
        audit_timeout_seconds=audit_timeout_seconds,
        cooldown_seconds=cooldown_seconds,
        now=now,
        sleeper=sleeper,
    ).run()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Apply and roll back one reviewed runtime parameter for Issue #47 "
            "single-instance acceptance."
        )
    )
    parser.add_argument("admin_base_url")
    parser.add_argument("--confirm-runtime-mutation", required=True)
    parser.add_argument("--expected-release-commit", default="")
    parser.add_argument(
        "--audit-timeout-seconds",
        type=float,
        default=DEFAULT_AUDIT_TIMEOUT_SECONDS,
    )
    args = parser.parse_args()

    if args.confirm_runtime_mutation != CONFIRMATION:
        print(json.dumps({"ok": False, "error": "confirmation_required"}))
        return 2
    if _COMMIT_PATTERN.fullmatch(args.expected_release_commit) is None:
        print(json.dumps({"ok": False, "error": "expected_release_commit_required"}))
        return 2
    if not 0 < args.audit_timeout_seconds <= 30:
        print(json.dumps({"ok": False, "error": "invalid_audit_timeout"}))
        return 2
    username = os.environ.get("ADMIN_VIEWER_USERNAME", "")
    password = os.environ.get("ADMIN_VIEWER_PASSWORD", "")
    business_api_token = os.environ.get("API_TOKEN", "")
    if not username or not password or not business_api_token:
        print(json.dumps({"ok": False, "error": "acceptance_credentials_required"}))
        return 2

    try:
        base_url = _bounded_base_url(args.admin_base_url, "admin_base_url")
        with httpx.Client(base_url=base_url, timeout=30, trust_env=False) as client:
            checks = run_runtime_acceptance(
                client,
                username=username,
                password=password,
                business_api_token=business_api_token,
                expected_release_commit=args.expected_release_commit,
                audit_timeout_seconds=args.audit_timeout_seconds,
            )
    except AcceptanceError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": exc.code,
                    "restored": exc.restored,
                    "manual_recovery_required": exc.manual_recovery_required,
                },
                sort_keys=True,
            )
        )
        return 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "manual_recovery_required": False,
                },
                sort_keys=True,
            )
        )
        return 1

    print(json.dumps({"ok": True, "checks": checks}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
