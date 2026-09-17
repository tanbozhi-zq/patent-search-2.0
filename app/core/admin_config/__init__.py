"""Administrator configuration contracts, validation, persistence, and runtime state.

This package intentionally separates Issue #59 draft/preflight responsibilities
from Issue #60's in-memory runtime snapshot controller while preserving the
existing import surface for API and test consumers.
"""

from app.core.admin_config.fingerprint import baseline_fingerprint, canonical_json
from app.core.admin_config.models import (
    AdminConfigDraft,
    AdminConfigStoreBusyError,
    AdminConfigStoreError,
    ConfigDiffValue,
    ConfigParameterDefinition,
    ConfigValidationIssue,
    ConfigValidationResult,
    ConfigValue,
    RuntimeConfigApplyError,
    RuntimeConfigConflictError,
    RuntimeConfigOperation,
    RuntimeConfigRateLimitedError,
    RuntimeConfigSnapshot,
    UnknownConfigKeyError,
)
from app.core.admin_config.registry import (
    ADMIN_CONFIG_APPLY_MODE_CONTRACT,
    ADMIN_CONFIG_DRAFT_BODY_MAX_BYTES,
    ADMIN_CONFIG_DRAFT_MAX_CHANGES,
    ADMIN_CONFIG_DRAFT_MAX_REASON_LENGTH,
    ADMIN_CONFIG_DRAFT_TTL_SECONDS,
    ADMIN_CONFIG_REGISTRY_VERSION,
    CONFIG_PARAMETER_REGISTRY,
    RUNTIME_RELOAD_KEYS,
    config_definition,
    current_config_values,
)
from app.core.admin_config.runtime import (
    CallbackRuntimeConfigVerifier,
    DEFAULT_RUNTIME_MUTATION_MIN_INTERVAL_SECONDS,
    RuntimeConfigController,
    RuntimeConfigProvider,
    current_runtime_config,
    new_runtime_snapshot,
    runtime_config_scope,
    runtime_snapshot_from_settings,
)
from app.core.admin_config.store import AdminConfigDraftStore
from app.core.admin_config.validation import reject_unknown_config_keys, validate_config_candidate

__all__ = [
    "ADMIN_CONFIG_APPLY_MODE_CONTRACT",
    "ADMIN_CONFIG_DRAFT_BODY_MAX_BYTES",
    "ADMIN_CONFIG_DRAFT_MAX_CHANGES",
    "ADMIN_CONFIG_DRAFT_MAX_REASON_LENGTH",
    "ADMIN_CONFIG_DRAFT_TTL_SECONDS",
    "ADMIN_CONFIG_REGISTRY_VERSION",
    "AdminConfigDraft",
    "AdminConfigDraftStore",
    "AdminConfigStoreBusyError",
    "AdminConfigStoreError",
    "CallbackRuntimeConfigVerifier",
    "CONFIG_PARAMETER_REGISTRY",
    "ConfigDiffValue",
    "ConfigParameterDefinition",
    "ConfigValidationIssue",
    "ConfigValidationResult",
    "ConfigValue",
    "DEFAULT_RUNTIME_MUTATION_MIN_INTERVAL_SECONDS",
    "RUNTIME_RELOAD_KEYS",
    "RuntimeConfigApplyError",
    "RuntimeConfigConflictError",
    "RuntimeConfigController",
    "RuntimeConfigOperation",
    "RuntimeConfigProvider",
    "RuntimeConfigRateLimitedError",
    "RuntimeConfigSnapshot",
    "UnknownConfigKeyError",
    "baseline_fingerprint",
    "canonical_json",
    "config_definition",
    "current_config_values",
    "current_runtime_config",
    "new_runtime_snapshot",
    "reject_unknown_config_keys",
    "runtime_config_scope",
    "runtime_snapshot_from_settings",
    "validate_config_candidate",
]
