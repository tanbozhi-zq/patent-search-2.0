"""Repository 是唯一直接访问 OpenSearch 的业务层。它在这里统一处理连接池准入、
绝对 deadline、一次性安全重试、响应形状校验和错误翻译；上层只看到稳定领域异常。
"""

import logging
from threading import BoundedSemaphore
from time import monotonic, sleep
from typing import Any, Callable, Optional, Sequence, Tuple

from opensearchpy import OpenSearch
from opensearchpy.exceptions import (
    ConnectionError as OpenSearchConnectionError,
    ConnectionTimeout,
    ImproperlyConfigured,
    OpenSearchException,
    TransportError,
)
from urllib3.util import Timeout

from app.core.admin_config.runtime import (
    RuntimeConfigProvider,
    current_runtime_config,
    runtime_snapshot_from_settings,
)
from app.core.admin_config.models import RuntimeConfigSnapshot
from app.core.config import Settings, get_settings
from app.core.deadline import current_request_deadline
from app.core.exceptions import (
    OpenSearchQueryError,
    SearchDependencyError,
    SearchDependencyTimeoutError,
    SearchDependencyUnavailableError,
)
from app.core.logging import log_event
from app.core.metrics import call_metrics
from app.core.request_context import current_request_id, new_request_id
from app.mappings.source_fields import TARGET_RANK_SOURCE_FIELDS
from app.repositories.deadline_connection import DeadlineUrllib3HttpConnection


logger = logging.getLogger(__name__)


class _InvalidOpenSearchResponseError(OpenSearchQueryError):
    # 下游 HTTP 成功但 JSON 形状不符合服务预期时，单独标记为 invalid_response，
    # 便于指标和管理员看板区分“依赖不可用”和“依赖返回坏数据”。
    pass


def _build_opensearch_client(settings: Settings, *, pool_maxsize: int) -> OpenSearch:
    """按应用资源边界构造 OpenSearch 客户端，并关闭 SDK 自主重试。

    应用层需要统一记录每次业务操作的 deadline、重试和指标，因而不能让 SDK 在此处
    隐式等待或再次发请求；readiness 也通过该工厂使用更小的独立连接池。
    """
    # SDK 内建重试全部关闭，交给 _perform 统一控制次数和总 deadline；否则 SDK
    # 的内部等待可能绕过应用层的剩余时间和观测记录。
    http_auth = None
    if settings.opensearch_user and settings.opensearch_pass:
        http_auth = (settings.opensearch_user, settings.opensearch_pass)
    return OpenSearch(
        hosts=[settings.opensearch_url],
        http_auth=http_auth,
        use_ssl=settings.opensearch_use_https,
        verify_certs=settings.opensearch_verify_certs,
        ssl_show_warn=settings.opensearch_verify_certs,
        timeout=settings.opensearch_timeout_seconds,
        pool_maxsize=pool_maxsize,
        max_retries=0,
        retry_on_status=(),
        retry_on_timeout=False,
        connection_class=DeadlineUrllib3HttpConnection,
    )


def build_readiness_client(settings: Settings) -> OpenSearch:
    """构造只为 readiness 保留的一连接客户端。"""
    return _build_opensearch_client(settings, pool_maxsize=1)


