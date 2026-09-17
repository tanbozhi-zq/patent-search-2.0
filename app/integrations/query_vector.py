"""查询向量生成的最小内部合同。

适配器实现只能使用传入的绝对 deadline，不得为自己重置一份完整超时；
异常和日志不得包含 semantic_text、向量值或凭据。
"""

import asyncio
import base64
import binascii
from dataclasses import dataclass, field
import math
import struct
from threading import Thread
from time import monotonic
from typing import Any, Callable, Mapping, Protocol

import httpx

from app.core.exceptions import (
    QueryVectorError,
    QueryVectorInvalidResponseError,
    QueryVectorTimeoutError,
    QueryVectorUnavailableError,
)


@dataclass(frozen=True, slots=True)
class QueryVectorConfig:
    """某组注册向量字段共用的模型和维度合同。"""

    model: str
    dimensions: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.model, str)
            or not self.model.strip()
            or type(self.dimensions) is not int
            or self.dimensions < 1
        ):
            raise ValueError("query vector model and positive dimensions are required")


@dataclass(frozen=True, slots=True)
class QueryVectorResult:
    """适配器返回的原始向量结果；使用前必须通过下方统一校验。"""

    model: str
    vector: tuple[object, ...] = field(repr=False)


class QueryVectorAdapter(Protocol):
    """供后续 vector/hybrid 策略注入的单一向量生成边界。"""

    def generate(
        self,
        semantic_text: str,
        *,
        config: QueryVectorConfig,
        deadline: float,
    ) -> QueryVectorResult:
        """在传入的绝对 deadline 前生成一个查询向量。"""


class ArkQueryVectorAdapter:
    """通过现有 Ark HTTP 接口生成查询向量，并共享一个有界异步连接池。

    对外仍保留同步边界，便于复用现有 SearchService；实际 HTTP I/O 放在单个后台
    event loop 中，因此绝对 deadline 到期时可以取消正在读取的响应，而不是依赖
    httpx 每个阶段都会重新开始计算的 inactivity timeout。
    """

    def __init__(
        self,
        *,
        api_url: str,
        api_key: str,
        model_endpoints: Mapping[str, str],
        max_connections: int,
        client: Any | None = None,
    ) -> None:
        self.api_url = api_url
        self._api_key = api_key
        self._model_endpoints = dict(model_endpoints)
        self._client = client or httpx.AsyncClient(
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_connections,
            ),
            follow_redirects=False,
        )
        self._loop = asyncio.new_event_loop()
        self._loop_thread = Thread(
            target=self._run_event_loop,
            name="query-vector-http",
            daemon=True,
        )
        self._loop_thread.start()
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            asyncio.run_coroutine_threadsafe(
                self._client.aclose(),
                self._loop,
            ).result()
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop_thread.join()
            self._loop.close()

    def _run_event_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def generate(
        self,
        semantic_text: str,
        *,
        config: QueryVectorConfig,
        deadline: float,
    ) -> QueryVectorResult:
        model_endpoint = self._model_endpoints.get(config.model)
        if self._closed or not self._api_key or not model_endpoint:
            raise QueryVectorUnavailableError("query vector provider is not configured")
        remaining_query_vector_seconds(deadline)
        future = asyncio.run_coroutine_threadsafe(
            self._post(
                semantic_text,
                config=config,
                model_endpoint=model_endpoint,
                deadline=deadline,
            ),
            self._loop,
        )
        try:
            # _post 内的 wait_for 是唯一取消源；等待它完成取消清理后再返回，确保
            # httpx 已把连接槽归还共享池，避免同步侧的第二次取消制造竞态和泄漏。
            response = future.result()
        except httpx.TimeoutException as exc:
            raise QueryVectorTimeoutError("query vector request timed out") from exc
        except httpx.RequestError as exc:
            raise QueryVectorUnavailableError("query vector provider unavailable") from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise QueryVectorUnavailableError("query vector provider unavailable")
        if not 200 <= response.status_code < 300:
            raise QueryVectorError("query vector request was rejected")
        try:
            payload = response.json()
            model = payload.get("model")
            if not isinstance(model, str):
                raise ValueError("query vector response model is invalid")
            result = QueryVectorResult(
                model=model,
                vector=_extract_embedding(payload),
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise QueryVectorInvalidResponseError(
                "query vector response has an invalid shape"
            ) from exc
        remaining_query_vector_seconds(deadline)
        return validate_query_vector_result(result, config=config)

    async def _post(
        self,
        semantic_text: str,
        *,
        config: QueryVectorConfig,
        model_endpoint: str,
        deadline: float,
    ) -> httpx.Response:
        timeout = remaining_query_vector_seconds(deadline)
        try:
            return await asyncio.wait_for(
                self._client.post(
                    self.api_url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": model_endpoint,
                        "input": [{"type": "text", "text": semantic_text}],
                        "dimensions": config.dimensions,
                        "encoding_format": "base64",
                    },
                    timeout=timeout,
                ),
                timeout=timeout,
            )
        except TimeoutError as exc:
            raise QueryVectorTimeoutError("query vector request timed out") from exc


