"""日志读取是一个有界、只读的适配层：process 模式读进程缓冲，journal 模式读专用
namespace。两种模式都限制时间窗口、扫描条数、消息大小、并发和游标范围，不能
被看板用来变成任意日志搜索或跨实例查询。
"""

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from hashlib import sha256
import importlib
import json
import math
from time import monotonic, time
from typing import Any, Callable, Protocol

from app.core.exceptions import ERROR_REGISTRY
from app.core.logging import STRUCTURED_LOG_FIELDS, StructuredLogBuffer
from app.core.metrics import (
    ADMIN_PATHS,
    CONTROL_PLANE_ROUTES,
    HTTP_METHODS,
    METRICS_PATHS,
)
from app.core.request_context import is_valid_request_id
from app.schemas.admin import AdminLogEvent, AdminLogsResponse


ADMIN_LOG_MAX_WINDOW_SECONDS = 86_400
ADMIN_LOG_MAX_PAGE_SIZE = 100
ADMIN_LOG_MAX_CURSOR_LENGTH = 2048
ADMIN_LOG_MAX_JOURNAL_SCAN = 5_000
ADMIN_LOG_MAX_MESSAGE_BYTES = 16_384
ADMIN_LOG_JOURNAL_WALL_CLOCK_SECONDS = 0.25
ADMIN_LOG_JOURNAL_NAMESPACE = "patent-search"

_JOURNAL_TARGETS = (
    ("patent-search-service.service", "patent-search-service"),
    ("patent-mcp.service", "patent-mcp"),
)
_LOG_CODES = frozenset({0, *(int(code) for code in ERROR_REGISTRY)})
_KNOWN_EVENTS = frozenset(
    {
        "admin_config_completed",
        "admin_config_store_failed",
        "admin_runtime_config_completed",
        "admin_runtime_config_store_failed",
        "dependency_call_completed",
        "dependency_retry",
        "http_request_completed",
        "in_flight",
        "mcp_tool_completed",
        "readiness_check_completed",
        "rejected",
        "unhandled_exception",
    }
)
_OUTPUT_FIELDS = tuple(output_name for output_name, _ in STRUCTURED_LOG_FIELDS)


@dataclass(frozen=True)
class AdminLogFilters:
    """管理日志允许使用的窄筛选集合；它们也是 cursor 指纹的一部分。"""

    # 过滤条件和 include_system 都会绑定到 cursor，避免把一组筛选的分页位置复用于另一组查询。
    request_id: str | None = None
    route: str | None = None
    code: int | None = None
    include_system: bool = True


class AdminLogReader(Protocol):
    """进程缓冲、systemd journal 与不可用降级实现共享的最小只读接口。"""

    # 路由依赖这个最小协议，因此可以在没有 systemd-python 或 journal 权限的机器上降级。
    scope: str

    async def read(
        self,
        *,
        window_seconds: int,
        limit: int,
        cursor: str | None,
        filters: AdminLogFilters,
    ) -> AdminLogsResponse: ...

    async def close(self) -> None: ...


class ProcessAdminLogReader:
    """基于现有结构化日志缓冲的有界当前进程视图。

    它适合本地或未配置 journal 的降级场景，只看当前 worker 的 allowlisted 事件；跨
    进程历史和任意 message 检索都不属于这个 reader 的能力范围。
    """

    scope = "current_process"

    def __init__(self, buffer: StructuredLogBuffer) -> None:
        self._buffer = buffer

    async def read(
        self,
        *,
        window_seconds: int,
        limit: int,
        cursor: str | None,
        filters: AdminLogFilters,
    ) -> AdminLogsResponse:
        """按倒序 sequence 返回一页已筛选记录，并为下一页生成绑定条件的游标。"""
        # 进程缓冲按 sequence 倒序扫描；游标指向上一页最后一条 sequence，避免
        # 时间戳相同或新日志追加时产生重复页。
        before_sequence = (
            _decode_process_cursor(cursor, window_seconds, filters) if cursor else None
        )
        cutoff = time() - window_seconds
        records = self._buffer.snapshot()
        items: list[AdminLogEvent] = []
        last_returned_sequence: int | None = None
        scanned_count = 0
        more_matches = False

        for record in reversed(records):
            sequence = record.get("sequence")
            timestamp_seconds = record.get("timestamp_seconds")
            if not isinstance(sequence, int) or not isinstance(
                timestamp_seconds, (int, float)
            ):
                continue
            if before_sequence is not None and sequence >= before_sequence:
                continue
            if timestamp_seconds < cutoff:
                break
            scanned_count += 1
            if not _mapping_matches(record, filters):
                continue
            if len(items) >= limit:
                more_matches = True
                break
            items.append(AdminLogEvent.model_validate(record))
            last_returned_sequence = sequence

        next_cursor = None
        if more_matches and last_returned_sequence is not None:
            next_cursor = _encode_bound_cursor(
                "p2",
                str(last_returned_sequence),
                window_seconds,
                filters,
            )

        return AdminLogsResponse(
            scope=self.scope,
            available=True,
            window_seconds=window_seconds,
            scanned_count=scanned_count,
            truncated=more_matches,
            next_cursor=next_cursor,
            items=items,
        )

    async def close(self) -> None:
        return None


