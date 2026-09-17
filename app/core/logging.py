"""日志模块同时维护 JSON 标准输出和一个有界的进程内副本。后者只服务本地
admin process fallback，字段、长度和事件范围都经过白名单，不能替代集中日志。
"""

from collections import deque
from datetime import datetime, timezone
import json
import logging
from threading import Lock
import math
from typing import Any


STRUCTURED_LOG_FIELDS = (
    ("event", "event"),
    ("request_id", "request_id"),
    ("route", "route"),
    ("method", "method"),
    ("status", "status"),
    ("code", "code"),
    ("elapsed_ms", "elapsed_ms"),
    ("dependency", "dependency"),
    ("operation", "operation"),
    ("outcome", "outcome"),
    ("retry_count", "retry_count"),
    ("name", "structured_name"),
    ("in_flight", "in_flight"),
    ("peak_in_flight", "peak_in_flight"),
    ("capacity", "capacity"),
    ("rejected_total", "rejected_total"),
    ("acquire_timeout_seconds", "acquire_timeout_seconds"),
    ("exception_type", "exception_type"),
    ("actor", "actor"),
    ("role", "role"),
    ("action", "action"),
    ("result", "result"),
    ("returned_count", "returned_count"),
    ("window_seconds", "window_seconds"),
    ("draft_id", "draft_id"),
    ("operation_id", "operation_id"),
    ("changed_parameter_count", "changed_parameter_count"),
    ("validation_error_count", "validation_error_count"),
)

_LOG_RECORD_FIELD_NAMES = {
    output_name: record_name
    for output_name, record_name in STRUCTURED_LOG_FIELDS
}

_SENSITIVE_DEPENDENCY_LOGGERS = (
    "httpcore",
    "httpx",
    "opensearch",
    "urllib3",
)
_SAFELY_LOGGED_EXCEPTION_ATTRIBUTE = "_patent_search_safely_logged"
STRUCTURED_LOG_BUFFER_CAPACITY = 2000


class StructuredLogBuffer:
    """仅保存白名单结构化字段的有界进程内日志镜像。

    它服务于管理面的当前进程降级视图，而不是通用日志存储：非结构化 message、管理面
    自身轮询和超长字段都不会进入缓冲，读取方获得副本后可在锁外筛选。
    """

    def __init__(self, capacity: int = STRUCTURED_LOG_BUFFER_CAPACITY) -> None:
        self.capacity = capacity
        self._records: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._sequence = 0
        self._lock = Lock()

    def append(self, record: logging.LogRecord) -> None:
        """从一条 LogRecord 提取可安全展示的字段，并按递增 sequence 追加到环形缓冲。"""
        # 只收结构化事件，不把任意 logger 的 message 当作管理查询数据；这样
        # unstructured 日志不会污染关联结果，也不会把敏感文本存进进程缓冲区。
        event = getattr(record, "event", None)
        if not isinstance(event, str) or not event or len(event) > 128:
            return
        route = getattr(record, "route", None)
        if event.startswith("admin_") or (
            isinstance(route, str)
            and (
                route in {"/admin", "/admin/"}
                or route.startswith(("/admin/", "/admin-api/"))
            )
        ):
            # Admin 轮询和拒绝事件仍会进入 journal 审计流，但不能挤出这个有意
            # 设得很小的业务关联缓冲区。
            return
        payload: dict[str, Any] = {
            "sequence": 0,
            "timestamp_seconds": float(record.created),
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=timezone.utc,
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }
        for output_name, record_name in STRUCTURED_LOG_FIELDS:
            value = getattr(record, record_name, None)
            if isinstance(value, str):
                if len(value) <= 256:
                    payload[output_name] = value
            elif isinstance(value, bool):
                payload[output_name] = value
            elif isinstance(value, int):
                payload[output_name] = value
            elif isinstance(value, float) and math.isfinite(value):
                payload[output_name] = value
        with self._lock:
            self._sequence += 1
            payload["sequence"] = self._sequence
            self._records.append(payload)

    def snapshot(self) -> tuple[dict[str, Any], ...]:
        """返回记录副本，允许调用方在不持锁的情况下执行筛选、倒序和分页。"""
        # 返回副本而不是内部 deque，读取方可以在锁外筛选和分页。
        with self._lock:
            return tuple(dict(record) for record in self._records)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._sequence = 0


class StructuredLogBufferHandler(logging.Handler):
    """将标准 logging 事件镜像到 StructuredLogBuffer，且绝不让观测写入反噬业务。"""

    def __init__(self, buffer: StructuredLogBuffer) -> None:
        super().__init__()
        self._buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        # 观测故障不能反过来影响业务 logger；任何异常都在这里吞掉。
        try:
            self._buffer.append(record)
        except Exception:
            # Observability must never fail or reveal a business request.
            return


