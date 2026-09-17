"""管理 Schema 有意比内部对象窄：它们是只读页面的输出契约，不是 Settings 或
Prometheus 原始响应的通用序列化器。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from app.core.admin_config import (
    ADMIN_CONFIG_APPLY_MODE_CONTRACT,
    ADMIN_CONFIG_DRAFT_MAX_CHANGES,
    ADMIN_CONFIG_DRAFT_MAX_REASON_LENGTH,
    ADMIN_CONFIG_DRAFT_TTL_SECONDS,
)


class AdminReleaseInfo(BaseModel):
    # 发布身份用来把看板数据绑定到某个进程实例，而不是声称整个集群统一。
    service_version: str
    commit: str
    tag: str
    instance_id: str
    started_at: datetime


class AdminStatusResponse(BaseModel):
    """管理首页加载时使用的发布身份和观测能力快照。

    该模型说明当前实例能否读取指标与日志，而不是汇报整个集群或下游依赖的健康度；
    前端应结合 ``notices`` 解释 unavailable 状态，不能简单当作零数据。配置草稿
    与运行时配置开关则明确界定当前管理会话可使用的控制面功能。
    """

    # metrics_source/log_scope 显式表示能力边界，避免 unavailable 被误读为“无数据”。
    role: Literal["admin"]
    config_drafts_enabled: bool
    runtime_config_enabled: bool
    release: AdminReleaseInfo
    metrics_source: Literal["prometheus", "unavailable"]
    log_scope: Literal["journal_local", "current_process", "unavailable"]
    log_routes: list[str]
    notices: list[str]


class AdminConfigItem(BaseModel):
    # secret 类配置只填 configured；runtime/limit 类配置才填 value。
    key: str
    label: str
    category: Literal["runtime", "limit", "secret"]
    value: str | None = None
    configured: bool | None = None


class AdminConfigResponse(BaseModel):
    """管理端可展示的配置白名单及其当前运行时快照版本。

    该模型绝不是完整 Settings 的序列化结果；``runtime_version`` 与 ``runtime_source``
    让看板可区分部署基线和已生效的临时运行时覆盖。
    """

    runtime_version: str
    runtime_source: Literal["deployment_baseline", "runtime_override"]
    items: list[AdminConfigItem]


class AdminConfigDefinition(BaseModel):
    key: str
    label: str
    purpose: str
    category: str
    value_type: Literal["integer", "number"]
    unit: str
    default_value: int | float | None
    minimum: int | float
    maximum: int | float
    apply_mode: Literal["runtime_reload", "restart_required", "unsupported"]
    risk: str
    observation_metrics: list[str]
    constraints: list[str]
    current_value: int | float
    rollback_value: int | float
    decrease_only: bool


class AdminConfigSchemaResponse(BaseModel):
    registry_version: str
    service_version: str
    release_commit: str
    baseline_fingerprint: str
    draft_ttl_seconds: Literal[ADMIN_CONFIG_DRAFT_TTL_SECONDS]
    apply_mode_contract: Literal[ADMIN_CONFIG_APPLY_MODE_CONTRACT]
    runtime_version: str
    runtime_source: Literal["deployment_baseline", "runtime_override"]
    items: list[AdminConfigDefinition]


class AdminConfigDraftCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseline_fingerprint: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    reason: str = Field(
        min_length=1,
        max_length=ADMIN_CONFIG_DRAFT_MAX_REASON_LENGTH,
    )
    candidate_values: dict[str, JsonValue] = Field(
        min_length=1,
        max_length=ADMIN_CONFIG_DRAFT_MAX_CHANGES,
    )

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("reason must not be blank")
        if any(ord(character) < 32 and character not in "\n\t" for character in normalized):
            raise ValueError("reason contains unsupported control characters")
        return normalized


class AdminConfigValidationError(BaseModel):
    code: str
    key: str | None = None
    message: str


class AdminConfigDiffItem(BaseModel):
    old: int | float
    new: int | float
    apply_mode: Literal["runtime_reload", "restart_required", "unsupported"]


class AdminConfigDraftResponse(BaseModel):
    id: str
    created_at: datetime
    expires_at: datetime
    operator: str
    reason: str
    registry_version: str
    service_version: str
    release_commit: str
    baseline_fingerprint: str
    validation_status: Literal["validated", "invalid"]
    status: Literal["validated", "invalid", "expired"]
    candidate_values: dict[str, int | float]
    diff: dict[str, AdminConfigDiffItem]
    validation_errors: list[AdminConfigValidationError]


class AdminConfigDraftExportResponse(BaseModel):
    format_version: Literal["admin-config-draft.v1"]
    draft: AdminConfigDraftResponse


class AdminConfigDraftListResponse(BaseModel):
    current_baseline_fingerprint: str
    items: list[AdminConfigDraftResponse]


class RuntimeConfigOperationResponse(BaseModel):
    operation_id: str
    kind: Literal["apply", "rollback"]
    status: Literal["applied", "rolled_back", "failed", "incomplete"]
    created_at: datetime
    actor: str
    reason: str
    expected_version: str
    previous_version: str | None
    current_version: str | None
    draft_id: str | None
    failure_code: str | None


class RuntimeConfigResponse(BaseModel):
    version: str
    fingerprint: str
    source: Literal["deployment_baseline", "runtime_override"]
    created_at: datetime
    rollback_version: str | None
    writes_enabled: bool
    values: dict[str, int | float]
    recent_operations: list[RuntimeConfigOperationResponse]


class RuntimeConfigApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: str = Field(min_length=36, max_length=36)
    expected_version: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )


class RuntimeConfigRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    target_version: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    reason: str = Field(
        min_length=1,
        max_length=ADMIN_CONFIG_DRAFT_MAX_REASON_LENGTH,
    )

    @field_validator("reason")
    @classmethod
    def validate_rollback_reason(cls, value: str) -> str:
        return AdminConfigDraftCreateRequest.validate_reason(value)


class AdminMetricSample(BaseModel):
    # labels 已在 Prometheus reader 中经过固定名称/长度白名单和数值有限性检查。
    labels: dict[str, str]
    value: float


class AdminMetricResult(BaseModel):
    # 单条 PromQL 可独立 unavailable，整批响应通过 partial 汇总。
    key: str
    available: bool
    samples: list[AdminMetricSample] = Field(default_factory=list)


class AdminMetricsResponse(BaseModel):
    """固定 PromQL 集合在一个受控窗口内的聚合读取结果。

    单个指标允许不可用而整批仍成功返回，``partial`` 用来标识这一情况；样本值和
    labels 在 reader 中已完成规模、名称和数值合法性校验。
    """

    # window_seconds 记录服务端实际查询窗口，generated_at 记录本批结果生成时间。
    source: Literal["prometheus", "unavailable"]
    generated_at: datetime
    window_seconds: int
    partial: bool
    results: list[AdminMetricResult]


class AdminLogEvent(BaseModel):
    # 事件字段是结构化日志白名单的镜像；可选字段允许不同事件只返回自身相关信息。
    timestamp: datetime
    event: str
    request_id: str | None = None
    route: str | None = None
    method: str | None = None
    status: int | None = Field(default=None, ge=100, le=599)
    code: int | None = None
    elapsed_ms: float | None = Field(default=None, ge=0)
    dependency: str | None = None
    operation: str | None = None
    outcome: str | None = None
    retry_count: int | None = Field(default=None, ge=0)
    name: str | None = None
    in_flight: int | None = Field(default=None, ge=0)
    peak_in_flight: int | None = Field(default=None, ge=0)
    capacity: int | None = Field(default=None, ge=0)
    rejected_total: int | None = Field(default=None, ge=0)
    acquire_timeout_seconds: float | None = Field(default=None, ge=0)
    exception_type: str | None = None
    actor: str | None = None
    role: str | None = None
    action: str | None = None
    result: str | None = None
    returned_count: int | None = Field(default=None, ge=0)
    window_seconds: int | None = Field(default=None, ge=0)
    draft_id: str | None = None
    operation_id: str | None = Field(
        default=None,
        min_length=36,
        max_length=36,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    )
    changed_parameter_count: int | None = Field(default=None, ge=0)
    validation_error_count: int | None = Field(default=None, ge=0)


class AdminLogsResponse(BaseModel):
    """一次有界关联日志扫描的分页结果与范围说明。

    ``scanned_count``、``truncated`` 和 ``next_cursor`` 共同说明当前页并非任意
    时间范围的全量日志导出；cursor 只可用于相同 reader、窗口与筛选条件。
    """

    # scanned_count/truncated/next_cursor 让调用方知道这是有界扫描而不是全量日志视图。
    scope: Literal["journal_local", "current_process", "unavailable"]
    available: bool
    window_seconds: int
    scanned_count: int
    truncated: bool
    next_cursor: str | None = None
    items: list[AdminLogEvent]
