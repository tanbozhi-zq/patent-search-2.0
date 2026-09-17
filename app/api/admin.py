"""管理看板是只读控制面：它只暴露发布身份、固定 PromQL、白名单配置和有界日志，
不修改 Settings、不调用 OpenSearch、不接受浏览器提交的任意 PromQL/命令。
"""

from datetime import datetime, timezone
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse

from app.core.admin_logs import (
    ADMIN_LOG_MAX_CURSOR_LENGTH,
    ADMIN_LOG_MAX_PAGE_SIZE,
    ADMIN_LOG_MAX_WINDOW_SECONDS,
    AdminLogFilters,
    AdminLogReader,
)
from app.core.admin_metrics import (
    ADMIN_METRIC_WINDOWS,
    PrometheusAdminMetricsReader,
    unavailable_metrics,
)
from app.core.admin_config import (
    CONFIG_PARAMETER_REGISTRY,
    RuntimeConfigProvider,
    current_config_values,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import ERROR_REGISTRY, ErrorCode, service_error
from app.core.logging import log_event
from app.core.metrics import ServiceMetrics
from app.core.request_context import UNMATCHED_ROUTE, is_valid_request_id
from app.core.security import AdminPrincipal, require_admin
from app.schemas.admin import (
    AdminConfigItem,
    AdminConfigResponse,
    AdminLogsResponse,
    AdminMetricsResponse,
    AdminReleaseInfo,
    AdminStatusResponse,
)
from app.version import __version__


router = APIRouter(tags=["admin"])
logger = logging.getLogger(__name__)
_LOG_CODES = frozenset({0, *(int(code) for code in ERROR_REGISTRY)})

_STATIC_ROOT = Path(__file__).resolve().parents[1] / "static" / "admin"
_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; connect-src 'self'; "
        "font-src 'self'; form-action 'self'; frame-ancestors 'none'; "
        "img-src 'self'; object-src 'none'; script-src 'self'; "
        "style-src 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}
_ASSET_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


def get_admin_metrics_reader(
    request: Request,
) -> PrometheusAdminMetricsReader | None:
    # Prometheus reader 在 lifespan 中按配置可选创建；未配置时 API 仍可返回身份、
    # 配置和日志范围，只把 metrics 卡片标记为 unavailable。
    return getattr(request.app.state, "admin_metrics_reader", None)


def get_admin_log_reader(request: Request) -> AdminLogReader:
    # 日志 reader 同样在 lifespan 中选择 journal/process/unavailable 实现，路由不
    # 关心底层是 systemd 还是进程缓冲。
    return request.app.state.admin_log_reader


def get_runtime_config_provider(request: Request) -> RuntimeConfigProvider:
    return request.app.state.runtime_config_provider


@router.get("/admin", include_in_schema=False)
@router.get("/admin/", include_in_schema=False)
def admin_page(
    principal: AdminPrincipal = Depends(require_admin),
) -> FileResponse:
    # 页面和静态资源都走独立 viewer 鉴权；页面本身不注入任何秘密或运行时配置。
    _audit(principal, action="page", result="ok")
    return FileResponse(_STATIC_ROOT / "index.html", headers=_PAGE_HEADERS)


@router.get("/admin/admin.css", include_in_schema=False)
def admin_styles(
    _principal: AdminPrincipal = Depends(require_admin),
) -> FileResponse:
    return FileResponse(
        _STATIC_ROOT / "admin.css",
        media_type="text/css",
        headers=_ASSET_HEADERS,
    )


@router.get("/admin/admin.js", include_in_schema=False)
def admin_script(
    _principal: AdminPrincipal = Depends(require_admin),
) -> FileResponse:
    return FileResponse(
        _STATIC_ROOT / "admin.js",
        media_type="text/javascript",
        headers=_ASSET_HEADERS,
    )


@router.get("/admin/favicon.svg", include_in_schema=False)
def admin_favicon(
    _principal: AdminPrincipal = Depends(require_admin),
) -> FileResponse:
    return FileResponse(
        _STATIC_ROOT / "favicon.svg",
        media_type="image/svg+xml",
        headers=_ASSET_HEADERS,
    )


@router.get(
    "/admin-api/v1/status",
    response_model=AdminStatusResponse,
    include_in_schema=False,
)
async def admin_status(
    request: Request,
    http_response: Response,
    principal: AdminPrincipal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> AdminStatusResponse:
    """返回管理看板启动所需的运行身份与能力边界。

    该接口刻意不承担健康检查，也不从 Prometheus 或 journal 拉取明细；它只把
    当前发布标识、已接入的观测能力和可筛选路由汇总成轻量快照。这样前端可以
    明确区分服务已启动、指标源可用与日志源可用这几个彼此独立的事实。
    """
    # status 组合发布身份与日志/指标能力边界，帮助使用者区分“代码已合并”“已部署”
    # 和“Prometheus/日志已接通”这几个不相等的状态。
    _no_store(http_response)
    service_metrics: ServiceMetrics = request.app.state.service_metrics
    metrics_available = get_admin_metrics_reader(request) is not None
    log_reader = get_admin_log_reader(request)
    notices = [
        "代码合并、部署完成与生产启用是三个独立状态。",
    ]
    if log_reader.scope == "journal_local":
        notices.insert(0, "关联日志仅查询当前实例的 patent-search journal namespace。")
    elif log_reader.scope == "current_process":
        notices.insert(0, "本地降级模式只查询当前进程最近的有界结构化事件。")
    else:
        notices.insert(0, "Journal 依赖或读取权限不可用，关联日志分区已降级。")
    if not metrics_available:
        notices.insert(0, "Prometheus 查询源未配置，聚合指标暂不可用。")
    response = AdminStatusResponse(
        role=principal.role,
        config_drafts_enabled=settings.admin_config_drafts_enabled,
        runtime_config_enabled=settings.admin_runtime_config_enabled,
        release=AdminReleaseInfo(
            service_version=__version__,
            commit=settings.service_release_commit,
            tag=settings.service_release_tag,
            instance_id=settings.service_instance_id,
            started_at=datetime.fromtimestamp(
                service_metrics.started_at_seconds,
                tz=timezone.utc,
            ),
        ),
        metrics_source="prometheus" if metrics_available else "unavailable",
        log_scope=log_reader.scope,
        log_routes=sorted(_log_route_templates(request)),
        notices=notices,
    )
    _audit(principal, action="status.read", result="ok")
    return response


@router.get(
    "/admin-api/v1/metrics",
    response_model=AdminMetricsResponse,
    include_in_schema=False,
)
async def admin_metrics(
    http_response: Response,
    principal: AdminPrincipal = Depends(require_admin),
    window_seconds: int = Query(default=300),
    reader: PrometheusAdminMetricsReader | None = Depends(get_admin_metrics_reader),
) -> AdminMetricsResponse:
    """读取固定时间窗口内的只读 Prometheus 管理指标。

    ``window_seconds`` 只能取预定义窗口，具体 PromQL 由 reader 封装，避免管理
    页面演变为任意查询代理。指标源未配置或部分查询失败时仍返回可说明状态的
    响应，而不是把观测系统自身的问题升级为业务接口故障。
    """
    # 窗口只能从三个固定值中选择，查询表达式由 reader 在服务端生成；单项失败
    # 记录为 partial，不把管理数据源故障传播成公开 API 的 50301。
    _no_store(http_response)
    if window_seconds not in ADMIN_METRIC_WINDOWS:
        raise service_error(ErrorCode.INVALID_REQUEST)
    response = (
        await reader.read(window_seconds)
        if reader is not None
        else unavailable_metrics(window_seconds)
    )
    _audit(
        principal,
        action="metrics.read",
        result="partial" if response.partial else "ok",
        returned_count=sum(len(result.samples) for result in response.results),
        window_seconds=window_seconds,
    )
    return response


@router.get(
    "/admin-api/v1/config",
    response_model=AdminConfigResponse,
    include_in_schema=False,
)
async def admin_config(
    request: Request,
    http_response: Response,
    principal: AdminPrincipal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
    runtime_config: RuntimeConfigProvider = Depends(get_runtime_config_provider),
) -> AdminConfigResponse:
    """返回供管理看板展示的配置白名单，不序列化完整 Settings。

    运行参数和查询上限可以展示具体值；凭据类字段只返回是否配置。字段名单由
    ``_safe_config_items`` 固定维护，因此新增配置不会在未审查的情况下自动暴露。
    """
    # 配置响应只使用 _safe_config_items 的显式白名单，绝不序列化整个 Settings。
    _no_store(http_response)
    snapshot = runtime_config.snapshot()
    response = AdminConfigResponse(
        runtime_version=snapshot.version,
        runtime_source=snapshot.source,
        items=_safe_config_items(settings, runtime_values=snapshot.as_dict()),
    )
    _audit(
        principal,
        action="config.read",
        result="ok",
        returned_count=len(response.items),
    )
    return response


@router.get(
    "/admin-api/v1/logs",
    response_model=AdminLogsResponse,
    include_in_schema=False,
)
async def admin_logs(
    request: Request,
    http_response: Response,
    principal: AdminPrincipal = Depends(require_admin),
    window_seconds: int = Query(
        default=3600,
        ge=60,
        le=ADMIN_LOG_MAX_WINDOW_SECONDS,
    ),
    limit: int = Query(default=50, ge=1, le=ADMIN_LOG_MAX_PAGE_SIZE),
    cursor: str | None = Query(default=None, max_length=ADMIN_LOG_MAX_CURSOR_LENGTH),
    request_id: str | None = Query(default=None, max_length=64),
    route: str | None = Query(default=None, max_length=160),
    code: int | None = Query(default=None, ge=0, le=99_999),
    include_system: bool = Query(default=True),
    reader: AdminLogReader = Depends(get_admin_log_reader),
) -> AdminLogsResponse:
    """按受控时间窗口和白名单筛选读取关联日志的一页结果。

    路由、错误码和 request id 在进入 reader 前完成校验；分页 cursor 同时绑定
    查询窗口与筛选条件，不能跨查询复用。底层 reader 可来自本地 journal、进程
    缓冲或不可用降级实现，路由只把它们统一为稳定的管理响应和审计事件。
    """
    # 所有筛选项都在服务端做长度/格式/枚举验证；cursor 绑定窗口和筛选指纹，
    # 防止把一个查询的分页游标挪到另一个查询继续使用。
    _no_store(http_response)
    if request_id is not None and not is_valid_request_id(request_id):
        raise service_error(ErrorCode.INVALID_REQUEST)
    if route is not None:
        if route not in _log_route_templates(request):
            raise service_error(ErrorCode.INVALID_REQUEST)
    if code is not None and code not in _LOG_CODES:
        raise service_error(ErrorCode.INVALID_REQUEST)
    try:
        response = await reader.read(
            window_seconds=window_seconds,
            limit=limit,
            cursor=cursor,
            filters=AdminLogFilters(
                request_id=request_id,
                route=route,
                code=code,
                include_system=include_system,
            ),
        )
    except ValueError as exc:
        raise service_error(ErrorCode.INVALID_REQUEST) from exc
    _audit(
        principal,
        action="logs.read",
        result=(
            "unavailable"
            if not response.available
            else "truncated" if response.truncated else "ok"
        ),
        returned_count=len(response.items),
        window_seconds=window_seconds,
    )
    return response


def _safe_config_items(
    settings: Settings,
    *,
    runtime_values: dict[str, int | float] | None = None,
) -> list[AdminConfigItem]:
    """将部署配置和当前运行时快照投影为经过审计的展示白名单。

    可热更新的参数从 ``runtime_values`` 读取，其他运行参数仍来自启动期 Settings；
    秘密始终只显示是否配置。函数只遍历显式注册表而不序列化整个 Settings，因此
    新增地址、索引或凭据字段不会因配置模型演进而意外暴露给管理页面。
    """
    # 这是管理页面的“数据泄露闸门”：注册表定义可展示项，秘密不输出其实际值。
    values = current_config_values(settings, runtime_values=runtime_values)
    items = [
        AdminConfigItem(
            key="auth.enabled",
            label="业务接口鉴权",
            category="runtime",
            value=str(settings.enable_auth).lower(),
        )
    ]
    items.extend(
        AdminConfigItem(
            key=definition.key,
            label=definition.label,
            category=(
                "limit" if definition.category == "query_budget" else "runtime"
            ),
            value=str(values[definition.key]),
        )
        for definition in CONFIG_PARAMETER_REGISTRY
    )
    items.extend(
        AdminConfigItem(
            key=key,
            label=label,
            category="runtime",
            value=str(value),
        )
        for key, label, value in (
            (
                "readiness.timeout_seconds",
                "Readiness 超时",
                settings.readiness_timeout_seconds,
            ),
            (
                "readiness.success_cache_seconds",
                "Readiness 成功缓存",
                settings.readiness_success_cache_seconds,
            ),
            (
                "readiness.failure_cache_seconds",
                "Readiness 失败缓存",
                settings.readiness_failure_cache_seconds,
            ),
        )
    )
    items.extend(
        (
            AdminConfigItem(
                key="secret.api_token",
                label="业务 API Token",
                category="secret",
                configured=bool(settings.api_token),
            ),
            AdminConfigItem(
                key="secret.console_password",
                label="Console 密码",
                category="secret",
                configured=bool(settings.console_password),
            ),
            AdminConfigItem(
                key="secret.opensearch_credentials",
                label="OpenSearch 凭据",
                category="secret",
                configured=bool(settings.opensearch_user and settings.opensearch_pass),
            ),
            AdminConfigItem(
                key="secret.admin_viewer_password",
                label="管理员密码",
                category="secret",
                configured=bool(settings.admin_viewer_password),
            ),
        )
    )
    return items


def _log_route_templates(request: Request) -> set[str]:
    """提取可用于日志筛选的低基数业务路由模板。

    管理端和静态资源路由被排除，动态路径只保留 FastAPI 的模板而非实际参数值；
    这既防止枚举业务标识，也使前端的 route 筛选拥有稳定有限的选项集合。
    """
    # route 只收集模板，不收集带 patent_id/request_id 的原始路径；管理筛选因此
    # 既可用又不会把高基数业务值变成可枚举的数据接口。
    return {
        path
        for candidate in request.app.routes
        if isinstance((path := getattr(candidate, "path", None)), str)
        and not (
            path in {"/admin", "/admin/"}
            or path.startswith(("/admin/", "/admin-api/"))
        )
    } | {UNMATCHED_ROUTE}


def _audit(
    principal: AdminPrincipal,
    *,
    action: str,
    result: str,
    returned_count: int | None = None,
    window_seconds: int | None = None,
) -> None:
    """记录一次管理面只读操作的最小审计事件。

    审计数据用于说明谁在何时读取了哪类管理信息，只携带固定动作、结果和聚合
    数量；查询文本、目标 request id、日志内容等高敏感或高基数字段不得进入此处。
    """
    # 每次只读管理读取都写固定审计事件；不写入筛选全文、目标 request_id 或错误原文。
    log_event(
        logger,
        logging.INFO,
        "admin_read_completed",
        actor=principal.subject,
        role=principal.role,
        action=action,
        result=result,
        returned_count=returned_count,
        window_seconds=window_seconds,
    )


def _no_store(response: Response) -> None:
    # 管理响应包含发布身份和运行态，禁止浏览器/代理缓存旧的权限范围或状态。
    response.headers["Cache-Control"] = "no-store"
