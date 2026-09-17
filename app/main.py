"""这里是 FastAPI 应用的组装入口。业务规则分别放在 api、service、query 和
repository 包中；本文件只负责把共享资源、路由、中间件和生命周期接起来。
"""

from contextlib import asynccontextmanager
from html import escape
import logging
from pathlib import Path

# FastAPI 框架基础能力
from fastapi import Depends, FastAPI, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse

# 按业务功能拆分的路由：检索、详情、引文、法务状态、探针、监控、管理员入口
from app.api.admin import router as admin_router
from app.api.admin_config import router as admin_config_router
from app.api.admin_runtime_config import router as admin_runtime_config_router
from app.api.citations import router as citations_router
from app.api.console import router as console_router
from app.api.detail import router as detail_router
from app.api.legal_history import router as legal_history_router
from app.api.metrics import router as metrics_router
from app.api.probes import router as probes_router
from app.api.search import router as search_router

# 管理配置/运行时控制（配置草稿、运行时快照、配置作用域）
from app.core.admin_config import (
    AdminConfigDraftStore,
    CallbackRuntimeConfigVerifier,
    RuntimeConfigController,
    RuntimeConfigProvider,
    runtime_config_scope,
    runtime_snapshot_from_settings,
)

# 请求保护和并发控制
from app.core.bulkhead import ApplicationBulkhead

# 管理端日志读取（进程日志或 journal）
from app.core.admin_logs import (
    ProcessAdminLogReader,
    create_systemd_journal_reader,
)

# 管理端 Prometheus 指标读取
from app.core.admin_metrics import PrometheusAdminMetricsReader

# 全局配置、超时、错误与日志统一处理
from app.core.config import get_settings
from app.core.deadline import request_deadline
from app.core.error_handlers import REQUEST_ID_HEADER, error_openapi_responses, register_error_handlers
from app.core.logging import configure_logging, log_event

# 可观测与探针
from app.core.metrics import ServiceMetrics, call_metrics
from app.core.probes import ReadinessProbe, ServiceLifecycle

# 请求体预算限制与安全控制
from app.core.request_body_limit import QueryRequestBodyLimitMiddleware
from app.core.security import require_console_access

# 查询向量供应商适配器
from app.integrations.query_vector import ArkQueryVectorAdapter
from app.mappings.query_field_mapping import VECTOR_FIELD_REGISTRY

# 查询预算与 OpenSearch 仓储
from app.query.budget import StaticQueryBudgetProvider
from app.repositories.opensearch_repo import OpenSearchRepository, build_readiness_client

# 响应模型与版本号
from app.schemas.response import HealthResponse
from app.version import __version__