class UnavailableAdminLogReader:
    """显式表示当前部署没有可安全读取的管理日志源。"""

    scope = "unavailable"

    async def read(
        self,
        *,
        window_seconds: int,
        limit: int,
        cursor: str | None,
        filters: AdminLogFilters,
    ) -> AdminLogsResponse:
        """忽略查询参数并返回 ``available=false``，而不是伪造一页空日志。"""
        # 不可用是显式能力状态，不伪造空的 available=true，以免看板把“无权限”当成“无事件”。
        del limit, cursor, filters
        return _unavailable_logs(window_seconds)

    async def close(self) -> None:
        return None


class SystemdJournalAdminLogReader:
    """基于 python-systemd native API 的有界本机 journal reader。

    journal 调用不可安全强杀，因此实例内只允许一个专用 worker 正在读取；请求超时后
    返回 unavailable 但保留 busy，直到 native 调用真正结束，防止后台并发累积。
    """

    scope = "journal_local"

    def __init__(
        self,
        *,
        reader_factory: Callable[..., Any],
        allowed_routes: frozenset[str],
        wall_clock_seconds: float = ADMIN_LOG_JOURNAL_WALL_CLOCK_SECONDS,
    ) -> None:
        self._reader_factory = reader_factory
        self._allowed_routes = allowed_routes
        self._wall_clock_seconds = wall_clock_seconds
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="admin-journal",
        )
        self._state_lock = asyncio.Lock()
        self._busy = False
        self._closed = False
        self._active: asyncio.Future[AdminLogsResponse] | None = None

    async def read(
        self,
        *,
        window_seconds: int,
        limit: int,
        cursor: str | None,
        filters: AdminLogFilters,
    ) -> AdminLogsResponse:
        """把一次 journal 分页读取委派给唯一 worker，并将忙碌或超时降级为不可用。"""
        # systemd journal API 是同步 native 调用；单专用线程、busy 标志和短 wall-clock
        # 将其隔离于事件循环。超时后保持 busy，直到 native 调用真正返回，避免并发重入。
        journal_cursor = (
            _decode_journal_cursor(cursor, window_seconds, filters)
            if cursor
            else None
        )
        async with self._state_lock:
            if self._closed or self._busy:
                return _unavailable_logs(window_seconds)
            self._busy = True
            loop = asyncio.get_running_loop()
            future = loop.run_in_executor(
                self._executor,
                partial(
                    self._read_sync,
                    window_seconds=window_seconds,
                    limit=limit,
                    journal_cursor=journal_cursor,
                    filters=filters,
                ),
            )
            self._active = future
            future.add_done_callback(self._schedule_release)
        try:
            response = await asyncio.wait_for(
                asyncio.shield(future),
                timeout=self._wall_clock_seconds + 0.25,
            )
        except TimeoutError:
            # native 调用不能安全强杀；在其线程内真正返回前保持 busy，避免第二个
            # 查询与可能仍在读 journal 的第一个查询并发。
            return _unavailable_logs(window_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            response = _unavailable_logs(window_seconds)
        await self._release(future)
        return response

    def _schedule_release(self, future: asyncio.Future[AdminLogsResponse]) -> None:
        asyncio.create_task(self._release(future))

    async def _release(self, future: asyncio.Future[AdminLogsResponse]) -> None:
        async with self._state_lock:
            if self._active is future:
                self._active = None
                self._busy = False

    async def close(self) -> None:
        """禁止后续读取，并在有界等待后放弃等待仍在 native API 中的 worker。"""
        # 关闭时最多等待当前 native 读取一个有界窗口，随后不等待 executor，避免
        # 服务退出被 journal 系统调用永久拖住。
        async with self._state_lock:
            self._closed = True
            active = self._active
        if active is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(active),
                    timeout=self._wall_clock_seconds + 0.25,
                )
            except (TimeoutError, asyncio.CancelledError):
                pass
            except Exception:
                pass
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _read_sync(
        self,
        *,
        window_seconds: int,
        limit: int,
        journal_cursor: str | None,
        filters: AdminLogFilters,
    ) -> AdminLogsResponse:
        """在专用线程中倒序扫描受限 journal namespace，并返回一页安全事件。

        扫描同时受 wall-clock、最大原始 entry 数、时间窗口和返回条数限制；cursor 只在
        结果被截断时产生，并会在外层与窗口和筛选条件绑定。
        """
        # 同步读取固定 namespace 和两个 unit/identifier 组合，按时间倒序最多扫描
        # 5000 条，并在达到 limit/截止时间时返回 cursor 让前端继续下一页。
        deadline = monotonic() + self._wall_clock_seconds
        cutoff = datetime.fromtimestamp(
            time() - window_seconds,
            tz=timezone.utc,
        )
        reader = self._reader_factory(namespace=ADMIN_LOG_JOURNAL_NAMESPACE)
        try:
            reader.data_threshold = ADMIN_LOG_MAX_MESSAGE_BYTES + 1
            _add_journal_matches(reader)
            if journal_cursor is None:
                reader.seek_tail()
            else:
                reader.seek_cursor(journal_cursor)

            items: list[AdminLogEvent] = []
            scanned_count = 0
            last_cursor: str | None = None
            truncated = False
            while scanned_count < ADMIN_LOG_MAX_JOURNAL_SCAN:
                if monotonic() >= deadline:
                    truncated = True
                    break
                entry = reader.get_previous()
                if not entry:
                    break
                scanned_count += 1
                raw_cursor = entry.get("__CURSOR")
                if isinstance(raw_cursor, str) and raw_cursor:
                    last_cursor = raw_cursor
                entry_time = _journal_timestamp(entry)
                if entry_time is None:
                    continue
                if entry_time < cutoff:
                    break
                event = _journal_event(
                    entry,
                    timestamp=entry_time,
                    allowed_routes=self._allowed_routes,
                )
                if event is None or not _event_matches(event, filters):
                    continue
                items.append(event)
                if len(items) >= limit:
                    truncated = True
                    break
            else:
                truncated = True

            next_cursor = (
                _encode_bound_cursor(
                    "j2",
                    last_cursor,
                    window_seconds,
                    filters,
                )
                if truncated and last_cursor is not None
                else None
            )
            return AdminLogsResponse(
                scope=self.scope,
                available=True,
                window_seconds=window_seconds,
                scanned_count=scanned_count,
                truncated=truncated,
                next_cursor=next_cursor,
                items=items,
            )
        finally:
            reader.close()


