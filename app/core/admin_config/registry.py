"""Explicit, non-secret administrator configuration registry."""

from __future__ import annotations

from typing import Mapping

from app.core.admin_config.models import ConfigParameterDefinition, ConfigValue
from app.core.config import Settings
from app.core.timing_contract import (
    MAX_RUNTIME_OPENSEARCH_TIMEOUT_SECONDS,
    MAX_RUNTIME_REQUEST_DEADLINE_SECONDS,
)
from app.query.budget import DEFAULT_QUERY_BUDGET, HARD_QUERY_BUDGET


ADMIN_CONFIG_REGISTRY_VERSION = "2026-08-23.v3"
ADMIN_CONFIG_DRAFT_MAX_CHANGES = 16
ADMIN_CONFIG_DRAFT_MAX_REASON_LENGTH = 500
ADMIN_CONFIG_DRAFT_BODY_MAX_BYTES = 16 * 1024
ADMIN_CONFIG_DRAFT_TTL_SECONDS = 24 * 60 * 60
ADMIN_CONFIG_APPLY_MODE_CONTRACT = "issue_60_revalidation_required"

_CAPACITY_METRICS = (
    "bulkhead_in_flight_total",
    "bulkhead_rejections_window",
    "opensearch_latency_p95_seconds",
)
_DEPENDENCY_METRICS = (
    "latency_p95_seconds",
    "dependency_failure_ratio",
    "dependency_retry_rate",
)
_QUERY_METRICS = (
    "http_outcome_rate",
    "latency_p95_seconds",
    "request_rate",
)