class OpenSearchRepository:
    """对 OpenSearch 读操作提供统一资源、超时、重试和响应边界。

    所有公开读方法最终进入 ``_perform``：它负责连接槽准入、绝对 deadline、可安全的
    短暂重试、领域异常翻译与观测。上层服务不应直接持有或调用 SDK client。
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        client: Any = None,
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
        metrics=None,
        runtime_config_provider: RuntimeConfigProvider | None = None,
    ):
        self.settings = settings or get_settings()
        self._runtime_config_provider = runtime_config_provider or RuntimeConfigProvider(
            runtime_snapshot_from_settings(self.settings)
        )
        self.index_name = self.settings.opensearch_index
        self.hosts = [self.settings.opensearch_url]
        self.http_auth = self._build_http_auth()
        self.verify_certs = self.settings.opensearch_verify_certs
        self._clock = clock
        self._sleep = sleeper
        self._metrics = metrics
        # urllib3 连接池耗尽时默认可能继续创建/等待连接；BoundedSemaphore 让
        # 应用能在进入 SDK 前明确返回 50302，而不是把压力隐藏在底层线程里。
        self._client_slots = BoundedSemaphore(self.settings.opensearch_pool_maxsize)
        self.client = client if client is not None else self._build_client()

    @property
    def timeout(self) -> int:
        """The per-call timeout in the request's immutable runtime snapshot."""
        return int(
            current_runtime_config(self._runtime_config_provider).value_for(
                "opensearch.timeout_seconds"
            )
        )

    def runtime_config_snapshot(self) -> RuntimeConfigSnapshot:
        """Return the provider snapshot bound to this repository instance.

        Verification uses this process-level readback rather than the request
        context, because an apply request intentionally keeps its ingress
        snapshot until that request itself has completed.
        """

        return self._runtime_config_provider.snapshot()

    def bind_runtime_config_provider(self, provider: RuntimeConfigProvider) -> None:
        """Bind the process-wide provider during application startup only."""

        self._runtime_config_provider = provider

    def _build_http_auth(self) -> Optional[Tuple[str, str]]:
        if self.settings.opensearch_user and self.settings.opensearch_pass:
            return (self.settings.opensearch_user, self.settings.opensearch_pass)
        return None

    def _build_client(self) -> OpenSearch:
        return _build_opensearch_client(
            self.settings,
            pool_maxsize=self.settings.opensearch_pool_maxsize,
        )

    def close(self) -> None:
        # 客户端只在应用 lifespan 结束时关闭，不能在单次请求后释放共享连接池。
        self.client.close()

    def bind_metrics(self, *, metrics) -> None:
        # Repository 可以先创建、后绑定 metrics，解决 lifespan 中指标对象和
        # OpenSearch 对象的初始化顺序依赖。
        self._metrics = metrics

    def search(self, body: dict, *, search_pipeline: str | None = None) -> dict:
        # 所有业务 search 都经过 _perform，包含列表搜索、目标查找和排名辅助查询。
        return self._perform(
            "search",
            lambda request_timeout: self._search(
                body,
                request_timeout,
                search_pipeline=search_pipeline,
            ),
        )

    def count(self, body: dict) -> int:
        # count 同样受同一 deadline/重试策略，避免排名查询成为未观测的旁路。
        return self._perform(
            "count",
            lambda request_timeout: self._count(
                body,
                request_timeout,
            ),
        )

    def _search(
        self,
        body: dict,
        request_timeout: Timeout,
        *,
        search_pipeline: str | None,
    ) -> dict:
        # request_timeout 是“当前尝试”的剩余时间，不是重新开始的完整总预算。
        params = {
            "request_timeout": request_timeout,
            # opensearch-py 2.x 会把 Python False 序列化成服务端不接受的 "False"；
            # 显式使用 REST 参数字面量，真实 3.3 服务端才能解析。
            "allow_partial_search_results": "false",
        }
        if search_pipeline is not None:
            params["search_pipeline"] = search_pipeline
        raw = self.client.search(
            index=self.index_name,
            body=body,
            params=params,
        )
        self._validate_search_response(raw)
        return raw

    def _count(self, body: dict, request_timeout: Timeout) -> int:
        # count 响应比 search 简单，但仍必须验证 count 是真正的非 bool 整数。
        raw = self.client.count(
            index=self.index_name,
            body=body,
            params={"request_timeout": request_timeout},
        )
        if not isinstance(raw, dict) or not self._is_int(raw.get("count")):
            raise _InvalidOpenSearchResponseError(
                "OpenSearch returned an invalid count response"
            )
        return raw["count"]

    def count_with_min_score(self, query: dict, min_score: float) -> int:
        # 通过 size=0 + track_total_hits 只取数量，用于相关性排名的严格更高计数。
        raw = self.search({
            "size": 0,
            "track_total_hits": True,
            "min_score": min_score,
            "query": query,
        })
        return self._total_hits(raw["hits"])

    def find_in_query(self, query: dict, identity: dict) -> Optional[dict]:
        # 先在基础结果集内寻找目标，而不是只根据全库标识符判断；返回第一条命中
        # 供后续按真实查询上下文重建排名 DSL。
        raw = self.search({
            "size": 1,
            "_source": list(TARGET_RANK_SOURCE_FIELDS),
            "track_total_hits": True,
            "query": {"bool": {"must": [query], "filter": [identity]}},
        })
        return self._first_hit(raw)

    def find_target(self, identifier: str) -> tuple[str, Optional[dict], int]:
        """按内部 ID 优先、公开号回退的规则定位一个目标专利。

        返回实际使用的索引字段、第一条命中和总命中数，而非直接判定唯一性；目标排名
        Service 需要利用总数把“目标不存在”和“标识符歧义”作为不同业务结果返回。
        """
        # 目标查找优先内部 patent_id，再回退到大写公开号；返回 match_count 让
        # Service 能把歧义目标和不存在目标区分开。
        patent_id = identifier.strip()
        raw = self.search({
            "size": 10,
            "_source": list(TARGET_RANK_SOURCE_FIELDS),
            "track_total_hits": True,
            "query": self._identifier_query("patent_id", patent_id),
        })
        hits_data = raw["hits"]
        hits = hits_data["hits"]
        if hits:
            return "patent_id", hits[0], self._total_hits(hits_data)

        publication_number = patent_id.upper()
        raw = self.search({
            "size": 10,
            "_source": list(TARGET_RANK_SOURCE_FIELDS),
            "track_total_hits": True,
            "query": self._identifier_query("PublicationNumber", publication_number),
        })
        hits_data = raw["hits"]
        hits = hits_data["hits"]
        return "PublicationNumber", (hits[0] if hits else None), self._total_hits(hits_data)

    def get_patent_by_identifier(
        self,
        identifier: str,
        source_fields: Optional[Sequence[str]] = None,
    ) -> Optional[dict]:
        """按稳定标识符优先级读取一条专利，并按需限制 ``_source`` 字段。

        这是详情、引证和法律历史的共享读取入口。每次查询固定 ``size=1``，并把字段
        投影权交给上层 Service，避免无意读取说明书等大字段。
        """
        # 详情/引证/法律历史按稳定字段优先级逐次查询；每次只取一条，避免完整
        # 文档读取走大结果集。source_fields 由详情 Service 决定，普通读取不带正文。
        for field in ("patent_id", "PublicationNumber", "ApplicationNumber"):
            body = {
                "size": 1,
                "query": self._identifier_query(field, identifier),
            }
            if source_fields is not None:
                body["_source"] = list(source_fields)
            raw = self.search(body)
            hit = self._first_hit(raw)
            if hit is not None:
                return hit
        return None

    def _perform(self, operation: str, callback: Callable[[Timeout], Any]) -> Any:
        """在一个绝对 deadline 内执行一次只读 SDK 操作，并按策略最多重试一次。

        ``callback`` 只获得本次尝试剩余的 timeout；连接槽、退避时间与 SDK 调用都计入
        同一总预算。无论成功或失败，函数都会写出一次完整调用指标和结构化日志，并把
        SDK 异常翻译成上层可处理的领域异常。
        """
        # 这是 Repository 的核心边界：一个业务操作可以有最多一次显式重试，
        # 但所有尝试、退避和连接等待都要受同一个绝对截止时间约束。
        started = self._clock()
        request_id = current_request_id() or new_request_id()
        runtime_config = current_runtime_config(self._runtime_config_provider)
        deadline = current_request_deadline()
        if deadline is None:
            deadline = started + float(
                runtime_config.value_for("request.deadline_seconds")
            )

        retry_count = 0
        outcome = "error"
        exception_type: str | None = None
        last_cause: Exception | None = None
        try:
            while True:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise SearchDependencyTimeoutError(
                        "OpenSearch request deadline exhausted"
                    )

                # 连接槽非阻塞获取，避免在 semaphore 内等待而消耗不可见的请求时间。
                if not self._client_slots.acquire(blocking=False):
                    raise SearchDependencyUnavailableError(
                        "OpenSearch connection pool unavailable"
                    )

                request_timeout = Timeout(
                    total=min(
                        float(runtime_config.value_for("opensearch.timeout_seconds")),
                        remaining,
                    )
                )
                try:
                    # callback 只收到当前尝试可用的 timeout；SDK 返回后还要再次检查
                    # deadline，防止“结果到了但总预算已经耗尽”被当成成功。
                    result = callback(request_timeout)
                except (ImproperlyConfigured, OpenSearchException) as exc:
                    translated = self._translate_exception(exc)
                    cause = exc
                    last_cause = exc
                else:
                    if deadline - self._clock() <= 0:
                        raise SearchDependencyTimeoutError(
                            "OpenSearch request deadline exhausted"
                        )
                    outcome = "success"
                    return result
                finally:
                    self._client_slots.release()

                if deadline - self._clock() <= 0:
                    raise SearchDependencyTimeoutError(
                        "OpenSearch request deadline exhausted"
                    ) from cause

                if (
                    not self._is_retryable(translated)
                    or retry_count
                    >= int(runtime_config.value_for("opensearch.max_retries"))
                ):
                    # 语法/配置/响应形状等非瞬时错误不重试；即使可重试，也不能超过
                    # Settings 已限定的最大次数。
                    raise translated from cause

                backoff = float(
                    runtime_config.value_for("opensearch.retry_backoff_seconds")
                ) * (
                    2 ** retry_count
                )
                if deadline - self._clock() <= backoff:
                    raise SearchDependencyTimeoutError(
                        "OpenSearch request deadline exhausted"
                    ) from cause

                # 退避也占用总预算，剩余时间不够时直接返回 timeout，不再 sleep。
                retry_count += 1
                call_metrics(
                    self._metrics,
                    "record_opensearch_retry",
                    operation=operation,
                    outcome=self._dependency_failure_outcome(
                        translated,
                        cause,
                    ),
                )
                log_event(
                    logger,
                    logging.WARNING,
                    "dependency_retry",
                    request_id=request_id,
                    dependency="opensearch",
                    operation=operation,
                    outcome="retrying",
                    retry_count=retry_count,
                    exception_type=type(cause).__name__,
                )
                self._sleep(backoff)
        except SearchDependencyTimeoutError as exc:
            outcome = "timeout"
            exception_type = type(exc).__name__
            raise
        except SearchDependencyUnavailableError as exc:
            outcome = self._dependency_failure_outcome(
                exc,
                last_cause,
            )
            exception_type = type(exc).__name__
            raise
        except _InvalidOpenSearchResponseError as exc:
            outcome = "invalid_response"
            exception_type = type(exc).__name__
            raise
        except OpenSearchQueryError as exc:
            outcome = "error"
            exception_type = type(exc).__name__
            raise
        except Exception as exc:
            outcome = "unexpected_error"
            exception_type = type(exc).__name__
            raise
        finally:
            # 无论成功、翻译后的异常还是意外异常，都写同一条完成观测并释放一次
            # 业务操作的指标上下文；重试次数作为字段保留，而不是拆成多条调用。
            elapsed_seconds = max(0.0, self._clock() - started)
            call_metrics(
                self._metrics,
                "record_opensearch_call",
                operation=operation,
                outcome=outcome,
                elapsed_seconds=elapsed_seconds,
            )
            level = logging.INFO if outcome == "success" else logging.WARNING
            log_event(
                logger,
                level,
                "dependency_call_completed",
                request_id=request_id,
                dependency="opensearch",
                operation=operation,
                outcome=outcome,
                elapsed_ms=round(elapsed_seconds * 1000, 3),
                retry_count=retry_count,
                exception_type=exception_type,
            )

    @staticmethod
    def _translate_exception(exc: Exception) -> SearchDependencyError:
        """把 OpenSearch/urllib3 的实现异常压缩为稳定的领域错误类型。

        响应层只依赖“超时、暂不可用、查询失败”这几个语义，原始异常仍作为 Python
        cause 保留给日志与调试，不能泄露到调用方消息中。
        """
        # 将 SDK/Transport 异常压缩成调用方可判断的三类：查询失败、暂不可用、超时。
        # 原始异常只作为 Python cause 保留，不进入响应 message。
        if isinstance(exc, ConnectionTimeout):
            return SearchDependencyTimeoutError("OpenSearch request timed out")
        if isinstance(exc, OpenSearchConnectionError):
            return SearchDependencyUnavailableError("OpenSearch connection unavailable")
        if isinstance(exc, TransportError):
            status_code = exc.status_code
            if status_code == 504:
                return SearchDependencyTimeoutError("OpenSearch gateway timed out")
            if status_code == 429 or (
                isinstance(status_code, int) and 500 <= status_code < 600
            ):
                return SearchDependencyUnavailableError("OpenSearch temporarily unavailable")
        return OpenSearchQueryError("OpenSearch request was rejected")

    @staticmethod
    def _is_retryable(exc: SearchDependencyError) -> bool:
        # 只有连接不可用和超时属于可安全重试的幂等读；查询被拒绝和坏响应不重试。
        return isinstance(
            exc,
            (SearchDependencyUnavailableError, SearchDependencyTimeoutError),
        )

    @staticmethod
    def _dependency_failure_outcome(
        exc: SearchDependencyError,
        cause: Exception | None,
    ) -> str:
        # metrics outcome 比错误码更细：连接错误单独区分，其他 5xx/429 归为 unavailable。
        if isinstance(exc, SearchDependencyTimeoutError):
            return "timeout"
        if (
            isinstance(exc, SearchDependencyUnavailableError)
            and isinstance(cause, OpenSearchConnectionError)
        ):
            return "connection_error"
        return "unavailable"

    @classmethod
    def _validate_search_response(cls, raw: Any) -> None:
        """验证 mapper 所需的最小 search 响应形状，而不是信任 HTTP 200。

        只检查 ``hits``、每条 ``_source``、可选 ``_score``/``took`` 与总命中数的类型；
        字段业务含义仍由 mapper 处理。无效形状被标记为独立领域错误且绝不重试。
        """
        # OpenSearch HTTP 200 不等于契约正确。这里只验证 mapper 会依赖的最小结构，
        # 防止第三方/代理返回一个看似 JSON 的错误对象后在更深处触发内部异常。
        if not isinstance(raw, dict):
            raise _InvalidOpenSearchResponseError(
                "OpenSearch returned an invalid search response"
            )
        timed_out = raw.get("timed_out")
        if timed_out is not None and not isinstance(timed_out, bool):
            raise _InvalidOpenSearchResponseError(
                "OpenSearch returned an invalid search response"
            )
        if timed_out:
            raise SearchDependencyTimeoutError(
                "OpenSearch returned an incomplete search response"
            )
        shards = raw.get("_shards")
        if shards is not None:
            if not isinstance(shards, dict) or not cls._is_int(shards.get("failed")):
                raise _InvalidOpenSearchResponseError(
                    "OpenSearch returned an invalid search response"
                )
            if shards["failed"] > 0:
                raise OpenSearchQueryError(
                    "OpenSearch returned an incomplete search response"
                )
        hits_data = raw.get("hits")
        if not isinstance(hits_data, dict):
            raise _InvalidOpenSearchResponseError(
                "OpenSearch returned an invalid search response"
            )
        hits = hits_data.get("hits")
        if not isinstance(hits, list) or any(
            not isinstance(hit, dict) or not isinstance(hit.get("_source"), dict)
            for hit in hits
        ):
            raise _InvalidOpenSearchResponseError(
                "OpenSearch returned an invalid search response"
            )
        if "took" in raw and not cls._is_int(raw["took"]):
            raise _InvalidOpenSearchResponseError(
                "OpenSearch returned an invalid search response"
            )
        for hit in hits:
            score = hit.get("_score")
            if score is not None and (
                not isinstance(score, (int, float)) or isinstance(score, bool)
            ):
                raise _InvalidOpenSearchResponseError(
                    "OpenSearch returned an invalid search response"
                )
        if "total" in hits_data:
            total = hits_data["total"]
            if isinstance(total, dict):
                total = total.get("value")
            if not cls._is_int(total):
                raise _InvalidOpenSearchResponseError(
                    "OpenSearch returned an invalid search response"
                )

    def _identifier_query(self, field: str, identifier: str) -> dict:
        # 标识符字段都是 keyword 语义，不能使用 match 或分析器进行模糊匹配。
        return {"term": {field: identifier}}

    def _first_hit(self, raw: dict) -> Optional[dict]:
        # 调用方已把 size 限制为 1；这里统一处理空 hits，减少每个 Service 的重复判断。
        hits = raw["hits"]["hits"]
        if not hits:
            return None
        return hits[0]

    @staticmethod
    def _total_hits(hits: dict) -> int:
        # OpenSearch 可能返回旧式整数或 track_total_hits 对象，两种形态统一成 int。
        total = hits.get("total", 0)
        if isinstance(total, dict):
            return total["value"]
        return total

    @staticmethod
    def _is_int(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)
