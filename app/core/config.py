"""Settings 是进程启动时读取的一次性运行合同。敏感字段隐藏 repr，验证器负责
拒绝不安全的认证组合、越过代码硬上限的配置和无法安全暴露的发布元数据。
"""

from functools import lru_cache
from pathlib import Path
import re
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.mappings.query_field_mapping import DEFAULT_VECTOR_EMBEDDING_MODEL
from app.query.budget import (
    DEFAULT_QUERY_BUDGET,
    HARD_QUERY_BUDGET,
    QueryBudget,
)
from app.core.timing_contract import (
    MAX_RUNTIME_OPENSEARCH_TIMEOUT_SECONDS,
    MAX_RUNTIME_REQUEST_DEADLINE_SECONDS,
)


def _is_printable_ascii(value: str) -> bool:
    # HTTP Basic 用户名/密码只接受可打印 ASCII，避免控制字符、冒号和 Unicode
    # 在不同服务器/客户端实现中产生解析差异。
    return value.isascii() and all(" " <= character <= "~" for character in value)


_RELEASE_VALUE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\Z")
_COMMIT_PATTERN = re.compile(r"(?:unknown|[0-9A-Fa-f]{7,64})\Z")


def _validate_basic_credentials(
    *,
    username: str,
    password: str,
    username_setting: str,
    password_setting: str,
) -> None:
    """校验一对将被 HTTP Basic 解析的凭据，并在错误中指出对应环境变量。

    这不是认证动作，而是启动期合同校验：限制字符集、长度和用户名冒号可避免不同
    Web 服务器或客户端对同一凭据产生不一致解释。
    """
    # 这组检查与 FastAPI/Starlette 的 Basic 解析规则对齐；用户名的冒号由
    # Basic 语法占用，不能作为用户名内容。
    if not username.strip():
        raise ValueError(f"{username_setting} is required")
    if not password:
        raise ValueError(f"{password_setting} is required")
    if not _is_printable_ascii(username):
        raise ValueError(
            f"{username_setting} must contain printable ASCII characters only"
        )
    if ":" in username:
        raise ValueError(f"{username_setting} must not contain ':'")
    if not _is_printable_ascii(password):
        raise ValueError(
            f"{password_setting} must contain printable ASCII characters only"
        )
    if len(username) > 128:
        raise ValueError(f"{username_setting} must not exceed 128 characters")
    if len(password) > 1024:
        raise ValueError(f"{password_setting} must not exceed 1024 characters")