_structured_log_buffer = StructuredLogBuffer()


class SuppressSafelyLoggedUvicornExceptionFilter(logging.Filter):
    """仅过滤已经由请求上下文安全记录过的 Uvicorn 重复 ASGI 异常。"""

    # 只抑制已经由应用安全记录过的 ASGI 异常，普通 Uvicorn 错误仍保留。
    def filter(self, record: logging.LogRecord) -> bool:
        """仅移除带安全记录标记的那一种 Uvicorn 重复异常日志。

        过滤条件同时约束 logger 名称、固定消息和异常对象属性，避免把其他 Uvicorn
        故障误当作重复日志吞掉。返回 ``True`` 的任何记录仍按原有 handler 链输出。
        """
        if record.name != "uvicorn.error":
            return True
        if record.getMessage().strip() != "Exception in ASGI application":
            return True
        exc_info = record.exc_info
        if not isinstance(exc_info, tuple) or len(exc_info) < 2:
            return True
        return not bool(
            getattr(exc_info[1], _SAFELY_LOGGED_EXCEPTION_ATTRIBUTE, False)
        )


class JsonLogFormatter(logging.Formatter):
    """把 allowlisted 结构化字段格式化为 JSON，并让普通日志显式标为非结构化事件。"""

    # 输出字段与 admin/journal 解析白名单同源，未结构化日志退化成 event=unstructured_log。
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created,
                tz=timezone.utc,
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "logger": record.name,
        }
        for output_name, record_name in STRUCTURED_LOG_FIELDS:
            value = getattr(record, record_name, None)
            if value is not None:
                payload[output_name] = value
        if "event" not in payload:
            payload["event"] = "unstructured_log"
            payload["message"] = record.getMessage()
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )


def configure_logging() -> StructuredLogBuffer:
    """安装进程级 JSON 日志、内存镜像与 Uvicorn 去重过滤器，并返回共享缓冲区。"""
    # force=True 清理已有 handler，避免 reload/test 重复安装导致一条事件被重复输出。
    formatter = JsonLogFormatter()
    exception_filter = SuppressSafelyLoggedUvicornExceptionFilter()
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.addFilter(exception_filter)
    buffer_handler = StructuredLogBufferHandler(_structured_log_buffer)
    buffer_handler.addFilter(exception_filter)
    logging.basicConfig(
        level=logging.INFO,
        handlers=[handler, buffer_handler],
        force=True,
    )
    _configure_uvicorn_logging(formatter, exception_filter)
    for logger_name in _SENSITIVE_DEPENDENCY_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.CRITICAL)
    return _structured_log_buffer


def mark_exception_safely_logged(exc: Exception) -> None:
    # 某些第三方异常对象不允许 setattr，因此标记失败必须静默，不影响原异常。
    try:
        setattr(exc, _SAFELY_LOGGED_EXCEPTION_ATTRIBUTE, True)
    except (AttributeError, TypeError):
        pass


def _configure_uvicorn_logging(
    formatter: JsonLogFormatter,
    exception_filter: SuppressSafelyLoggedUvicornExceptionFilter,
) -> None:
    # Uvicorn 自带 handler 也要使用相同 JSON 格式；access log 关闭后由请求上下文
    # 统一记录 route/status/code，避免两条口径互相重复。
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.asgi"):
        for handler in logging.getLogger(logger_name).handlers:
            handler.setFormatter(formatter)
            if not any(
                isinstance(existing, SuppressSafelyLoggedUvicornExceptionFilter)
                for existing in handler.filters
            ):
                handler.addFilter(exception_filter)
    logging.getLogger("uvicorn.access").disabled = True


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: Any,
) -> None:
    """记录一条只包含允许字段的结构化业务事件。

    ``fields`` 中未列入白名单的值不会进入 JSON 结构字段；调用方仍应避免把请求正文、
    凭据或下游异常原文拼到 event/message 中，因为日志模块不会替业务层猜测敏感性。
    """
    # extra 只传固定字段供 buffer/formatter 使用，message 仍保持可读，且不主动
    # 拼入请求正文、专利号、Token 或异常原文。
    structured_fields = {"event": event, **fields}
    record_fields = {
        _LOG_RECORD_FIELD_NAMES[name]: value
        for name, value in structured_fields.items()
        if name in _LOG_RECORD_FIELD_NAMES
    }
    message = " ".join(
        f"{name}={value}"
        for name, value in structured_fields.items()
        if value is not None
    )
    logger.log(level, message, extra=record_fields)