# 日志先于 Settings 初始化，保证启动阶段的配置错误也能按统一格式输出。
structured_log_buffer = configure_logging()
logger = logging.getLogger(__name__)
settings = get_settings()
query_budget_provider = StaticQueryBudgetProvider(settings.query_budget)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """在一个 worker 的完整生命周期内创建、发布和有序关闭共享运行时资源。

    这里建立 Repository、独立 readiness client、舱壁、探针和可选管理旁路，再把它们
    放入 ``app.state`` 供依赖注入轻量引用。初始化失败不能留下半启动状态；关闭则按
    依赖的反向顺序释放资源，使 readiness 在真正断开依赖前先停止对外宣称可用。
    """
    # 这些对象都是“进程级”资源：同一个 worker 内的请求共享它们，关闭时
    # 由这里按相反顺序释放。请求本身只通过 app.state 取用，不在每次调用时
    # 重建 OpenSearch 客户端、舱壁或观测客户端。
    service_metrics = ServiceMetrics(
        http_routes=(getattr(route, "path", "") for route in app.routes),
        version=__version__,
        commit=settings.service_release_commit,
        tag=settings.service_release_tag,
    )
    lifecycle = ServiceLifecycle(metrics=service_metrics)
    app.state.service_metrics = service_metrics
    app.state.probe_lifecycle = lifecycle
    try:
        runtime_config_provider = RuntimeConfigProvider(
            runtime_snapshot_from_settings(settings)
        )
        # 全局舱壁控制所有业务 OpenSearch 请求；重请求舱壁只包住检索和目标
        # 排名，并且容量必须更小，以便详情、引证和法律状态仍保留一个入口。
        global_bulkhead = ApplicationBulkhead(
            capacity=settings.patent_search_bulkhead_capacity,
            acquire_timeout_seconds=(
                settings.patent_search_bulkhead_acquire_timeout_seconds
            ),
            name="global",
            metrics=service_metrics,
        )
        heavy_bulkhead = ApplicationBulkhead(
            capacity=settings.patent_search_heavy_bulkhead_capacity,
            acquire_timeout_seconds=(
                settings.patent_search_bulkhead_acquire_timeout_seconds
            ),
            name="heavy_search",
            metrics=service_metrics,
        )
        # Keep the long-standing one-argument construction contract: internal
        # test doubles and integrations may provide a lightweight repository
        # with only ``settings``.  The real repository binds the shared
        # process-level runtime provider before the lifespan accepts traffic.
        repository = OpenSearchRepository(settings=settings)
        bind_runtime_config_provider = getattr(
            repository,
            "bind_runtime_config_provider",
            None,
        )
        if callable(bind_runtime_config_provider):
            bind_runtime_config_provider(runtime_config_provider)
        call_metrics(
            repository,
            "bind_metrics",
            metrics=service_metrics,
        )
        readiness_client = build_readiness_client(settings)

        def check_readiness() -> bool:
            # readiness 只做廉价的 exists/HEAD 类检查，不执行搜索、计数或集群
            # 健康查询；它使用独立客户端，避免业务流量耗尽时探针也被拖住。
            return bool(
                readiness_client.indices.exists(
                    index=settings.opensearch_index,
                    params={"request_timeout": settings.readiness_timeout_seconds},
                )
            )

        readiness_probe = ReadinessProbe(
            check=check_readiness,
            timeout_seconds=settings.readiness_timeout_seconds,
            success_cache_seconds=settings.readiness_success_cache_seconds,
            failure_cache_seconds=settings.readiness_failure_cache_seconds,
            metrics=service_metrics,
        )
    except BaseException:
        lifecycle.mark_failed()
        raise

    admin_metrics_reader = None
    if settings.admin_enabled and settings.admin_prometheus_url:
        try:
            # 管理看板的 Prometheus 读取器是可选旁路。初始化或后续单项查询
            # 失败只能让看板显示 partial/unavailable，不能阻止公开搜索服务启动。
            admin_metrics_reader = PrometheusAdminMetricsReader(
                base_url=settings.admin_prometheus_url,
                timeout_seconds=settings.admin_metrics_timeout_seconds,
                job=settings.admin_prometheus_job,
            )
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                "admin_metrics_source_unavailable",
                outcome="initialization_failed",
                exception_type=type(exc).__name__,
            )

    async def verify_runtime_config(_snapshot) -> bool:
        if not lifecycle.is_started:
            return False
        observed = repository.runtime_config_snapshot()
        if (
            observed.version != _snapshot.version
            or observed.values != _snapshot.values
        ):
            return False
        return await readiness_probe.is_ready(force_refresh=True)

    admin_config_store = AdminConfigDraftStore(settings.admin_config_database_path)
    runtime_config_controller = RuntimeConfigController(
        settings=settings,
        provider=runtime_config_provider,
        store=admin_config_store,
        verifier=CallbackRuntimeConfigVerifier(verify_runtime_config),
    )

    # 将本次启动创建的对象放入 app.state，FastAPI dependency 只做轻量引用；运行时
    # 配置 provider/controller 也在这里拥有与 Repository 相同的进程生命周期。
    app.state.opensearch_repository = repository
    app.state.query_budget_provider = query_budget_provider
    app.state.runtime_config_provider = runtime_config_provider
    app.state.runtime_config_controller = runtime_config_controller
    app.state.admin_config_store = admin_config_store
    app.state.search_request_bulkhead = global_bulkhead
    app.state.heavy_search_request_bulkhead = heavy_bulkhead
    app.state.readiness_probe = readiness_probe
    app.state.readiness_client = readiness_client
    app.state.admin_metrics_reader = admin_metrics_reader
    # 日志读取器只允许看到业务路由，不把管理看板自身的轮询和鉴权事件混入
    # 当前进程的业务关联窗口；journal 模式还会在读取层再次做事件白名单校验。
    allowed_log_routes = frozenset(
        path
        for route in app.routes
        if isinstance((path := getattr(route, "path", None)), str)
        and not (
            path in {"/admin", "/admin/"}
            or path.startswith(("/admin/", "/admin-api/"))
        )
    ) | {"__unmatched__"}
    if not settings.admin_enabled or settings.admin_log_source == "process":
        admin_log_reader = ProcessAdminLogReader(structured_log_buffer)
    else:
        admin_log_reader = create_systemd_journal_reader(
            allowed_routes=allowed_log_routes,
        )
    # 所有其他同步依赖构造成功后才启动查询向量后台 loop；此后只发布 app.state
    # 并进入受 finally 保护的 lifespan，启动失败不会遗留无人回收的线程。
    query_vector_adapter = ArkQueryVectorAdapter(
        api_url=settings.query_vector_api_url,
        api_key=settings.query_vector_api_key,
        model_endpoints=settings.query_vector_endpoint_routes,
        max_connections=settings.patent_search_heavy_bulkhead_capacity,
    )
    app.state.query_vector_adapter = query_vector_adapter
    app.state.admin_log_reader = admin_log_reader
    lifecycle.mark_started()
    try:
        yield
    finally:
        # 先标记 stopping，让 startup/readiness 在关闭窗口内立即失效；随后
        # 释放外部 HTTP 客户端、日志读取器、探针线程和 OpenSearch 连接。
        lifecycle.mark_stopping()
        try:
            if admin_metrics_reader is not None:
                await admin_metrics_reader.close()
        finally:
            try:
                await admin_log_reader.close()
            finally:
                try:
                    await readiness_probe.close()
                finally:
                    try:
                        readiness_client.close()
                    finally:
                        try:
                            query_vector_adapter.close()
                        finally:
                            try:
                                repository.close()
                            finally:
                                del app.state.opensearch_repository
                                del app.state.query_vector_adapter
                                del app.state.query_budget_provider
                                del app.state.runtime_config_provider
                                del app.state.runtime_config_controller
                                del app.state.admin_config_store
                                del app.state.search_request_bulkhead
                                del app.state.heavy_search_request_bulkhead
                                del app.state.readiness_probe
                                del app.state.readiness_client
                                del app.state.admin_metrics_reader
                                del app.state.admin_log_reader
                                del app.state.probe_lifecycle
                                del app.state.service_metrics


