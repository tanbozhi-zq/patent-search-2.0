"""RequestContextMiddleware 负责三件相互关联的事：规范请求 ID、给响应补同一
个 ID、记录 HTTP 完成指标/日志。它还把 route 模板和业务错误码放进 scope.state，
让错误响应、中间件和 Prometheus 不必从原始 URL 猜测语义。
"""

from contextvars import ContextVar, Token
import logging
import re
from time import monotonic
from typing import Any
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.exceptions import ErrorCode, error_code_for_http_status
from app.core.logging import log_event, mark_exception_safely_logged
from app.core.metrics import (
    CONTROL_PLANE_ROUTES,
    call_metrics,
    should_observe_http_path,
)


REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_MAX_LENGTH = 64
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
UNMATCHED_ROUTE = "__unmatched__"
UNHANDLED_EXCEPTION_LOGGED_STATE = "unhandled_exception_logged"

_request_id_context: ContextVar[str | None] = ContextVar(
    "request_id",
    default=None,
)
logger = logging.getLogger(__name__)


def new_request_id() -> str:
    # uuid4.hex 不带连字符，天然满足当前 request-id 的 ASCII/长度约束。
    return uuid4().hex


def current_request_id() -> str | None:
    return _request_id_context.get()


def bind_request_id(request_id: str) -> Token[str | None]:
    return _request_id_context.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id_context.reset(token)


def request_id_from_scope(scope: Scope) -> str:
    """从 ASGI headers 接受一个安全的 Request ID，或生成新的关联 ID。

    只接受唯一、受限 ASCII 格式的入口值；无效值必须被替换而不是“尽量保留”，否则
    同一请求在日志、响应头和 Prometheus 关联中可能出现注入、截断或歧义。
    """
    # 只有唯一、ASCII、格式合法的入口值才会被接受；重复头、Unicode、控制字符
    # 和超长值全部替换为服务生成的 ID，避免日志关联字段被注入或歧义解析。
    values = [
        value
        for name, value in scope.get("headers", [])
        if name.lower() == b"x-request-id"
    ]
    if len(values) != 1 or len(values[0]) > REQUEST_ID_MAX_LENGTH:
        return new_request_id()
    try:
        candidate = values[0].decode("ascii")
    except UnicodeDecodeError:
        return new_request_id()
    if not is_valid_request_id(candidate):
        return new_request_id()
    return candidate


def is_valid_request_id(value: str) -> bool:
    return (
        len(value) <= REQUEST_ID_MAX_LENGTH
        and REQUEST_ID_PATTERN.fullmatch(value) is not None
    )


class RequestContextMiddleware:
    """为每个 HTTP 请求建立短生命周期的关联和观测上下文。

    中间件将 Request ID 同时写入 ASGI state、ContextVar 和响应头；还负责用低基数
    route 模板记录完成事件与指标。它不翻译业务异常，异常处理器仍拥有响应格式。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """包裹 HTTP ASGI 调用，保证关联 ID、完成日志和指标在所有退出路径收束。

        非 HTTP scope 原样透传。对 HTTP 请求，响应开始前的异常走取消/失败观测路径，
        已开始响应则以实际 status 和业务 code 记录；finally 总会恢复 ContextVar。
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # request.state 和 ContextVar 同时保存：前者供 FastAPI/错误处理器访问，
        # 后者供 Repository、MCP adapter 等非路由函数读取。
        request_id = request_id_from_scope(scope)
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        state["response_code"] = 0
        token = bind_request_id(request_id)
        started = monotonic()
        status = 500
        request_failed = False
        response_started = False
        service_metrics = _service_metrics(scope)
        observe_request = should_observe_http_path(scope.get("path", ""))
        metrics_started = False
        if observe_request:
            metrics_started = isinstance(
                call_metrics(
                    service_metrics,
                    "start_http_request",
                    method=scope.get("method", ""),
                ),
                str,
            )

        async def send_with_request_id(message: Message) -> None:
            # 只在 response.start 写头，避免后续 body chunk 重复修改 headers。
            nonlocal response_started, status
            if message["type"] == "http.response.start":
                response_started = True
                status = int(message["status"])
                headers = MutableHeaders(raw=message.setdefault("headers", []))
                headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as exc:
            # 异常可能随后还会被 Uvicorn 记录；标记一次安全日志，过滤器会抑制
            # 重复的“Exception in ASGI application”并保留统一的类型化事件。
            request_failed = True
            state["response_code"] = int(ErrorCode.INTERNAL_ERROR)
            log_unhandled_exception(state, request_id, exc)
            raise
        finally:
            # response.start 之前就失败的请求仍有指标取消路径；已开始响应的请求
            # 才记录完整 status/code/耗时，避免把未完成请求伪装成成功样本。
            elapsed_ms = round((monotonic() - started) * 1000, 3)
            code = _response_code(state, status)
            metric_code = _metric_response_code(
                state,
                route=_route_template(scope),
                fallback_code=code,
            )
            level = _completion_log_level(status)
            try:
                if observe_request and metrics_started:
                    if response_started or request_failed:
                        call_metrics(
                            service_metrics,
                            "finish_http_request",
                            method=scope.get("method", ""),
                            route=_route_template(scope),
                            status=status,
                            code=metric_code,
                            elapsed_seconds=max(0.0, elapsed_ms / 1000),
                        )
                    else:
                        call_metrics(
                            service_metrics,
                            "cancel_http_request",
                            method=scope.get("method", ""),
                        )
                log_event(
                    logger,
                    level,
                    "http_request_completed",
                    request_id=request_id,
                    route=_route_template(scope),
                    method=scope.get("method", ""),
                    status=status,
                    code=code,
                    elapsed_ms=elapsed_ms,
                )
            finally:
                reset_request_id(token)


