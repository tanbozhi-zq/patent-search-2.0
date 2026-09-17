"""管理指标读取器只执行代码内固定的 PromQL。它把外部 Prometheus 当作不可信的
管理数据源，限制查询窗口、并发、响应大小、series 数量和 label 值，再把每条
查询的失败隔离成 partial 结果。
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from time import monotonic
from typing import Any

import httpx

from app.schemas.admin import (
    AdminMetricResult,
    AdminMetricSample,
    AdminMetricsResponse,
)


ADMIN_METRICS_CACHE_SECONDS = 5.0
ADMIN_METRICS_MAX_CONCURRENCY = 4
ADMIN_METRICS_MAX_RESPONSE_BYTES = 1_048_576
ADMIN_METRICS_MAX_SERIES_PER_QUERY = 200
ADMIN_METRICS_MAX_LABEL_VALUE_LENGTH = 128
ADMIN_METRIC_WINDOWS = frozenset({300, 900, 3600})
DEFAULT_ADMIN_PROMETHEUS_JOB = "patent-search"

_SAFE_LABELS = frozenset(
    {
        "bulkhead",
        "code",
        "commit",
        "instance",
        "job",
        "operation",
        "outcome",
        "probe",
        "route",
        "status",
        "tag",
        "version",
    }
)


def _queries(
    window_seconds: int,
    job: str = DEFAULT_ADMIN_PROMETHEUS_JOB,
) -> dict[str, str]:
    """生成管理看板唯一允许执行的一组固定 PromQL 表达式。

    调用方只能选择经验证的时间窗口和受限 job label；浏览器不会提交表达式，因此管理
    API 不会成为任意 Prometheus 查询代理。每个 key 也是前端稳定渲染合同的一部分。
    """
    # job selector 由 Settings 校验为有限 label；所有表达式在这里集中生成，浏览器
    # 只能选择窗口，不能注入任意 PromQL。业务成功率和延迟排除四个控制面路由。
    window = f"{window_seconds}s"
    job_selector = f'job="{job}"'
    business = f'{job_selector},route!~"/(live|startup|ready|health)"'
    total_rate = (
        f"sum(rate(patent_search_http_requests_total{{{business}}}[{window}]))"
    )
    return {
        "request_rate": total_rate,
        "success_rate": (
            "sum(rate(patent_search_http_requests_total"
            f'{{{business},status=~"2.."}}[{window}])) '
            f"/ clamp_min({total_rate}, 0.001)"
        ),
        "latency_p50_seconds": (
            "histogram_quantile(0.50, sum by (le) "
            "(rate(patent_search_http_request_duration_seconds_bucket"
            f"{{{business}}}[{window}])))"
        ),
        "latency_p95_seconds": (
            "histogram_quantile(0.95, sum by (le) "
            "(rate(patent_search_http_request_duration_seconds_bucket"
            f"{{{business}}}[{window}])))"
        ),
        "latency_p99_seconds": (
            "histogram_quantile(0.99, sum by (le) "
            "(rate(patent_search_http_request_duration_seconds_bucket"
            f"{{{business}}}[{window}])))"
        ),
        "http_outcome_rate": (
            "sum by (status, code) (rate(patent_search_http_requests_total"
            f"{{{business}}}[{window}]))"
        ),
        "route_request_rate": (
            "sum by (route) (rate(patent_search_http_requests_total"
            f"{{{business}}}[{window}]))"
        ),
        "opensearch_call_rate": (
            "sum by (operation, outcome) "
            "(rate(patent_search_opensearch_calls_total{"
            f"{job_selector}" + "}"
            f"[{window}]))"
        ),
        "opensearch_latency_p95_seconds": (
            "histogram_quantile(0.95, sum by (operation, le) "
            "(rate(patent_search_opensearch_call_duration_seconds_bucket{"
            f"{job_selector}" + "}"
            f"[{window}])))"
        ),
        "opensearch_retry_rate": (
            "sum by (operation) (rate(patent_search_opensearch_retries_total{"
            f"{job_selector}" + "}"
            f"[{window}]))"
        ),
        "bulkhead_capacity_total": (
            "sum by (bulkhead) (patent_search_bulkhead_capacity{"
            f"{job_selector}" + "})"
        ),
        "bulkhead_capacity_by_instance": (
            "patent_search_bulkhead_capacity{" + f"{job_selector}" + "}"
        ),
        "bulkhead_in_flight_total": (
            "sum by (bulkhead) (patent_search_bulkhead_in_flight{"
            f"{job_selector}" + "})"
        ),
        "bulkhead_in_flight_by_instance": (
            "patent_search_bulkhead_in_flight{" + f"{job_selector}" + "}"
        ),
        "bulkhead_utilization_by_instance": (
            "patent_search_bulkhead_in_flight{" + f"{job_selector}" + "} "
            "/ patent_search_bulkhead_capacity{" + f"{job_selector}" + "}"
        ),
        "bulkhead_worst_utilization": (
            "max by (job, bulkhead) "
            "(patent_search_bulkhead_in_flight{" + f"{job_selector}" + "} "
            "/ patent_search_bulkhead_capacity{" + f"{job_selector}" + "})"
        ),
        "bulkhead_rejections": (
            "sum by (bulkhead) (increase(patent_search_bulkhead_rejections_total{"
            f"{job_selector}" + "}"
            f"[{window}]))"
        ),
        "probe_worst": (
            "min by (job, probe) (patent_search_probe_status{"
            f"{job_selector}" + "})"
        ),
        "probe_by_instance": (
            "patent_search_probe_status{" + f"{job_selector}" + "}"
        ),
        "service_start_time_seconds": (
            "patent_search_service_start_time_seconds{"
            + f"{job_selector}"
            + "}"
        ),
        "build_info": "patent_search_build_info{" + f"{job_selector}" + "}",
    }


@dataclass(frozen=True)
class _CachedSnapshot:
    """按窗口缓存的一次不可变管理指标快照及其过期单调时间。"""

    # 缓存按窗口保存不可变响应；过期后由同窗口的 single-flight 任务重新读取。
    expires_at: float
    response: AdminMetricsResponse


class PrometheusAdminMetricsReader:
    """受限读取由服务端拥有的固定 PromQL 查询集合。

    Reader 将 Prometheus 视为不可信的可选旁路：按窗口 single-flight 和短缓存减少重复
    拉取，按查询隔离故障，并限制连接、总时间、响应字节、series 与 label 值的规模。
    """

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        job: str = DEFAULT_ADMIN_PROMETHEUS_JOB,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """初始化受限的 Prometheus HTTP client、缓存和同窗口共享任务表。

        ``base_url`` 只被拼接为固定 query endpoint，``job`` 用于服务端拥有的
        selector；可选 transport 仅供测试注入。client 禁用环境代理与重定向，并将
        连接数固定在管理旁路预算内，不能与业务流量竞争无上限资源。
        """
        # AsyncClient 不信任环境代理、禁止重定向，并把连接池限制在固定并发；这条
        # 管理旁路不能因为 Prometheus 异常占满业务 HTTP 连接或泄露凭据。
        self._query_url = f"{base_url.rstrip('/')}/api/v1/query"
        self._timeout_seconds = timeout_seconds
        self._job = job
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            limits=httpx.Limits(
                max_connections=ADMIN_METRICS_MAX_CONCURRENCY,
                max_keepalive_connections=2,
            ),
            follow_redirects=False,
            transport=transport,
            trust_env=False,
        )
        self._semaphore = asyncio.Semaphore(ADMIN_METRICS_MAX_CONCURRENCY)
        self._lock = asyncio.Lock()
        self._cache: dict[int, _CachedSnapshot] = {}
        self._inflight: dict[int, asyncio.Task[AdminMetricsResponse]] = {}
        self._closed = False

    async def read(self, window_seconds: int) -> AdminMetricsResponse:
        """返回一个窗口的完整指标快照，复用缓存或正在进行的同窗口批量读取。

        调用方取消等待不会取消共享 task；真实 Prometheus 失败被 ``_read_uncached``
        降级为单个 unavailable 指标，只有 reader 关闭等生命周期错误会向上传播。
        """
        # 先读短缓存，再按 window single-flight；同一窗口的并发看板刷新共享一次
        # 20 条 PromQL 批次，断开的浏览器不会触发重复查询。
        if window_seconds not in ADMIN_METRIC_WINDOWS:
            raise ValueError("unsupported administrator metric window")
        async with self._lock:
            if self._closed:
                raise RuntimeError("administrator metrics reader is closed")
            cached = self._cache.get(window_seconds)
            if cached is not None and cached.expires_at > monotonic():
                return cached.response.model_copy(deep=True)
            task = self._inflight.get(window_seconds)
            if task is None:
                task = asyncio.create_task(self._read_uncached(window_seconds))
                self._inflight[window_seconds] = task
        try:
            response = await asyncio.shield(task)
        except asyncio.CancelledError:
            # 保留 shared task 的注册：其他 viewer 可能仍在等待，断开的客户端
            # 不能取消整批查询并导致下一次刷新重复访问 Prometheus。
            raise
        except BaseException:
            async with self._lock:
                if self._inflight.get(window_seconds) is task:
                    self._inflight.pop(window_seconds, None)
            raise
        async with self._lock:
            if self._inflight.get(window_seconds) is task:
                self._cache[window_seconds] = _CachedSnapshot(
                    expires_at=monotonic() + ADMIN_METRICS_CACHE_SECONDS,
                    response=response,
                )
                self._inflight.pop(window_seconds, None)
        return response.model_copy(deep=True)

    async def close(self) -> None:
        """取消未完成的共享读取、释放缓存并关闭专用 HTTP 客户端。"""
        # 关闭时取消未完成批次、清空缓存，再关闭 HTTP client；不让应用退出时
        # 留下后台 query task 或 keep-alive 连接。
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            tasks = tuple(set(self._inflight.values()))
            self._inflight.clear()
            self._cache.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._client.aclose()

    async def _read_uncached(self, window_seconds: int) -> AdminMetricsResponse:
        """并发执行固定查询集，并把每条查询的失败隔离成 partial 结果项。"""
        # 每条查询独立 gather 并捕获异常，单个指标坏掉只标记对应卡片 unavailable。
        queries = _queries(window_seconds, self._job)
        raw_results = await asyncio.gather(
            *(self._query(key, expression) for key, expression in queries.items()),
            return_exceptions=True,
        )
        results: list[AdminMetricResult] = []
        for key, raw in zip(queries, raw_results, strict=True):
            if isinstance(raw, BaseException):
                results.append(AdminMetricResult(key=key, available=False))
            else:
                results.append(
                    AdminMetricResult(key=key, available=True, samples=raw)
                )
        return AdminMetricsResponse(
            source="prometheus",
            generated_at=datetime.now(timezone.utc),
            window_seconds=window_seconds,
            partial=any(not result.available for result in results),
            results=results,
        )

    async def _query(
        self,
        key: str,
        expression: str,
    ) -> list[AdminMetricSample]:
        """在包含排队、连接和读取的总时间上限内执行一条固定 PromQL 查询。"""
        # asyncio.timeout 包住等待槽位、建立连接、读取响应的总时限，而不是只限制
        # response body；这保证管理查询不会长时间排队。
        del key
        async with asyncio.timeout(self._timeout_seconds):
            return await self._query_within_deadline(expression)

    async def _query_within_deadline(
        self,
        expression: str,
    ) -> list[AdminMetricSample]:
        """流式拉取并严格验证一条 Prometheus instant vector 响应。

        声明长度与实际字节数都受限；解析成功后还要限制 vector series 数和每个允许
        label 的形状，保证一条失控响应不会放大管理页面的内存或标签基数。
        """
        # 使用流式读取同时检查 Content-Length 和实际累计字节，防止 Prometheus 返回
        # 一个没有长度头的超大响应；解析后再限制 vector series 数量和 label 内容。
        async with self._semaphore, self._client.stream(
            "GET",
            self._query_url,
            params={"query": expression},
            headers={"Accept": "application/json"},
        ) as response:
            declared_size = response.headers.get("Content-Length")
            if declared_size is not None:
                try:
                    parsed_size = int(declared_size)
                except ValueError as exc:
                    raise ValueError("Prometheus Content-Length is invalid") from exc
                if parsed_size < 0:
                    raise ValueError("Prometheus Content-Length is invalid")
                if parsed_size > ADMIN_METRICS_MAX_RESPONSE_BYTES:
                    raise ValueError("Prometheus response exceeds byte limit")
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > ADMIN_METRICS_MAX_RESPONSE_BYTES:
                    raise ValueError("Prometheus response exceeds byte limit")
        payload = json.loads(body)
        result = _vector_result(payload)
        if len(result) > ADMIN_METRICS_MAX_SERIES_PER_QUERY:
            raise ValueError("Prometheus response exceeds series limit")
        return [_sample(item) for item in result]


def unavailable_metrics(window_seconds: int) -> AdminMetricsResponse:
    """构造完整 key 集合均不可用的响应，使前端无需猜测 Prometheus 是否已配置。"""
    # Prometheus 未配置时仍返回完整 key 集合，前端可以稳定渲染每个指标卡片为不可用。
    return AdminMetricsResponse(
        source="unavailable",
        generated_at=datetime.now(timezone.utc),
        window_seconds=window_seconds,
        partial=True,
        results=[
            AdminMetricResult(key=key, available=False)
            for key in _queries(window_seconds)
        ],
    )


def _vector_result(payload: Any) -> list[dict[str, Any]]:
    """从 Prometheus 成功响应中提取唯一允许的 instant vector 数据结构。"""
    # 管理看板只接受 instant vector；matrix/scalar/string 等其他 Prometheus 结果
    # 类型不能直接套用统一 sample 模型。
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise ValueError("Prometheus query failed")
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("resultType") != "vector":
        raise ValueError("Prometheus response is not an instant vector")
    result = data.get("result")
    if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
        raise ValueError("Prometheus vector is invalid")
    return result


def _sample(item: dict[str, Any]) -> AdminMetricSample:
    """将一条原始 vector item 收缩为允许标签与有限浮点值的公开 sample。"""
    # 只保留面板需要的 label 名称和有限长度值，丢弃 instance 之外的未知 label；
    # value 必须是有限浮点数，避免 NaN/Inf 传播到前端计算。
    raw_labels = item.get("metric")
    raw_value = item.get("value")
    if not isinstance(raw_labels, dict):
        raise ValueError("Prometheus labels are invalid")
    labels: dict[str, str] = {}
    for name, value in raw_labels.items():
        if name not in _SAFE_LABELS:
            continue
        if not isinstance(value, str) or len(value) > ADMIN_METRICS_MAX_LABEL_VALUE_LENGTH:
            raise ValueError("Prometheus label is invalid")
        labels[name] = value
    if (
        not isinstance(raw_value, list)
        or len(raw_value) != 2
        or not isinstance(raw_value[1], str)
        or len(raw_value[1]) > 64
    ):
        raise ValueError("Prometheus sample is invalid")
    value = float(raw_value[1])
    if not math.isfinite(value):
        raise ValueError("Prometheus sample is not finite")
    return AdminMetricSample(labels=labels, value=value)
