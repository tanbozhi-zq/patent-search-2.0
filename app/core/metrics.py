"""Prometheus 指标只允许固定的低基数 label。业务请求的原始 URL、查询文本、
request ID 和异常内容都不进入 label，以免指标系统被无限维度拖垮或泄露数据。
"""

from collections.abc import Iterable
from time import time
from typing import Any

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    ProcessCollector,
    generate_latest,
)

from app.core.exceptions import ERROR_REGISTRY
from app.core.timing_contract import DURATION_OVERSHOOT_BUCKET_SECONDS


METRICS_PATHS = frozenset({"/metrics", "/metrics/"})
ADMIN_PATHS = frozenset(
    {
        "/admin",
        "/admin/",
        "/admin/admin.css",
        "/admin/admin.js",
        "/admin/favicon.svg",
        "/admin-api/v1/status",
        "/admin-api/v1/metrics",
        "/admin-api/v1/config",
        "/admin-api/v1/config-schema",
        "/admin-api/v1/config-drafts",
        "/admin-api/v1/config-drafts/export",
        "/admin-api/v1/runtime-config",
        "/admin-api/v1/runtime-config/apply",
        "/admin-api/v1/runtime-config/rollback",
        "/admin-api/v1/logs",
    }
)
CONTROL_PLANE_ROUTES = frozenset({"/health", "/live", "/ready", "/startup"})
UNMATCHED_ROUTE = "__unmatched__"
OTHER_LABEL_VALUE = "other"

HTTP_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "POST"})
HTTP_STATUSES = frozenset(
    {
        "200",
        "307",
        "400",
        "401",
        "404",
        "405",
        "409",
        "413",
        "422",
        "429",
        "500",
        "502",
        "503",
        "504",
    }
)
HTTP_CODES = frozenset({"0", *(str(int(code)) for code in ERROR_REGISTRY)})
OPENSEARCH_OPERATIONS = frozenset({"count", "readiness", "search"})
OPENSEARCH_OUTCOMES = frozenset(
    {
        "connection_error",
        "error",
        "invalid_response",
        "success",
        "timeout",
        "unavailable",
        "unexpected_error",
    }
)
BULKHEAD_NAMES = frozenset({"global", "heavy_search"})
PROBE_NAMES = frozenset({"live", "ready", "startup"})
RATE_LIMIT_SCOPES = frozenset({"caller"})
QUERY_STAGES = frozenset(
    {"query_vector", "opensearch", "opensearch_took", "end_to_end"}
)
SEARCH_MODES = frozenset({"boolean", "vector", "hybrid"})
VECTOR_FIELD_COUNTS = frozenset(str(count) for count in range(6))
SEARCH_SORT_TYPES = frozenset({"relevance", "date"})
SEARCH_RANKING_PROFILES = frozenset(
    {
        "boolean",
        "patent-knn-cosine-v1",
        *(f"patent-vector-rrf-v1-{count}" for count in range(2, 6)),
        *(f"patent-hybrid-rrf-v1-{count}" for count in range(1, 5)),
    }
)
QUERY_STAGE_OUTCOMES = frozenset({"success", "timeout", "failure", "rejected"})

# Seconds. Keep a finite bucket beyond the 240-second execution budget so
# response mapping and cancellation cleanup do not collapse into +Inf.
HTTP_DURATION_BUCKETS = (
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1,
    2.5,
    5,
    10,
    30,
    60,
    120,
    180,
    240,
    DURATION_OVERSHOOT_BUCKET_SECONDS,
)
OPENSEARCH_DURATION_BUCKETS = HTTP_DURATION_BUCKETS


