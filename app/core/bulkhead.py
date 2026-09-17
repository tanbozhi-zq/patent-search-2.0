"""ApplicationBulkhead 是进程级异步准入阀，不等同于 OpenSearch 连接池：它在
请求进入 Repository 之前快速拒绝过载，并把轻/重业务的容量边界暴露给日志和指标。
"""

import asyncio
from contextlib import asynccontextmanager
import logging
from typing import AsyncIterator

from app.core.exceptions import ErrorCode, service_error
from app.core.logging import log_event
from app.core.metrics import call_metrics
from app.core.request_context import current_request_id


logger = logging.getLogger(__name__)


class ApplicationBulkhead:
    """用短暂等待换取明确的 50301，而不是让请求无限排队。

    它是请求级异步准入阀，而非 OpenSearch 连接池：成功进入的请求才计入在途数，
    超时获取许可的请求立即得到领域错误并留下可观测的拒绝计数。
    """

    def __init__(
        self,
        capacity: int,
        acquire_timeout_seconds: float,
        name: str = "global",
        metrics=None,
    ):
        self.capacity = capacity
        self.acquire_timeout_seconds = acquire_timeout_seconds
        self.name = name
        self._metrics = metrics
        self._slots = asyncio.BoundedSemaphore(capacity)
        self._in_flight = 0
        self._peak_in_flight = 0
        self._rejected_count = 0
        call_metrics(
            self._metrics,
            "initialize_bulkhead",
            name=self.name,
            capacity=self.capacity,
        )

    @property
    def in_flight(self) -> int:
        return self._in_flight

    @property
    def rejected_count(self) -> int:
        return self._rejected_count

    @property
    def peak_in_flight(self) -> int:
        return self._peak_in_flight

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """取得一个业务准入槽位，并在正常、异常或取消退出时成对释放。

        许可等待最多持续 ``acquire_timeout_seconds``；等待超时不是后台排队信号，而是
        立即转换为 SERVICE_BUSY。metrics 和日志在获取、拒绝和释放时同步更新。
        """
        # wait_for 的上限很短，目的不是排队平滑流量，而是避免过载请求长时间
        # 占住连接、线程和客户端；拿到许可后，finally 保证异常/取消也会释放。
        try:
            await asyncio.wait_for(
                self._slots.acquire(),
                timeout=self.acquire_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            self._rejected_count += 1
            call_metrics(
                self._metrics,
                "record_bulkhead_rejection",
                name=self.name,
            )
            self._log_state(
                logging.WARNING,
                "rejected",
            )
            raise service_error(ErrorCode.SERVICE_BUSY) from exc

        # 只有成功获取许可的请求才计入在途和 peak；被拒绝的请求只增加拒绝计数。
        self._in_flight += 1
        call_metrics(
            self._metrics,
            "set_bulkhead_in_flight",
            name=self.name,
            in_flight=self._in_flight,
        )
        self._peak_in_flight = max(self._peak_in_flight, self._in_flight)
        try:
            self._log_state(logging.INFO, "in_flight")
            yield
        finally:
            self._in_flight -= 1
            call_metrics(
                self._metrics,
                "set_bulkhead_in_flight",
                name=self.name,
                in_flight=self._in_flight,
            )
            self._slots.release()
            self._log_state(logging.INFO, "in_flight")

    def _log_state(self, level: int, event: str) -> None:
        # 日志字段全部是固定的数值/枚举，不把请求内容、专利号或凭据写入观测流。
        log_event(
            logger,
            level,
            event,
            request_id=current_request_id(),
            name=self.name,
            in_flight=self._in_flight,
            peak_in_flight=self._peak_in_flight,
            rejected_total=self._rejected_count,
            capacity=self.capacity,
            acquire_timeout_seconds=self.acquire_timeout_seconds,
        )