def create_systemd_journal_reader(
    *,
    allowed_routes: frozenset[str],
) -> AdminLogReader:
    """探测可选 systemd 依赖与 namespace 权限，失败时返回安全的不可用 reader。"""
    # 启动时先探测可选依赖和 namespace 权限；任何失败都降级为 unavailable，不能
    # 让管理日志能力阻断 FastAPI 主服务启动。
    try:
        journal = importlib.import_module("systemd.journal")
        probe = journal.Reader(namespace=ADMIN_LOG_JOURNAL_NAMESPACE)
        try:
            probe.data_threshold = ADMIN_LOG_MAX_MESSAGE_BYTES + 1
        finally:
            probe.close()
    except Exception:
        return UnavailableAdminLogReader()
    return SystemdJournalAdminLogReader(
        reader_factory=journal.Reader,
        allowed_routes=allowed_routes,
    )


def _add_journal_matches(reader: Any) -> None:
    # unit 和 SYSLOG_IDENTIFIER 双重白名单，减少同一 namespace 中无关事件进入解析器。
    for index, (unit, identifier) in enumerate(_JOURNAL_TARGETS):
        if index:
            reader.add_disjunction()
        reader.add_match(
            _SYSTEMD_UNIT=unit,
            SYSLOG_IDENTIFIER=identifier,
        )