class ServiceMetrics:
    """一个进程本地、严格低基数的 Prometheus 指标注册表。

    每个 worker 维护自己的 CollectorRegistry，跨实例合并交给 Prometheus 查询层。所有
    标签必须先收敛到有限枚举，业务路径不得把原始 URL、用户输入或异常文本变成时间序列。
    """

    def __init__(
        self,
        *,
        http_routes: Iterable[str],
        started_at_seconds: float | None = None,
        version: str = "unknown",
        commit: str = "unknown",
        tag: str = "unknown",
    ) -> None:
        """创建当前 worker 独占的指标注册表及所有预声明时间序列。

        ``http_routes`` 是应用启动后得到的路由模板，会在此收敛为允许标签；版本与
        启动时间只写入 build/start 指标，不能由业务请求改变。构造阶段预置探针和
        舱壁等有限标签，避免首次请求才动态创建不受控的 Prometheus child。
        """
        # 每个 FastAPI worker 使用自己的 CollectorRegistry；横向扩容后的合并由
        # Prometheus 查询层完成，应用本身不假装拥有跨进程的全局计数。
        self.registry = CollectorRegistry()
        ProcessCollector(registry=self.registry)
        self.http_routes = frozenset(
            {
                route
                for route in http_routes
                if isinstance(route, str) and route and route not in METRICS_PATHS
            }
        ) | {UNMATCHED_ROUTE}

        self.http_requests = Counter(
            "patent_search_http_requests_total",
            "Completed HTTP requests.",
            ("method", "route", "status", "code"),
            registry=self.registry,
        )
        self.http_in_flight = Gauge(
            "patent_search_http_requests_in_flight",
            "HTTP requests currently executing in this process.",
            ("method",),
            registry=self.registry,
        )
        self.http_duration = Histogram(
            "patent_search_http_request_duration_seconds",
            "End-to-end HTTP request duration in seconds.",
            ("method", "route", "status", "code"),
            buckets=HTTP_DURATION_BUCKETS,
            registry=self.registry,
        )
        self.opensearch_calls = Counter(
            "patent_search_opensearch_calls_total",
            "Completed OpenSearch dependency calls.",
            ("operation", "outcome"),
            registry=self.registry,
        )
        self.opensearch_duration = Histogram(
            "patent_search_opensearch_call_duration_seconds",
            "OpenSearch dependency call duration in seconds.",
            ("operation", "outcome"),
            buckets=OPENSEARCH_DURATION_BUCKETS,
            registry=self.registry,
        )
        self.opensearch_retries = Counter(
            "patent_search_opensearch_retries_total",
            "Explicit OpenSearch retry attempts.",
            ("operation", "outcome"),
            registry=self.registry,
        )
        self.query_stage_calls = Counter(
            "patent_search_query_stage_calls_total",
            "Completed search stages by bounded request characteristics.",
            (
                "stage",
                "mode",
                "vector_field_count",
                "sort_type",
                "ranking_profile",
                "outcome",
            ),
            registry=self.registry,
        )
        self.query_stage_duration = Histogram(
            "patent_search_query_stage_duration_seconds",
            "Search stage duration in seconds by bounded request characteristics.",
            (
                "stage",
                "mode",
                "vector_field_count",
                "sort_type",
                "ranking_profile",
                "outcome",
            ),
            buckets=HTTP_DURATION_BUCKETS,
            registry=self.registry,
        )
        self.bulkhead_capacity = Gauge(
            "patent_search_bulkhead_capacity",
            "Configured process-local application bulkhead capacity.",
            ("bulkhead",),
            registry=self.registry,
        )
        self.bulkhead_in_flight = Gauge(
            "patent_search_bulkhead_in_flight",
            "Requests currently admitted by a process-local application bulkhead.",
            ("bulkhead",),
            registry=self.registry,
        )
        self.bulkhead_rejections = Counter(
            "patent_search_bulkhead_rejections_total",
            "Requests rejected by a process-local application bulkhead.",
            ("bulkhead",),
            registry=self.registry,
        )
        self.probe_status = Gauge(
            "patent_search_probe_status",
            "Last process-local probe state, where one is available and zero is unavailable.",
            ("probe",),
            registry=self.registry,
        )
        self.service_start_time = Gauge(
            "patent_search_service_start_time_seconds",
            "Unix time when this service instance began initialization.",
            registry=self.registry,
        )
        self.started_at_seconds = (
            time() if started_at_seconds is None else started_at_seconds
        )
        self.service_start_time.set(self.started_at_seconds)
        self.build_info = Gauge(
            "patent_search_build_info",
            "Build metadata for this service process.",
            ("version", "commit", "tag"),
            registry=self.registry,
        )
        self.build_info.labels(
            version=version,
            commit=commit,
            tag=tag,
        ).set(1)
        # #45 负责真正的调用方限流。这里只发布稳定的指标契约，不创建虚假的 caller
        # child，也就不会在限流尚未实现时制造运行序列。
        self.rate_limit_rejections = Counter(
            "patent_search_rate_limit_rejections_total",
            "Requests rejected by the future trusted-caller rate limiter.",
            ("scope",),
            registry=self.registry,
        )

        for probe in PROBE_NAMES:
            self.probe_status.labels(probe=probe).set(0)
        self.probe_status.labels(probe="live").set(1)

    def start_http_request(self, method: str) -> str:
        """增加指定受限 HTTP method 的在途数，并返回供完成路径复用的安全标签。"""
        # 先增加在途数，完成/取消路径必须与这里一一对应。
        safe_method = _allow(method.upper(), HTTP_METHODS)
        self.http_in_flight.labels(method=safe_method).inc()
        return safe_method

    def finish_http_request(
        self,
        *,
        method: str,
        route: str,
        status: int,
        code: int,
        elapsed_seconds: float,
    ) -> None:
        """写入一次完成请求的计数和耗时，并在 finally 中成对撤销在途数。

        route、status、业务 code 和 method 均会先收敛为允许标签；即使 prometheus-client
        的计数/直方图更新异常，也不能让 in-flight gauge 永久偏高。
        """
        # 所有 label 在写入前都收敛到固定集合；finally 保证即便 prometheus
        # client 出错也会把在途数减回去。
        safe_method = _allow(method.upper(), HTTP_METHODS)
        safe_route = _allow(route, self.http_routes, fallback=UNMATCHED_ROUTE)
        safe_status = _allow(str(status), HTTP_STATUSES)
        safe_code = _allow(str(code), HTTP_CODES)
        labels = {
            "method": safe_method,
            "route": safe_route,
            "status": safe_status,
            "code": safe_code,
        }
        try:
            self.http_requests.labels(**labels).inc()
            self.http_duration.labels(**labels).observe(max(0.0, elapsed_seconds))
        finally:
            self.http_in_flight.labels(method=safe_method).dec()

    def cancel_http_request(self, *, method: str) -> None:
        # ASGI 在 response.start 前被取消时没有完整 status，只能撤销在途 gauge。
        safe_method = _allow(method.upper(), HTTP_METHODS)
        self.http_in_flight.labels(method=safe_method).dec()

    def record_opensearch_call(
        self,
        *,
        operation: str,
        outcome: str,
        elapsed_seconds: float,
    ) -> None:
        """记录一次已完成的 OpenSearch 业务操作及其受限 outcome 和耗时。"""
        # operation/outcome 使用有限枚举，与 repository 的异常翻译保持一致。
        labels = {
            "operation": _allow(operation, OPENSEARCH_OPERATIONS),
            "outcome": _allow(outcome, OPENSEARCH_OUTCOMES),
        }
        self.opensearch_calls.labels(**labels).inc()
        self.opensearch_duration.labels(**labels).observe(
            max(0.0, elapsed_seconds)
        )

    def record_opensearch_retry(self, *, operation: str, outcome: str) -> None:
        self.opensearch_retries.labels(
            operation=_allow(operation, OPENSEARCH_OPERATIONS),
            outcome=_allow(outcome, OPENSEARCH_OUTCOMES),
        ).inc()

    def record_query_stage(
        self,
        *,
        stage: str,
        mode: str,
        vector_field_count: int,
        sort_type: str,
        ranking_profile: str,
        outcome: str,
        elapsed_seconds: float,
    ) -> None:
        """记录查询阶段；所有请求特征先收敛到固定枚举。"""
        labels = {
            "stage": _allow(stage, QUERY_STAGES),
            "mode": _allow(mode, SEARCH_MODES),
            "vector_field_count": _allow(
                str(vector_field_count),
                VECTOR_FIELD_COUNTS,
            ),
            "sort_type": _allow(sort_type, SEARCH_SORT_TYPES),
            "ranking_profile": _allow(
                ranking_profile,
                SEARCH_RANKING_PROFILES,
            ),
            "outcome": _allow(outcome, QUERY_STAGE_OUTCOMES),
        }
        self.query_stage_calls.labels(**labels).inc()
        self.query_stage_duration.labels(**labels).observe(
            max(0.0, elapsed_seconds)
        )

    def initialize_bulkhead(self, *, name: str, capacity: int) -> None:
        # capacity 是配置值，in_flight 在每次进程启动时显式归零。
        safe_name = _allow(name, BULKHEAD_NAMES)
        self.bulkhead_capacity.labels(bulkhead=safe_name).set(capacity)
        self.bulkhead_in_flight.labels(bulkhead=safe_name).set(0)

    def set_bulkhead_in_flight(self, *, name: str, in_flight: int) -> None:
        self.bulkhead_in_flight.labels(
            bulkhead=_allow(name, BULKHEAD_NAMES),
        ).set(max(0, in_flight))

    def record_bulkhead_rejection(self, *, name: str) -> None:
        self.bulkhead_rejections.labels(
            bulkhead=_allow(name, BULKHEAD_NAMES),
        ).inc()

    def set_probe_status(self, *, probe: str, available: bool) -> None:
        self.probe_status.labels(
            probe=_allow(probe, PROBE_NAMES),
        ).set(1 if available else 0)

    def record_rate_limit_rejection(self, *, scope: str = "caller") -> None:
        self.rate_limit_rejections.labels(
            scope=_allow(scope, RATE_LIMIT_SCOPES),
        ).inc()

    def render(self) -> bytes:
        # 导出当前进程 registry 的原生 Prometheus 文本格式。
        return generate_latest(self.registry)


def call_metrics(metrics: Any, method_name: str, /, **fields: Any) -> Any:
    """尽力调用可选指标对象，保证观测故障不会传播回业务请求路径。

    该辅助只解决观测对象不存在、接口缺失或自身异常的隔离；调用方仍负责传递已经
    收敛过的 label 值，不能把它当作任意指标写入的安全过滤器。
    """
    if metrics is None:
        return None
    method = getattr(metrics, method_name, None)
    if not callable(method):
        return None
    try:
        return method(**fields)
    except Exception:
        return None


def should_observe_http_path(path: str) -> bool:
    # metrics 自身、管理轮询和静态管理资源不计入业务吞吐/成功率，避免控制面
    # 噪声改变检索 SLI；live/startup/ready/health 则保留请求日志但单独处理 code。
    if path in METRICS_PATHS:
        return False
    return path not in ADMIN_PATHS


def _allow(
    value: str,
    allowed: frozenset[str],
    *,
    fallback: str = OTHER_LABEL_VALUE,
) -> str:
    # 未知值统一归入 other，而不是把外部输入直接变成新的时间序列。
    return value if value in allowed else fallback