# 应用对象在模块级导出，便于 uvicorn、测试客户端和集成代码直接导入。
app = FastAPI(
    title="patent-search-service",
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
    responses=error_openapi_responses(),
    lifespan=lifespan,
)

# 请求体限制必须在路由解析前生效；RequestContextMiddleware 由错误处理器注册，
# 两者共同保证过大的请求也能拿到统一错误体和 X-Request-ID。
app.add_middleware(
    QueryRequestBodyLimitMiddleware,
    budget_provider=query_budget_provider,
)


@app.middleware("http")
async def _request_deadline_middleware(request: Request, call_next):
    """为请求固定运行时配置快照，并按其中的预算绑定绝对 deadline。

    同一请求内的中间件、依赖、查询解析和 OpenSearch I/O 必须看到相同配置；若
    应用已完成显式测试生命周期关闭则保持透传，避免 teardown 中的无匹配请求被
    误转为控制面 500。
    """
    runtime_config_provider = getattr(
        request.app.state,
        "runtime_config_provider",
        None,
    )
    # Starlette 可能在显式 TestClient 生命周期结束后仍路由一次最终测试请求；生产
    # 流量始终具备 provider。此时保留旧透传语义，避免无匹配路由被误报为 500。
    if runtime_config_provider is None:
        return await call_next(request)
    runtime_config = runtime_config_provider.snapshot()
    request.state.runtime_config_snapshot = runtime_config
    with runtime_config_scope(runtime_config), request_deadline(
        float(runtime_config.value_for("request.deadline_seconds"))
    ):
        return await call_next(request)


