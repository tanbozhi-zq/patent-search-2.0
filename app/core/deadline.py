"""这组 ContextVar 工具把“本次 HTTP 请求最多允许运行多久”传给 Repository 和
自定义 urllib3 连接。调用链上的每一层都只能消耗剩余时间，不能重新获得一个
完整的 240 秒超时。
"""

from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic
from typing import Callable, Iterator


_request_deadline: ContextVar[float | None] = ContextVar("request_deadline", default=None)


@contextmanager
def request_deadline(
    seconds: float,
    clock: Callable[[], float] = monotonic,
) -> Iterator[None]:
    # 使用 token 恢复上层上下文，兼容同一线程/协程嵌套处理请求或测试。
    token = _request_deadline.set(clock() + seconds)
    try:
        yield
    finally:
        _request_deadline.reset(token)


def current_request_deadline() -> float | None:
    # 没有 HTTP 请求上下文时返回 None，Repository 会退回到配置的请求总预算。
    return _request_deadline.get()