def _journal_timestamp(entry: dict[str, Any]) -> datetime | None:
    # systemd 的 realtime timestamp 可能没有 tzinfo，统一解释为 UTC 后再做窗口比较。
    value = entry.get("__REALTIME_TIMESTAMP")
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _journal_event(
    entry: dict[str, Any],
    *,
    timestamp: datetime,
    allowed_routes: frozenset[str],
) -> AdminLogEvent | None:
    """将一条原始 journal entry 验证、收缩并映射为允许展示的结构化管理事件。

    任一字节、编码、JSON、事件名、request ID、route、code、method 或 Pydantic 校验失败
    都只丢弃当前 entry，不让日志脏数据中断同一页剩余的有效事件。
    """
    # 先限制 MESSAGE 原始字节，再解析 JSON、事件名、route/code/method 白名单，最后
    # 用 Pydantic 做数值范围校验；任何一个边界失败都丢弃该 entry，不影响其他日志。
    message = entry.get("MESSAGE")
    if isinstance(message, bytes):
        if len(message) > ADMIN_LOG_MAX_MESSAGE_BYTES:
            return None
        try:
            message = message.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(message, str):
        return None
    try:
        if len(message.encode("utf-8")) > ADMIN_LOG_MAX_MESSAGE_BYTES:
            return None
    except UnicodeEncodeError:
        return None
    try:
        payload = json.loads(message)
    except (ValueError, UnicodeError, RecursionError):
        return None
    if not isinstance(payload, dict) or payload.get("event") not in _KNOWN_EVENTS:
        return None

    sanitized = _sanitize_event(payload)
    request_id = sanitized.get("request_id")
    route = sanitized.get("route")
    code = sanitized.get("code")
    method = sanitized.get("method")
    if request_id is not None and (
        not isinstance(request_id, str) or not is_valid_request_id(request_id)
    ):
        return None
    if route is not None and route not in allowed_routes:
        return None
    if code is not None and code not in _LOG_CODES:
        return None
    if method is not None and method not in HTTP_METHODS:
        return None
    sanitized["timestamp"] = timestamp
    try:
        return AdminLogEvent.model_validate(sanitized)
    except Exception:
        return None


def _sanitize_event(payload: dict[str, Any]) -> dict[str, Any]:
    """复制日志白名单字段并拒绝超长文本、未知类型和非有限浮点值。"""
    # 只复制 STRUCTURED_LOG_FIELDS 中的允许字段，且限制字符串长度和浮点有限性。
    sanitized: dict[str, Any] = {}
    for name in _OUTPUT_FIELDS:
        value = payload.get(name)
        if isinstance(value, str):
            if len(value) <= 256:
                sanitized[name] = value
        elif isinstance(value, bool):
            sanitized[name] = value
        elif isinstance(value, int):
            sanitized[name] = value
        elif isinstance(value, float) and math.isfinite(value):
            sanitized[name] = value
    return sanitized


def _mapping_matches(record: dict[str, Any], filters: AdminLogFilters) -> bool:
    """在进程缓冲字典上执行与 journal 事件完全相同的可见性和字段筛选。"""
    # 进程缓冲和 journal 使用两套数据对象，但 include_system/request/route/code 的筛选语义保持一致。
    if not filters.include_system and _is_system_activity(
        event=record.get("event"),
        route=record.get("route"),
        operation=record.get("operation"),
    ):
        return False
    if filters.request_id is not None and record.get("request_id") != filters.request_id:
        return False
    if filters.route is not None and record.get("route") != filters.route:
        return False
    if filters.code is not None and record.get("code") != filters.code:
        return False
    return True


def _event_matches(event: AdminLogEvent, filters: AdminLogFilters) -> bool:
    """在已模型化的 journal 事件上执行管理日志筛选，而不重复解析原始字段。"""
    # journal 已经完成模型校验，这里复用同一组筛选条件，不把可见性逻辑分叉。
    if not filters.include_system and _is_system_activity(
        event=event.event,
        route=event.route,
        operation=event.operation,
    ):
        return False
    if filters.request_id is not None and event.request_id != filters.request_id:
        return False
    if filters.route is not None and event.route != filters.route:
        return False
    if filters.code is not None and event.code != filters.code:
        return False
    return True