# 先注册错误边界，再挂载业务路由，确保依赖、解析器和下游异常都走统一契约。
register_error_handlers(app)


# 正式 API、内部 Console、探针、指标和管理看板分组挂载；各 router 自己声明
# 鉴权与舱壁依赖，避免在这里用一个全局依赖误伤无需鉴权的控制面接口。
app.include_router(search_router)
app.include_router(detail_router)
app.include_router(citations_router)
app.include_router(legal_history_router)
app.include_router(console_router)
app.include_router(probes_router)
app.include_router(metrics_router)
app.include_router(admin_router)
app.include_router(admin_config_router)
app.include_router(admin_runtime_config_router)

console_index_file = Path(__file__).resolve().parent / "static" / "console" / "index.html"
_console_vector_options = "\n".join(
    '<option value="{}"{}>{}</option>'.format(
        escape(name, quote=True),
        " selected" if index == 0 else "",
        escape(name),
    )
    for index, name in enumerate(VECTOR_FIELD_REGISTRY)
)
console_index_html = console_index_file.read_text(encoding="utf-8").replace(
    "<!-- VECTOR_FIELD_OPTIONS -->",
    _console_vector_options,
)


@app.get(
    "/console",
    dependencies=[Depends(require_console_access)],
    include_in_schema=False,
)
@app.get(
    "/console/",
    dependencies=[Depends(require_console_access)],
    include_in_schema=False,
)
def console_page() -> HTMLResponse:
    # HTML 由浏览器的 Basic 认证保护，页面不携带 API Token，也不把查询状态
    # 写入 localStorage；no-store 避免凭据保护范围内的页面被缓存复用。
    return HTMLResponse(
        console_index_html,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "base-uri 'none'; frame-ancestors 'none'",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _openapi() -> dict:
    """生成并缓存 OpenAPI 文档，同时写入公共错误信封与 Request ID 响应头契约。"""
    # FastAPI 默认会为 Pydantic 校验生成 422。项目对外使用 40002/40003/40004
    # 等稳定错误码，因此这里清理默认 422，并为每个响应补上请求 ID 响应头契约。
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if isinstance(operation, dict):
                responses = operation.get("responses", {})
                responses.pop("422", None)
                for response in responses.values():
                    if isinstance(response, dict):
                        response.setdefault("headers", {}).setdefault(
                            REQUEST_ID_HEADER,
                            {
                                "description": "服务接受合法入口值或生成的请求关联 ID。",
                                "schema": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 64,
                                    "pattern": "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
                                },
                            },
                        )
    schemas = schema.get("components", {}).get("schemas", {})
    schemas.pop("HTTPValidationError", None)
    schemas.pop("ValidationError", None)
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = _openapi


@app.get("/health", response_model=HealthResponse)
def health():
    # 这是历史兼容入口，继续保持旧的 healthy 信封；新部署应分别使用 live、
    # startup 和 ready 来表达三种不同的状态。
    return {
        "success": True,
        "code": 0,
        "message": "ok",
        "data": {
            "status": "healthy",
            "service": "patent-search-service",
        },
    }