def remaining_query_vector_seconds(
    deadline: float,
    *,
    clock: Callable[[], float] = monotonic,
) -> float:
    """返回该阶段可用的剩余时间，不重置 HTTP 请求总预算。"""
    remaining = deadline - clock()
    if not math.isfinite(remaining) or remaining <= 0:
        raise QueryVectorTimeoutError("query vector deadline exhausted")
    return remaining


def validate_query_vector_result(
    result: QueryVectorResult,
    *,
    config: QueryVectorConfig,
) -> QueryVectorResult:
    """校验模型、维度、数值类型和有限值，并返回不可变 float 向量。"""
    if (
        not isinstance(result, QueryVectorResult)
        or not isinstance(result.model, str)
        or not isinstance(result.vector, (list, tuple))
    ):
        raise QueryVectorInvalidResponseError("query vector response has an invalid shape")
    if result.model != config.model:
        raise QueryVectorInvalidResponseError("query vector model mismatch")
    if len(result.vector) != config.dimensions:
        raise QueryVectorInvalidResponseError("query vector dimension mismatch")

    values: list[float] = []
    for value in result.vector:
        # bool 是 int 的子类，但不是合法的向量数值。
        if type(value) not in (int, float):
            raise QueryVectorInvalidResponseError("query vector contains a non-numeric value")
        try:
            normalized = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise QueryVectorInvalidResponseError(
                "query vector contains an invalid numeric value"
            ) from exc
        if not math.isfinite(normalized):
            raise QueryVectorInvalidResponseError("query vector contains a non-finite value")
        values.append(normalized)
    if not any(value != 0.0 for value in values):
        # 当前注册字段均使用 cosine similarity；零向量没有可用方向，应在供应商
        # 边界归类为无效响应，而不是交给 OpenSearch 形成失败分片。
        raise QueryVectorInvalidResponseError("query vector must not be all zero")
    return QueryVectorResult(model=result.model, vector=tuple(values))


def _extract_embedding(payload: dict) -> tuple[object, ...]:
    data = payload.get("data")
    if isinstance(data, list):
        if len(data) != 1:
            raise ValueError("query vector response must contain one result")
        data = data[0]
    if not isinstance(data, dict):
        raise ValueError("query vector response data is invalid")
    embedding = data.get("embedding")
    if isinstance(embedding, (list, tuple)):
        return tuple(embedding)
    if not isinstance(embedding, str):
        raise ValueError("query vector response embedding is invalid")
    try:
        raw = base64.b64decode(embedding, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("query vector response embedding is invalid") from exc
    if not raw or len(raw) % 4:
        raise ValueError("query vector response embedding is invalid")
    return struct.unpack(f"<{len(raw) // 4}f", raw)
