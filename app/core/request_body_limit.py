"""这个 ASGI 中间件专门守住三个会解析查询式的 POST 路由。它既检查 Content-Length，
也检查分块读取累计字节数，因为没有 Content-Length 的请求不能绕过大小预算。
"""

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.admin_config import ADMIN_CONFIG_DRAFT_BODY_MAX_BYTES
from app.core.error_handlers import known_error_response
from app.core.exceptions import ErrorCode
from app.query.budget import QueryBudgetProvider


QUERY_BODY_PATHS = frozenset(
    {
        "/api/patent/search",
        "/console-api/search",
        "/console-api/test/target-rank",
    }
)
FIXED_BODY_LIMITS = {
    "/admin-api/v1/config-drafts": ADMIN_CONFIG_DRAFT_BODY_MAX_BYTES,
    "/admin-api/v1/runtime-config/apply": ADMIN_CONFIG_DRAFT_BODY_MAX_BYTES,
    "/admin-api/v1/runtime-config/rollback": ADMIN_CONFIG_DRAFT_BODY_MAX_BYTES,
}


class QueryRequestBodyLimitMiddleware:
    """在请求进入 JSON/Pydantic 解析前执行查询请求体硬限制。

    它只拦截会解析查询式的 POST 路由，并在 scope 中固定一份预算快照。既利用可信
    Content-Length 提前拒绝，也包裹流式 receive 累计真实字节，避免 chunked 请求绕过。
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        budget_provider: QueryBudgetProvider,
        paths: frozenset[str] = QUERY_BODY_PATHS,
        fixed_limits: dict[str, int] = FIXED_BODY_LIMITS,
    ) -> None:
        self.app = app
        self.budget_provider = budget_provider
        self.paths = paths
        self.fixed_limits = dict(fixed_limits)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """在受控 POST 路由上确定当前 body 限制并完整缓冲请求体。

        查询路由会在 scope 中固定同一份查询预算；管理配置路由使用独立固定上限。
        任何路径不受本中间件约束时直接透传。先消费并验证整个流再交给下游，避免
        chunked 请求绕过限制，也让拒绝路径无需启动 Pydantic/业务解析。
        """
        limit = self._body_limit(scope)
        if limit is None:
            await self.app(scope, receive, send)
            return

        content_length = _content_length(scope)
        if content_length is not None and content_length > limit:
            await _send_request_too_large(scope, receive, send)
            return

        buffered_messages: list[Message] = []
        received_bytes = 0
        # 逐块累计真实 body 字节；超过请求特定上限时直接生成统一 41301，避免
        # JSON 解析器先消费一大段无用内容。已验证消息随后由 buffered_receive 重放。
        while True:
            message = await receive()
            buffered_messages.append(message)
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > limit:
                    await _send_request_too_large(scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                break

        message_index = 0

        async def buffered_receive() -> Message:
            nonlocal message_index
            if message_index < len(buffered_messages):
                message = buffered_messages[message_index]
                message_index += 1
                return message
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, buffered_receive, send)

    def _body_limit(self, scope: Scope) -> int | None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            return None
        path = scope.get("path")
        if path in self.paths:
            budget = self.budget_provider.snapshot()
            state = scope.setdefault("state", {})
            state["query_budget_snapshot"] = budget
            state["route_template"] = path
            return budget.max_request_body_bytes
        return self.fixed_limits.get(path)


def _content_length(scope: Scope) -> int | None:
    """读取唯一可信的非负 Content-Length；缺失或格式错误时返回 ``None`` 交给流式限制。"""
    # 无效/负数 Content-Length 不在这里直接判定为超限，交由 ASGI/框架处理；
    # 对可信的非负值则可在读取 body 前快速失败。
    for name, value in scope.get("headers", []):
        if name.lower() != b"content-length":
            continue
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


async def _send_request_too_large(
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    """在 body 尚未进入应用时发送与常规异常处理一致的 41301 错误信封。"""
    # Content-Length 已明确超限时还没有进入应用路由，因此手工构造 Request
    # 只用于让 known_error_response 生成同样的 request_id 和错误信封。
    response = known_error_response(
        Request(scope, receive=receive),
        ErrorCode.REQUEST_TOO_LARGE,
    )
    await response(scope, receive, send)