class Settings(BaseSettings):
    """进程启动时解析的一次性运行合同。

    Settings 汇聚服务身份、鉴权、依赖连接、并发资源和查询预算，并通过 model validator
    保护它们之间的关系。字段可来自环境变量或 ``.env``，但运行中不应直接重读环境。
    """

    # 对外环境变量名由 Pydantic Settings 按字段名映射。这里的默认值只适合
    # 本地/测试；生产必须显式提供容量、凭据和发布身份等部署参数。
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "patent-search-service"
    service_host: str = "0.0.0.0"
    service_port: int = 8000

    enable_auth: bool = True
    api_token: str = Field(default="", repr=False)
    console_username: str = ""
    console_password: str = Field(default="", repr=False)

    admin_enabled: bool = False
    admin_viewer_username: str = ""
    admin_viewer_password: str = Field(default="", repr=False)
    admin_config_drafts_enabled: bool = False
    admin_runtime_config_enabled: bool = False
    admin_config_database_path: str = (
        "/var/lib/patent-search-service/admin-config.sqlite3"
    )
    admin_prometheus_url: str = ""
    admin_prometheus_job: str = "patent-search"
    admin_metrics_timeout_seconds: float | str = 2.0
    admin_log_source: str = "journal"

    service_release_commit: str = "unknown"
    service_release_tag: str = "unknown"
    service_instance_id: str = "unknown"

    opensearch_host: str = "localhost"
    opensearch_port: int = 9200
    opensearch_use_https: bool = True
    opensearch_user: str = ""
    opensearch_pass: str = Field(default="", repr=False)
    opensearch_index: str = "patent_search_read"
    opensearch_verify_certs: bool = False
    opensearch_timeout_seconds: int = Field(
        default=MAX_RUNTIME_OPENSEARCH_TIMEOUT_SECONDS,
        ge=1,
        le=MAX_RUNTIME_OPENSEARCH_TIMEOUT_SECONDS,
    )
    opensearch_pool_maxsize: int = Field(default=10, ge=1)
    opensearch_max_retries: int = Field(default=1, ge=0, le=1)
    opensearch_retry_backoff_seconds: float = Field(default=0.1, ge=0)
    patent_search_bulkhead_capacity: int = Field(ge=1)
    patent_search_heavy_bulkhead_capacity: int = Field(ge=1)
    patent_search_bulkhead_acquire_timeout_seconds: float = Field(
        gt=0,
        le=0.1,
    )
    patent_search_deadline_seconds: float = Field(
        default=float(MAX_RUNTIME_REQUEST_DEADLINE_SECONDS),
        ge=1,
        le=MAX_RUNTIME_REQUEST_DEADLINE_SECONDS,
    )
    readiness_timeout_seconds: float = Field(default=1.0, gt=0, le=5)
    readiness_success_cache_seconds: float = Field(default=2.0, ge=0, le=30)
    readiness_failure_cache_seconds: float = Field(default=1.0, ge=0, le=30)

    query_vector_api_url: str = (
        "https://ark.cn-beijing.volces.com/api/v3/embeddings/multimodal"
    )
    query_vector_api_key: str = Field(default="", repr=False)
    query_vector_model_endpoint: str = ""
    query_vector_model_endpoints: dict[str, str] = Field(
        default_factory=dict,
        repr=False,
    )

    query_max_request_body_bytes: int = Field(
        default=DEFAULT_QUERY_BUDGET.max_request_body_bytes,
        ge=1,
        le=HARD_QUERY_BUDGET.max_request_body_bytes,
    )
    query_max_chars: int = Field(
        default=DEFAULT_QUERY_BUDGET.max_query_chars,
        ge=1,
        le=HARD_QUERY_BUDGET.max_query_chars,
    )
    query_max_nesting_depth: int = Field(
        default=DEFAULT_QUERY_BUDGET.max_nesting_depth,
        ge=1,
        le=HARD_QUERY_BUDGET.max_nesting_depth,
    )
    query_max_tokens: int = Field(
        default=DEFAULT_QUERY_BUDGET.max_tokens,
        ge=1,
        le=HARD_QUERY_BUDGET.max_tokens,
    )
    query_max_ast_nodes: int = Field(
        default=DEFAULT_QUERY_BUDGET.max_ast_nodes,
        ge=1,
        le=HARD_QUERY_BUDGET.max_ast_nodes,
    )
    query_max_boolean_clauses: int = Field(
        default=DEFAULT_QUERY_BUDGET.max_boolean_clauses,
        ge=1,
        le=HARD_QUERY_BUDGET.max_boolean_clauses,
    )
    query_max_page_size: int = Field(
        default=DEFAULT_QUERY_BUDGET.max_page_size,
        ge=1,
        le=HARD_QUERY_BUDGET.max_page_size,
    )
    query_max_result_window: int = Field(
        default=DEFAULT_QUERY_BUDGET.max_result_window,
        ge=1,
        le=HARD_QUERY_BUDGET.max_result_window,
    )

    @model_validator(mode="after")
    def validate_bulkhead_capacity(self) -> "Settings":
        """确保请求舱壁不会超过连接池，并为轻量读取保留容量。"""
        # OpenSearch 客户端连接池是更底层的硬资源；全局舱壁不能超过它，重请求
        # 舱壁还必须留下至少一个槽位给详情等轻量接口。
        if self.patent_search_bulkhead_capacity > self.opensearch_pool_maxsize:
            raise ValueError(
                "PATENT_SEARCH_BULKHEAD_CAPACITY must be less than or equal to "
                "OPENSEARCH_POOL_MAXSIZE"
            )
        if (
            self.patent_search_heavy_bulkhead_capacity
            >= self.patent_search_bulkhead_capacity
        ):
            raise ValueError(
                "PATENT_SEARCH_HEAVY_BULKHEAD_CAPACITY must be less than "
                "PATENT_SEARCH_BULKHEAD_CAPACITY so lightweight requests keep "
                "reserved capacity"
            )
        return self

    @model_validator(mode="after")
    def validate_console_credentials(self) -> "Settings":
        """在业务鉴权开启时验证 Console Basic 凭据及其与 API Token 的隔离。"""
        # 关闭业务鉴权时允许本地空凭据，开启后仍要求 Console 使用独立 Basic
        # 凭据；复用 API_TOKEN 会破坏浏览器/程序的权限隔离。
        if not self.enable_auth:
            return self
        try:
            _validate_basic_credentials(
                username=self.console_username,
                password=self.console_password,
                username_setting="CONSOLE_USERNAME",
                password_setting="CONSOLE_PASSWORD",
            )
        except ValueError as exc:
            raise ValueError(f"{exc} when ENABLE_AUTH is true") from exc
        if self.api_token and self.console_password == self.api_token:
            raise ValueError(
                "CONSOLE_PASSWORD must not reuse API_TOKEN"
            )
        return self

    @model_validator(mode="after")
    def validate_admin_settings(self) -> "Settings":
        """验证管理面及其草稿/运行时配置功能的依赖、权限与资源边界。

        管理看板关闭时允许无关观测配置保持 dormant；草稿和运行时配置一旦开启，
        必须先满足更基础的管理功能开关。启用管理面后再验证持久化路径、观测源与
        独立管理员凭据，避免控制面配置绕过启动期合同。
        """
        # 草稿与运行时覆盖是管理面的可选子能力，不能在未启用管理认证时单独打开。
        if self.admin_config_drafts_enabled and not self.admin_enabled:
            raise ValueError(
                "ADMIN_CONFIG_DRAFTS_ENABLED requires ADMIN_ENABLED"
            )
        if self.admin_runtime_config_enabled and not self.admin_config_drafts_enabled:
            raise ValueError(
                "ADMIN_RUNTIME_CONFIG_ENABLED requires ADMIN_CONFIG_DRAFTS_ENABLED"
            )
        # 管理看板是可选旁路。关闭时不验证 dormant 的 Prometheus/日志配置，
        # 开启后才检查 URL 形状、固定 job、超时、日志源和独立管理员凭据。
        if not self.admin_enabled:
            return self
        database_path = Path(self.admin_config_database_path)
        if (
            not self.admin_config_database_path
            or "\x00" in self.admin_config_database_path
            or not database_path.is_absolute()
            or database_path.parent == Path("/")
        ):
            raise ValueError(
                "ADMIN_CONFIG_DATABASE_PATH must be an absolute file path "
                "inside a dedicated state directory"
            )
        if self.admin_prometheus_url:
            parsed = urlsplit(self.admin_prometheus_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    "ADMIN_PROMETHEUS_URL must be an HTTP(S) base URL without "
                    "credentials, query, or fragment"
                )
        try:
            metrics_timeout = float(self.admin_metrics_timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "ADMIN_METRICS_TIMEOUT_SECONDS must be numeric"
            ) from exc
        if not 0 < metrics_timeout <= 5:
            raise ValueError(
                "ADMIN_METRICS_TIMEOUT_SECONDS must be greater than 0 and "
                "less than or equal to 5"
            )
        self.admin_metrics_timeout_seconds = metrics_timeout
        if _RELEASE_VALUE_PATTERN.fullmatch(self.admin_prometheus_job) is None:
            raise ValueError(
                "ADMIN_PROMETHEUS_JOB must be a bounded label value"
            )
        if self.admin_log_source not in {"journal", "process"}:
            raise ValueError(
                "ADMIN_LOG_SOURCE must be either journal or process"
            )
        _validate_basic_credentials(
            username=self.admin_viewer_username,
            password=self.admin_viewer_password,
            username_setting="ADMIN_VIEWER_USERNAME",
            password_setting="ADMIN_VIEWER_PASSWORD",
        )
        if self.api_token and self.admin_viewer_password == self.api_token:
            raise ValueError(
                "ADMIN_VIEWER_PASSWORD must not reuse API_TOKEN"
            )
        return self

    @model_validator(mode="after")
    def validate_release_metadata(self) -> "Settings":
        """限制会进入日志、页面或 Prometheus label 的发布元数据字符集与长度。"""
        # 这些值会进入 Prometheus label 和管理员页面，因此必须限制长度/字符集，
        # 避免把任意运行时文本变成高基数指标或 HTML/日志输入。
        if _COMMIT_PATTERN.fullmatch(self.service_release_commit) is None:
            raise ValueError(
                "SERVICE_RELEASE_COMMIT must be unknown or a 7-64 character "
                "hex commit"
            )
        for setting_name, value in (
            ("SERVICE_RELEASE_TAG", self.service_release_tag),
            ("SERVICE_INSTANCE_ID", self.service_instance_id),
        ):
            if _RELEASE_VALUE_PATTERN.fullmatch(value) is None:
                raise ValueError(
                    f"{setting_name} must be a bounded deployment identifier"
                )
        return self

    @model_validator(mode="after")
    def validate_query_vector_settings(self) -> "Settings":
        """校验查询向量路由，并拒绝缺少鉴权或含歧义的配置。"""
        parsed = urlsplit(self.query_vector_api_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "QUERY_VECTOR_API_URL must be an HTTPS URL without credentials, "
                "query, or fragment"
            )
        self.query_vector_api_key = self.query_vector_api_key.strip()
        self.query_vector_model_endpoint = self.query_vector_model_endpoint.strip()
        routes: dict[str, str] = {}
        for model, endpoint in self.query_vector_model_endpoints.items():
            normalized_model = model.strip()
            normalized_endpoint = endpoint.strip()
            if not normalized_model or not normalized_endpoint:
                raise ValueError(
                    "QUERY_VECTOR_MODEL_ENDPOINTS requires non-empty model and endpoint"
                )
            if normalized_model in routes:
                raise ValueError(
                    "QUERY_VECTOR_MODEL_ENDPOINTS contains duplicate model routes"
                )
            routes[normalized_model] = normalized_endpoint
        self.query_vector_model_endpoints = routes
        if self.query_vector_model_endpoint and routes:
            raise ValueError(
                "QUERY_VECTOR_MODEL_ENDPOINT and QUERY_VECTOR_MODEL_ENDPOINTS "
                "cannot both be set"
            )
        has_endpoint = bool(self.query_vector_model_endpoint or routes)
        if bool(self.query_vector_api_key) != has_endpoint:
            raise ValueError(
                "QUERY_VECTOR_API_KEY and a query-vector model endpoint must be set together"
            )
        return self

    @property
    def query_vector_endpoint_routes(self) -> dict[str, str]:
        """返回 model 到 provider endpoint 的受控路由；兼容当前单模型配置。"""
        if self.query_vector_model_endpoints:
            return dict(self.query_vector_model_endpoints)
        if self.query_vector_model_endpoint:
            return {
                DEFAULT_VECTOR_EMBEDDING_MODEL: self.query_vector_model_endpoint,
            }
        return {}

    @model_validator(mode="after")
    def validate_query_budget(self) -> "Settings":
        """确认运行时预算只能维持或收紧代码中的资源硬上限。"""
        # property 会再次构造 QueryBudget 并与代码硬上限比较，确保环境变量只能
        # 收紧资源边界，不能在部署时偷偷放宽它。
        self.query_budget
        return self

    @property
    def opensearch_url(self) -> str:
        """从受控的 scheme、host 和 port 组成 Repository 唯一使用的连接地址。"""
        # Repository 只从这里拿 scheme/host/port，调用方不再自行拼接连接地址。
        scheme = "https" if self.opensearch_use_https else "http"
        return f"{scheme}://{self.opensearch_host}:{self.opensearch_port}"

    @property
    def query_budget(self) -> QueryBudget:
        """构造当前配置的不可变预算快照，并再次验证字段间关系与硬上限。"""
        # 每次访问都生成不可变快照；字段之间的关系（例如结果窗口不能小于页大小）
        # 由 QueryBudget 自己验证，避免分散在多个路由中。
        budget = QueryBudget(
            max_request_body_bytes=self.query_max_request_body_bytes,
            max_query_chars=self.query_max_chars,
            max_nesting_depth=self.query_max_nesting_depth,
            max_tokens=self.query_max_tokens,
            max_ast_nodes=self.query_max_ast_nodes,
            max_boolean_clauses=self.query_max_boolean_clauses,
            max_page_size=self.query_max_page_size,
            max_result_window=self.query_max_result_window,
        )
        budget.ensure_within(HARD_QUERY_BUDGET)
        return budget


@lru_cache
def get_settings() -> Settings:
    """返回本进程唯一缓存的 Settings；配置变更必须通过重启形成新的启动合同。"""
    # FastAPI dependency 共享同一个配置对象；配置改变必须通过正式重启生效，
    # 不支持请求期间读取环境变量造成的半动态行为。
    return Settings()