def _service_metrics(scope: Scope) -> Any:
    # middleware 也用于最小测试 ASGI app，找不到 metrics 时应当静默降级。
    app = scope.get("app")
    state = getattr(app, "state", None)
    return getattr(state, "service_metrics", None)


def log_unhandled_exception(
    state: dict[str, Any],
    request_id: str,
    exc: Exception,
) -> None:
    """为一个请求最多记录一次未处理异常，并标记它已被安全地结构化记录。"""
    # 同一个异常可能同时经过 Exception handler 和 ASGI middleware；state 标记
    # 保证只写一次事件，避免一个请求制造两条看似独立的内部错误。
    mark_exception_safely_logged(exc)
    if state.get(UNHANDLED_EXCEPTION_LOGGED_STATE):
        return
    state[UNHANDLED_EXCEPTION_LOGGED_STATE] = True
    log_event(
        logger,
        logging.ERROR,
        "unhandled_exception",
        request_id=request_id,
        exception_type=type(exc).__name__,
    )


def _route_template(scope: Scope) -> str:
    """提取低基数路由模板，绝不把带业务参数的原始 URL 作为观测标签。"""
    # 优先使用请求体中间件提前保存的 path，其次读取 Starlette 匹配后的模板；
    # 找不到匹配路由时使用固定值，绝不把带参数的原始 URL 作为指标 label。
    state_route = scope.get("state", {}).get("route_template")
    if isinstance(state_route, str) and state_route:
        return state_route
    route = scope.get("route")
    route_path = getattr(route, "path", None)
    if not isinstance(route_path, str) or not route_path:
        return UNMATCHED_ROUTE
    return route_path


def _response_code(state: dict[str, Any], status: int) -> int:
    # 业务错误响应会预先写入明确 code；没有明确 code 时只按 HTTP 状态选择默认码。
    code = state.get("response_code")
    if isinstance(code, int) and not isinstance(code, bool) and code:
        return code
    if status < 400:
        return 0
    return int(error_code_for_http_status(status))


def _metric_response_code(
    state: dict[str, Any],
    *,
    route: str,
    fallback_code: int,
) -> int:
    # 控制面探针的 503 不代表业务过载，指标固定使用 code=0，避免告警误判 50301。
    explicit_code = state.get("response_code")
    if route in CONTROL_PLANE_ROUTES and explicit_code == 0:
        return 0
    return fallback_code


def _completion_log_level(status: int) -> int:
    # 以响应状态决定日志等级，4xx 是调用方/契约问题，5xx 才需要服务侧告警关注。
    if status >= 500:
        return logging.ERROR
    if status >= 400:
        return logging.WARNING
    return logging.INFO
