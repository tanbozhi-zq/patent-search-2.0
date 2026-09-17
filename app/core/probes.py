"""探针资源与业务资源隔离：readiness 使用自己的线程和一个并发槽，避免慢的
OpenSearch SDK 调用堵住 FastAPI 的共享线程池或在超时后继续无限堆积。
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
import logging
from threading import BoundedSemaphore
from time import monotonic
from typing import Callable

from app.core.logging import log_event
from app.core.metrics import call_metrics


logger = logging.getLogger(__name__)


class ServiceLifecycleState(str, Enum):
    """单个服务进程在生命周期探针中可公开的粗粒度状态。"""

    # startup 状态只描述本进程生命周期，不代表 OpenSearch ready。
    STARTING = "starting"
    STARTED = "started"
    STOPPING = "stopping"
    FAILED = "failed"


class ServiceLifecycle:
    """保存 startup/readiness 探针需要的最小进程生命周期状态。

    该状态只回答“本 worker 是否完整启动且尚未开始关闭”，不代表 OpenSearch 是否可用；
    依赖可用性由 ``ReadinessProbe`` 的独立检查提供，二者不能混为同一个布尔值。
    """

    def __init__(self, metrics=None) -> None:
        self._metrics = metrics
        self.state = ServiceLifecycleState.STARTING
        call_metrics(
            self._metrics,
            "set_probe_status",
            probe="startup",
            available=False,
        )

    @property
    def is_started(self) -> bool:
        return self.state is ServiceLifecycleState.STARTED

    def mark_started(self) -> None:
        # 只有 lifespan 完成所有初始化后才切换到 started，避免监听器先接流量。
        self.state = ServiceLifecycleState.STARTED
        call_metrics(
            self._metrics,
            "set_probe_status",
            probe="startup",
            available=True,
        )

    def mark_stopping(self) -> None:
        # 关闭一开始就让 startup/ready 失效，防止新流量在资源释放过程中进入。
        self.state = ServiceLifecycleState.STOPPING
        call_metrics(
            self._metrics,
            "set_probe_status",
            probe="startup",
            available=False,
        )

    def mark_failed(self) -> None:
        self.state = ServiceLifecycleState.FAILED
        call_metrics(
            self._metrics,
            "set_probe_status",
            probe="startup",
            available=False,
        )


class ReadinessProbe:
    """合并并短暂缓存每个进程的一次隔离依赖检查。

    同一时刻的多个 ``/ready`` 调用共享一个 refresh task，实际 SDK 调用放到专用单线程；
    超时或调用方取消不会无限堆积后台检查，也不会把异常原文暴露给探针响应。
    """

    def __init__(
        self,
        *,
        check: Callable[[], bool],
        timeout_seconds: float,
        success_cache_seconds: float,
        failure_cache_seconds: float,
        clock: Callable[[], float] = monotonic,
        metrics=None,
    ) -> None:
        """配置一次可共享的依赖检查及其成功/失败缓存策略。

        ``check`` 是可能阻塞的同步依赖探测，会被放入本 probe 专有的单线程执行器；
        ``clock`` 可替换以便测试缓存边界。实例拥有执行器生命周期，调用方须在应用
        关闭时调用 ``close``，防止超时后的后台线程继续被新检查堆积。
        """
        self._check = check
        self._timeout_seconds = timeout_seconds
        self._success_cache_seconds = success_cache_seconds
        self._failure_cache_seconds = failure_cache_seconds
        self._clock = clock
        self._metrics = metrics
        self._lock = asyncio.Lock()
        self._inflight: asyncio.Task[bool] | None = None
        self._cached_result: bool | None = None
        self._cache_expires_at = 0.0
        self._closed = False
        # 探针不能等待 FastAPI 共享线程池中的业务工作；专用单线程既隔离慢调用，
        # 也把同时发给 OpenSearch 的 readiness 请求限制为一个。
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="opensearch-readiness",
        )
        self._execution_slot = BoundedSemaphore(1)

    async def is_ready(self, *, force_refresh: bool = False) -> bool:
        """返回当前依赖可用性，默认复用缓存，必要时可强制刷新。

        ``force_refresh`` 只跳过有效缓存，不会重复创建并发检查；同一时刻的调用仍
        共享已有 refresh task。调用方取消只会取消自己的等待，不会取消其他探针正在
        使用的底层检查。
        """
        async with self._lock:
            if not force_refresh:
                cached = self._valid_cached_result()
                if cached is not None:
                    return cached
            if self._closed:
                return False
            if self._inflight is None:
                self._inflight = asyncio.create_task(self._refresh())
            inflight = self._inflight

        return await asyncio.shield(inflight)

    async def close(self) -> None:
        """禁止新的 refresh、清空缓存并非阻塞地关闭专用执行器。"""
        # 关闭时清空缓存并禁止新的刷新；线程池以不等待方式释放，避免 shutdown
        # 被一个已经超时但 SDK 仍未返回的调用拖住。
        async with self._lock:
            self._closed = True
            self._cached_result = None
            self._cache_expires_at = 0.0
        call_metrics(
            self._metrics,
            "set_probe_status",
            probe="ready",
            available=False,
        )
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _valid_cached_result(self) -> bool | None:
        if self._cached_result is None:
            return None
        if self._clock() >= self._cache_expires_at:
            return None
        return self._cached_result

    async def _refresh(self) -> bool:
        """执行一次有超时的依赖检查，更新缓存并记录统一日志与指标。

        所有失败形态向调用方都降级为 ``False``；区别只保留在 outcome/exception_type
        观测字段中，保证 ``/ready`` 不泄露 OpenSearch 或网络实现细节。
        """
        # 这里同时实现严格超时、短缓存、统一日志和 Prometheus outcome。探针失败
        # 只返回 false，不把下游异常细节带到 /ready 响应。
        started = self._clock()
        outcome = "unavailable"
        exception_type: str | None = None
        try:
            loop = asyncio.get_running_loop()
            ready = await asyncio.wait_for(
                loop.run_in_executor(self._executor, self._check_once),
                timeout=self._timeout_seconds,
            )
            outcome = "success" if ready else "unavailable"
        except TimeoutError:
            ready = False
            outcome = "timeout"
            exception_type = "TimeoutError"
        except Exception as exc:
            ready = False
            exception_type = type(exc).__name__
        finally:
            elapsed_ms = round((self._clock() - started) * 1000, 3)

        async with self._lock:
            if not self._closed:
                self._cached_result = ready
                ttl = (
                    self._success_cache_seconds
                    if ready
                    else self._failure_cache_seconds
                )
                self._cache_expires_at = self._clock() + ttl
            self._inflight = None

        level = logging.INFO if ready else logging.WARNING
        log_event(
            logger,
            level,
            "readiness_check_completed",
            dependency="opensearch",
            operation="readiness",
            outcome=outcome,
            elapsed_ms=elapsed_ms,
            exception_type=exception_type,
        )
        call_metrics(
            self._metrics,
            "record_opensearch_call",
            operation="readiness",
            outcome=outcome,
            elapsed_seconds=max(0.0, elapsed_ms / 1000),
        )
        call_metrics(
            self._metrics,
            "set_probe_status",
            probe="ready",
            available=ready and not self._closed,
        )
        return ready and not self._closed

    def _check_once(self) -> bool:
        """在专用线程内尝试一次底层检查，拒绝与尚未结束的旧检查并发或排队。"""
        # SDK 调用可能在 asyncio 超时后仍在线程里收尾；不能让第二次检查排队在
        # 第一次之后，否则表面上虽然快速返回，后台仍会无限增加依赖流量。
        if not self._execution_slot.acquire(blocking=False):
            return False
        try:
            return bool(self._check())
        finally:
            self._execution_slot.release()