CONFIG_PARAMETER_REGISTRY: tuple[ConfigParameterDefinition, ...] = (
    ConfigParameterDefinition(
        key="opensearch.pool_maxsize", settings_attribute="opensearch_pool_maxsize",
        label="OpenSearch 连接池上限", purpose="限制单进程可使用的 OpenSearch HTTP 连接数量。",
        category="capacity", value_type="integer", unit="connections", default_value=10,
        minimum=1, maximum=32, apply_mode="restart_required",
        risk="提高后会增加单实例对 OpenSearch 的并发压力。",
        observation_metrics=_CAPACITY_METRICS,
        constraints=("全局舱壁容量不得高于连接池上限。",),
    ),
    ConfigParameterDefinition(
        key="opensearch.timeout_seconds", settings_attribute="opensearch_timeout_seconds",
        label="OpenSearch 请求超时", purpose="限制一次 OpenSearch 调用允许等待的时间。",
        category="timeout", value_type="integer", unit="seconds",
        default_value=MAX_RUNTIME_OPENSEARCH_TIMEOUT_SECONDS,
        minimum=1, maximum=MAX_RUNTIME_OPENSEARCH_TIMEOUT_SECONDS,
        apply_mode="runtime_reload",
        risk="提高后会延长依赖占用；降低后可能增加超时响应。",
        observation_metrics=_DEPENDENCY_METRICS,
        constraints=("有效依赖超时不得突破请求总 deadline。",),
    ),
    ConfigParameterDefinition(
        key="opensearch.max_retries", settings_attribute="opensearch_max_retries",
        label="OpenSearch 最大重试", purpose="限制瞬时可重试依赖错误的额外尝试次数。",
        category="retry", value_type="integer", unit="retries", default_value=1,
        minimum=0, maximum=1, apply_mode="runtime_reload",
        risk="重试会增加依赖负载并消耗请求总预算。",
        observation_metrics=_DEPENDENCY_METRICS,
        constraints=("重试、backoff 和单次超时的组合必须留在总 deadline 内。",),
    ),
    ConfigParameterDefinition(
        key="opensearch.retry_backoff_seconds", settings_attribute="opensearch_retry_backoff_seconds",
        label="OpenSearch 重试退避", purpose="控制两次 OpenSearch 尝试之间的指数退避基数。",
        category="retry", value_type="number", unit="seconds", default_value=0.1,
        minimum=0, maximum=30, apply_mode="runtime_reload",
        risk="提高后会减少快速重试，但也会消耗更多请求预算。",
        observation_metrics=_DEPENDENCY_METRICS,
        constraints=("重试、backoff 和单次超时的组合必须留在总 deadline 内。",),
    ),
    ConfigParameterDefinition(
        key="bulkhead.global_capacity", settings_attribute="patent_search_bulkhead_capacity",
        label="全局舱壁容量", purpose="限制单进程同时进入 OpenSearch 业务链路的请求数量。",
        category="capacity", value_type="integer", unit="requests", default_value=None,
        minimum=1, maximum=32, apply_mode="restart_required",
        risk="提高后可能增加 OpenSearch CPU、Heap、队列和拒绝压力。",
        observation_metrics=_CAPACITY_METRICS,
        constraints=("重请求舱壁容量 < 全局舱壁容量 <= Client pool 上限。",),
    ),
    ConfigParameterDefinition(
        key="bulkhead.heavy_capacity", settings_attribute="patent_search_heavy_bulkhead_capacity",
        label="重请求舱壁容量", purpose="为检索和目标排名保留独立的重请求并发边界。",
        category="capacity", value_type="integer", unit="requests", default_value=None,
        minimum=1, maximum=32, apply_mode="restart_required",
        risk="过高会挤占轻请求容量，过低会增加重请求拒绝。",
        observation_metrics=_CAPACITY_METRICS,
        constraints=("必须严格小于全局舱壁容量。",),
    ),
    ConfigParameterDefinition(
        key="bulkhead.acquire_timeout_seconds", settings_attribute="patent_search_bulkhead_acquire_timeout_seconds",
        label="舱壁准入等待", purpose="限制请求等待应用舱壁许可的时间。",
        category="capacity", value_type="number", unit="seconds", default_value=None,
        minimum=0.001, maximum=0.1, apply_mode="restart_required",
        risk="提高后会增加排队和尾延迟，降低后会增加快速拒绝。",
        observation_metrics=_CAPACITY_METRICS,
    ),
    ConfigParameterDefinition(
        key="request.deadline_seconds", settings_attribute="patent_search_deadline_seconds",
        label="请求总 deadline", purpose="限制一个业务请求从入口到返回的总执行预算。",
        category="timeout", value_type="number", unit="seconds",
        default_value=MAX_RUNTIME_REQUEST_DEADLINE_SECONDS,
        minimum=1, maximum=MAX_RUNTIME_REQUEST_DEADLINE_SECONDS,
        apply_mode="runtime_reload",
        risk="提高后会延长资源占用，降低后可能中断长查询。",
        observation_metrics=_DEPENDENCY_METRICS,
        constraints=("必须覆盖有效依赖调用和获准的重试退避预算。",),
    ),
    ConfigParameterDefinition(
        key="query.max_request_body_bytes", settings_attribute="query_max_request_body_bytes",
        label="请求体上限", purpose="限制可解析的检索请求体字节数。",
        category="query_budget", value_type="integer", unit="bytes",
        default_value=DEFAULT_QUERY_BUDGET.max_request_body_bytes,
        minimum=1, maximum=HARD_QUERY_BUDGET.max_request_body_bytes,
        apply_mode="restart_required", risk="收紧后可能拒绝现有的大请求。",
        observation_metrics=_QUERY_METRICS, decrease_only=True,
    ),
    ConfigParameterDefinition(
        key="query.max_chars", settings_attribute="query_max_chars",
        label="检索式字符上限", purpose="限制单个检索表达式的字符数量。",
        category="query_budget", value_type="integer", unit="characters",
        default_value=DEFAULT_QUERY_BUDGET.max_query_chars,
        minimum=1, maximum=HARD_QUERY_BUDGET.max_query_chars,
        apply_mode="restart_required", risk="收紧后可能拒绝现有的长检索式。",
        observation_metrics=_QUERY_METRICS, decrease_only=True,
    ),
    ConfigParameterDefinition(
        key="query.max_nesting_depth", settings_attribute="query_max_nesting_depth",
        label="嵌套深度上限", purpose="限制检索语法树的最大嵌套深度。",
        category="query_budget", value_type="integer", unit="levels",
        default_value=DEFAULT_QUERY_BUDGET.max_nesting_depth,
        minimum=1, maximum=HARD_QUERY_BUDGET.max_nesting_depth,
        apply_mode="restart_required", risk="收紧后可能拒绝深层布尔表达式。",
        observation_metrics=_QUERY_METRICS, decrease_only=True,
    ),
    ConfigParameterDefinition(
        key="query.max_tokens", settings_attribute="query_max_tokens",
        label="Token 数量上限", purpose="限制检索式词法分析产生的 token 数量。",
        category="query_budget", value_type="integer", unit="tokens",
        default_value=DEFAULT_QUERY_BUDGET.max_tokens,
        minimum=1, maximum=HARD_QUERY_BUDGET.max_tokens,
        apply_mode="restart_required", risk="收紧后可能拒绝包含较多条件的查询。",
        observation_metrics=_QUERY_METRICS, decrease_only=True,
    ),
    ConfigParameterDefinition(
        key="query.max_ast_nodes", settings_attribute="query_max_ast_nodes",
        label="AST 节点上限", purpose="限制解析后检索语法树的节点数量。",
        category="query_budget", value_type="integer", unit="nodes",
        default_value=DEFAULT_QUERY_BUDGET.max_ast_nodes,
        minimum=1, maximum=HARD_QUERY_BUDGET.max_ast_nodes,
        apply_mode="restart_required", risk="收紧后可能拒绝复杂但合法的检索表达式。",
        observation_metrics=_QUERY_METRICS, decrease_only=True,
    ),
    ConfigParameterDefinition(
        key="query.max_boolean_clauses", settings_attribute="query_max_boolean_clauses",
        label="布尔子句上限", purpose="限制检索表达式中的布尔子句数量。",
        category="query_budget", value_type="integer", unit="clauses",
        default_value=DEFAULT_QUERY_BUDGET.max_boolean_clauses,
        minimum=1, maximum=HARD_QUERY_BUDGET.max_boolean_clauses,
        apply_mode="restart_required", risk="收紧后可能拒绝包含较多布尔条件的查询。",
        observation_metrics=_QUERY_METRICS, decrease_only=True,
    ),
    ConfigParameterDefinition(
        key="query.max_page_size", settings_attribute="query_max_page_size",
        label="单页数量上限", purpose="限制一次检索响应允许返回的结果数量。",
        category="query_budget", value_type="integer", unit="results",
        default_value=DEFAULT_QUERY_BUDGET.max_page_size,
        minimum=1, maximum=HARD_QUERY_BUDGET.max_page_size,
        apply_mode="restart_required", risk="收紧后可能拒绝现有的大页请求。",
        observation_metrics=_QUERY_METRICS,
        constraints=("不得高于结果窗口上限。",), decrease_only=True,
    ),
    ConfigParameterDefinition(
        key="query.max_result_window", settings_attribute="query_max_result_window",
        label="结果窗口上限", purpose="限制分页 offset 与 page size 的最大组合窗口。",
        category="query_budget", value_type="integer", unit="results",
        default_value=DEFAULT_QUERY_BUDGET.max_result_window,
        minimum=1, maximum=HARD_QUERY_BUDGET.max_result_window,
        apply_mode="restart_required", risk="收紧后可能使深分页请求失效。",
        observation_metrics=_QUERY_METRICS,
        constraints=("不得低于单页数量上限。",), decrease_only=True,
    ),
)

