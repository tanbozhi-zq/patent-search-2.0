"""Immutable data contracts for the administrator configuration control plane."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


ConfigValue = int | float
RuntimeConfigSource = Literal["deployment_baseline", "runtime_override"]


@dataclass(frozen=True, slots=True)
class ConfigParameterDefinition:
    key: str
    settings_attribute: str
    label: str
    purpose: str
    category: str
    value_type: str
    unit: str
    default_value: ConfigValue | None
    minimum: ConfigValue
    maximum: ConfigValue
    apply_mode: str
    risk: str
    observation_metrics: tuple[str, ...]
    constraints: tuple[str, ...] = ()
    decrease_only: bool = False


@dataclass(frozen=True, slots=True)
class ConfigValidationIssue:
    code: str
    message: str
    key: str | None = None


@dataclass(frozen=True, slots=True)
class ConfigDiffValue:
    old: ConfigValue
    new: ConfigValue
    apply_mode: str


@dataclass(frozen=True, slots=True)
class ConfigValidationResult:
    status: str
    candidate_values: dict[str, ConfigValue]
    diff: dict[str, ConfigDiffValue]
    errors: tuple[ConfigValidationIssue, ...]


@dataclass(frozen=True, slots=True)
class AdminConfigDraft:
    draft_id: str
    created_at: datetime
    expires_at: datetime
    operator: str
    reason: str
    registry_version: str
    service_version: str
    release_commit: str
    baseline_fingerprint: str
    validation_status: str
    status: str
    candidate_values: dict[str, ConfigValue]
    diff: dict[str, ConfigDiffValue]
    validation_errors: tuple[ConfigValidationIssue, ...]


@dataclass(frozen=True, slots=True)
class RuntimeConfigSnapshot:
    """A complete, immutable view of the registered configuration values.

    Values are stored as sorted tuples rather than a mutable mapping so a request
    holding an old snapshot can never observe a field-level partial update.
    """

    version: str
    fingerprint: str
    source: RuntimeConfigSource
    created_at: datetime
    values: tuple[tuple[str, ConfigValue], ...]
    parent_version: str | None = None

    def __post_init__(self) -> None:
        keys = tuple(key for key, _value in self.values)
        if not keys or keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise ValueError("runtime configuration values must have unique sorted keys")

    def as_dict(self) -> dict[str, ConfigValue]:
        return dict(self.values)

    def value_for(self, key: str) -> ConfigValue:
        for candidate, value in self.values:
            if candidate == key:
                return value
        raise KeyError(key)


@dataclass(frozen=True, slots=True)
class RuntimeConfigOperation:
    operation_id: str
    kind: Literal["apply", "rollback"]
    status: Literal["applied", "rolled_back", "failed", "incomplete"]
    created_at: datetime
    actor: str
    reason: str
    expected_version: str
    previous_version: str | None
    current_version: str | None
    draft_id: str | None = None
    failure_code: str | None = None


class UnknownConfigKeyError(ValueError):
    def __init__(self, keys: tuple[str, ...]):
        self.keys = keys
        super().__init__("candidate values contain unregistered parameter keys")


class AdminConfigStoreError(RuntimeError):
    pass


class AdminConfigStoreBusyError(AdminConfigStoreError):
    pass


class RuntimeConfigOperationError(RuntimeError):
    """A safe API-facing failure with optional durable-operation correlation."""

    def __init__(
        self,
        message: str,
        *,
        operation_id: str | None = None,
        draft_id: str | None = None,
    ):
        self.operation_id = operation_id
        self.draft_id = draft_id
        super().__init__(message)

    def with_operation_context(
        self,
        operation: RuntimeConfigOperation,
    ) -> RuntimeConfigOperationError:
        self.operation_id = operation.operation_id
        self.draft_id = operation.draft_id
        return self


class RuntimeConfigConflictError(RuntimeConfigOperationError):
    pass


class RuntimeConfigRateLimitedError(RuntimeConfigOperationError):
    pass


class RuntimeConfigApplyError(RuntimeConfigOperationError):
    def __init__(
        self,
        message: str,
        *,
        failure_code: str = "verification_failed",
        operation_id: str | None = None,
        draft_id: str | None = None,
    ):
        self.failure_code = failure_code
        super().__init__(
            message,
            operation_id=operation_id,
            draft_id=draft_id,
        )