_SYSTEM_ACTIVITY_ROUTES = CONTROL_PLANE_ROUTES | METRICS_PATHS | ADMIN_PATHS
_SYSTEM_ACTIVITY_EVENTS = frozenset(
    {
        "admin_config_completed",
        "admin_config_store_failed",
        "admin_read_completed",
        "admin_runtime_config_completed",
        "admin_runtime_config_store_failed",
        "readiness_check_completed",
    }
)


def _is_system_activity(
    *,
    event: Any,
    route: Any,
    operation: Any,
) -> bool:
    return bool(
        route in _SYSTEM_ACTIVITY_ROUTES
        or event in _SYSTEM_ACTIVITY_EVENTS
        or operation == "readiness"
    )


def _encode_cursor(version: str, value: str) -> str:
    # cursor 是不透明的 URL-safe base64，不让前端依赖 sequence/journal cursor 的内部格式。
    raw = f"{version}:{value}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, expected_version: str) -> str:
    """验证并解开当前日志 reader 版本生成的不透明分页游标。

    游标必须是长度受限的 URL-safe base64，且解码内容的版本与调用 reader 完全
    相同；旧格式、跨 reader 游标、空位置和畸形编码一律以 ``ValueError`` 拒绝。
    调用方再把该异常统一映射为客户端可理解的无效请求。
    """
    # 解码同时限制长度、base64 合法性和版本号，旧格式或跨 reader 游标都拒绝。
    if len(cursor) > ADMIN_LOG_MAX_CURSOR_LENGTH:
        raise ValueError("invalid log cursor")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(
            padded,
            altchars=b"-_",
            validate=True,
        ).decode("utf-8")
        version, value = decoded.split(":", 1)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("invalid log cursor") from exc
    if version != expected_version or not value:
        raise ValueError("invalid log cursor")
    return value


def _cursor_filter_fingerprint(
    window_seconds: int,
    filters: AdminLogFilters,
) -> str:
    # 将窗口、include_system 和筛选条件哈希进游标，防止分页位置被用于另一组查询。
    payload = json.dumps(
        {
            "window_seconds": window_seconds,
            "request_id": filters.request_id,
            "route": filters.route,
            "code": filters.code,
            "include_system": filters.include_system,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()[:32]


def _encode_bound_cursor(
    version: str,
    value: str,
    window_seconds: int,
    filters: AdminLogFilters,
) -> str:
    # 绑定 fingerprint 后再编码；调用方只需保存 opaque cursor。
    fingerprint = _cursor_filter_fingerprint(window_seconds, filters)
    return _encode_cursor(version, f"{fingerprint}:{value}")


def _decode_bound_cursor(
    cursor: str,
    version: str,
    window_seconds: int,
    filters: AdminLogFilters,
) -> str:
    """解码并验证游标版本、窗口及筛选指纹，再返回底层 sequence 或 journal cursor。"""
    # 先验证 fingerprint 再返回底层 sequence/journal cursor，避免越权扩大扫描范围。
    payload = _decode_cursor(cursor, version)
    try:
        fingerprint, value = payload.split(":", 1)
    except ValueError as exc:
        raise ValueError("invalid log cursor") from exc
    if (
        fingerprint != _cursor_filter_fingerprint(window_seconds, filters)
        or not value
    ):
        raise ValueError("invalid log cursor")
    return value


def _decode_process_cursor(
    cursor: str,
    window_seconds: int,
    filters: AdminLogFilters,
) -> int:
    raw_value = _decode_bound_cursor(
        cursor,
        "p2",
        window_seconds,
        filters,
    )
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError("invalid log cursor") from exc
    if value < 1:
        raise ValueError("invalid log cursor")
    return value


def _decode_journal_cursor(
    cursor: str,
    window_seconds: int,
    filters: AdminLogFilters,
) -> str:
    return _decode_bound_cursor(
        cursor,
        "j2",
        window_seconds,
        filters,
    )


def _unavailable_logs(window_seconds: int) -> AdminLogsResponse:
    # 统一的不可用响应保留窗口信息，但不返回异常原文或内部文件/namespace 细节。
    return AdminLogsResponse(
        scope="unavailable",
        available=False,
        window_seconds=window_seconds,
        scanned_count=0,
        truncated=False,
        items=[],
    )
