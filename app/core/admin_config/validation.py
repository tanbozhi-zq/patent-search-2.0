"""Server-side validation for registered configuration candidates."""

from __future__ import annotations

import math
from typing import Mapping

from app.core.admin_config.models import (
    ConfigDiffValue,
    ConfigParameterDefinition,
    ConfigValidationIssue,
    ConfigValidationResult,
    ConfigValue,
    UnknownConfigKeyError,
)
from app.core.admin_config.registry import (
    CAPACITY_KEYS,
    DEPENDENCY_BUDGET_KEYS,
    QUERY_BUDGET_KEYS,
    CONFIG_PARAMETER_REGISTRY,
    config_definition,
    current_config_values,
)
from app.core.config import Settings
from app.query.budget import HARD_QUERY_BUDGET, QueryBudget


def reject_unknown_config_keys(candidate_values: Mapping[str, object]) -> None:
    registered = {definition.key for definition in CONFIG_PARAMETER_REGISTRY}
    unknown = tuple(sorted(set(candidate_values) - registered))
    if unknown:
        raise UnknownConfigKeyError(unknown)


def validate_config_candidate(
    settings: Settings,
    candidate_values: Mapping[str, object],
    *,
    current_values: Mapping[str, ConfigValue] | None = None,
) -> ConfigValidationResult:
    reject_unknown_config_keys(candidate_values)
    current = (
        dict(current_values)
        if current_values is not None
        else current_config_values(settings)
    )
    normalized: dict[str, ConfigValue] = {}
    errors: list[ConfigValidationIssue] = []

    for key, raw_value in candidate_values.items():
        definition = config_definition(key)
        value = _normalize_value(definition, raw_value)
        if value is None:
            errors.append(
                ConfigValidationIssue(
                    code="invalid_type",
                    key=key,
                    message=(
                        "必须提交整数。"
                        if definition.value_type == "integer"
                        else "必须提交有限数值。"
                    ),
                )
            )
            continue
        normalized[key] = value
        if value < definition.minimum or value > definition.maximum:
            errors.append(
                ConfigValidationIssue(
                    code="out_of_range",
                    key=key,
                    message=(
                        f"允许范围为 {definition.minimum} 到 "
                        f"{definition.maximum} {definition.unit}。"
                    ),
                )
            )
        if value == current[key]:
            errors.append(
                ConfigValidationIssue(
                    code="unchanged_value",
                    key=key,
                    message="候选值与当前值相同。",
                )
            )
        if definition.decrease_only and value > current[key]:
            errors.append(
                ConfigValidationIssue(
                    code="must_not_loosen_limit",
                    key=key,
                    message="查询预算只能保持或收紧，不能高于当前值。",
                )
            )

    effective = dict(current)
    effective.update(normalized)
    touched = frozenset(candidate_values)
    if touched & CAPACITY_KEYS:
        _validate_capacity_relationships(effective, errors)
    if touched & DEPENDENCY_BUDGET_KEYS:
        _validate_dependency_budget(effective, errors)
    if touched & QUERY_BUDGET_KEYS:
        _validate_query_budget(effective, errors)

    diff = {
        key: ConfigDiffValue(
            old=current[key],
            new=value,
            apply_mode=config_definition(key).apply_mode,
        )
        for key, value in normalized.items()
        if value != current[key]
    }
    return ConfigValidationResult(
        status="invalid" if errors else "validated",
        candidate_values=normalized,
        diff=diff,
        errors=tuple(errors),
    )


def _normalize_value(
    definition: ConfigParameterDefinition,
    raw_value: object,
) -> ConfigValue | None:
    if definition.value_type == "integer":
        return raw_value if type(raw_value) is int else None
    if (
        isinstance(raw_value, (int, float))
        and not isinstance(raw_value, bool)
        and math.isfinite(float(raw_value))
    ):
        return float(raw_value)
    return None


def _validate_capacity_relationships(
    effective: Mapping[str, ConfigValue],
    errors: list[ConfigValidationIssue],
) -> None:
    heavy = effective["bulkhead.heavy_capacity"]
    global_capacity = effective["bulkhead.global_capacity"]
    pool = effective["opensearch.pool_maxsize"]
    if heavy >= global_capacity:
        errors.append(
            ConfigValidationIssue(
                code="heavy_capacity_not_reserved",
                message="重请求舱壁容量必须严格小于全局舱壁容量。",
            )
        )
    if global_capacity > pool:
        errors.append(
            ConfigValidationIssue(
                code="bulkhead_exceeds_pool",
                message="全局舱壁容量不得高于 OpenSearch Client 连接池上限。",
            )
        )


def _validate_dependency_budget(
    effective: Mapping[str, ConfigValue],
    errors: list[ConfigValidationIssue],
) -> None:
    timeout = float(effective["opensearch.timeout_seconds"])
    retries = int(effective["opensearch.max_retries"])
    backoff = float(effective["opensearch.retry_backoff_seconds"])
    deadline = float(effective["request.deadline_seconds"])
    if timeout > deadline:
        errors.append(
            ConfigValidationIssue(
                code="dependency_timeout_exceeds_deadline",
                message=(
                    "OpenSearch 配置超时不得高于请求总 deadline；实际调用仍会按剩余预算截断。"
                ),
            )
        )
    if retries and backoff >= deadline:
        errors.append(
            ConfigValidationIssue(
                code="retry_backoff_exhausts_deadline",
                message="OpenSearch 重试退避必须严格小于请求总 deadline。",
            )
        )


def _validate_query_budget(
    effective: Mapping[str, ConfigValue],
    errors: list[ConfigValidationIssue],
) -> None:
    try:
        budget = QueryBudget(
            max_request_body_bytes=int(effective["query.max_request_body_bytes"]),
            max_query_chars=int(effective["query.max_chars"]),
            max_nesting_depth=int(effective["query.max_nesting_depth"]),
            max_tokens=int(effective["query.max_tokens"]),
            max_ast_nodes=int(effective["query.max_ast_nodes"]),
            max_boolean_clauses=int(effective["query.max_boolean_clauses"]),
            max_page_size=int(effective["query.max_page_size"]),
            max_result_window=int(effective["query.max_result_window"]),
        )
        budget.ensure_within(HARD_QUERY_BUDGET)
    except ValueError:
        errors.append(
            ConfigValidationIssue(
                code="invalid_query_budget_combination",
                message="查询预算组合无效；结果窗口不得低于单页上限，也不得突破代码硬上限。",
            )
        )