_DEFINITIONS_BY_KEY = {definition.key: definition for definition in CONFIG_PARAMETER_REGISTRY}
CAPACITY_KEYS = frozenset({"opensearch.pool_maxsize", "bulkhead.global_capacity", "bulkhead.heavy_capacity"})
DEPENDENCY_BUDGET_KEYS = frozenset({
    "opensearch.timeout_seconds", "opensearch.max_retries",
    "opensearch.retry_backoff_seconds", "request.deadline_seconds",
})
QUERY_BUDGET_KEYS = frozenset(
    definition.key for definition in CONFIG_PARAMETER_REGISTRY if definition.category == "query_budget"
)
RUNTIME_RELOAD_KEYS = frozenset(
    definition.key for definition in CONFIG_PARAMETER_REGISTRY if definition.apply_mode == "runtime_reload"
)


def config_definition(key: str) -> ConfigParameterDefinition:
    return _DEFINITIONS_BY_KEY[key]


def current_config_values(
    settings: Settings,
    *,
    runtime_values: Mapping[str, ConfigValue] | None = None,
) -> dict[str, ConfigValue]:
    values = {
        definition.key: getattr(settings, definition.settings_attribute)
        for definition in CONFIG_PARAMETER_REGISTRY
    }
    if runtime_values is not None:
        for key in RUNTIME_RELOAD_KEYS:
            if key in runtime_values:
                values[key] = runtime_values[key]
    return values
