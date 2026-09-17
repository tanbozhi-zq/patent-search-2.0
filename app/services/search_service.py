"""Service 层把“请求对象”转换成“可执行查询”，再把 OpenSearch 原始响应
转成公共结果。它不处理 HTTP 鉴权，也不把底层客户端细节泄露给路由。
"""

from contextlib import contextmanager
import math
import struct
from time import monotonic
from typing import Mapping, Protocol

from app.core.deadline import current_request_deadline
from app.core.exceptions import (
    PaginationOutOfRangeError,
    QueryComplexityError,
    QuerySyntaxError,
    QueryVectorTimeoutError,
    SearchDependencyTimeoutError,
)
from app.core.metrics import call_metrics
from app.integrations.query_vector import (
    QueryVectorAdapter,
    QueryVectorConfig,
    validate_query_vector_result,
)
from app.mappings.query_field_mapping import VECTOR_FIELD_REGISTRY
from app.mappings.result_mapper import map_search_response
from app.query.dsl_builder import build_search_dsl, build_target_rank_dsl
from app.query.semantic_dsl_builder import (
    build_hybrid_boolean_query,
    build_hybrid_search_dsl,
    semantic_ranking_profile,
    build_vector_search_dsl,
)
from app.query.budget import (
    DEFAULT_QUERY_BUDGET_PROVIDER,
    QueryBudget,
    QueryBudgetProvider,
)
from app.repositories.opensearch_repo import OpenSearchRepository
from app.schemas.search import SearchMode, SearchRequest, TargetRankRequest


class SearchStrategy(Protocol):
    """检索模式共享的执行边界。"""

    def search(self, request: SearchRequest, *, budget: QueryBudget) -> dict:
        """使用同一次请求冻结的预算执行并映射搜索结果。"""


class BooleanSearchStrategy:
    """保持现有 parser、DSL、Repository 和响应映射行为的布尔策略。"""

    def __init__(self, repository: OpenSearchRepository, metrics=None):
        self.repository = repository
        self.metrics = metrics

    def search(self, request: SearchRequest, *, budget: QueryBudget) -> dict:
        body = build_search_dsl(request, budget=budget)
        with _observe_query_stage(
            self.metrics,
            request,
            stage="opensearch",
            ranking_profile="boolean",
        ):
            raw = self.repository.search(body)
        _record_opensearch_took(self.metrics, request, "boolean", raw)
        return map_search_response(
            raw,
            page=request.page,
            page_size=request.page_size,
            budget=budget,
        )


class VectorSearchStrategy:
    """生成一次或多组查询向量，再执行 k-NN 或多向量原生融合。"""

    def __init__(
        self,
        repository: OpenSearchRepository,
        query_vector_adapter: QueryVectorAdapter,
        metrics=None,
    ) -> None:
        self.repository = repository
        self.query_vector_adapter = query_vector_adapter
        self.metrics = metrics

    def search(self, request: SearchRequest, *, budget: QueryBudget) -> dict:
        profile = semantic_ranking_profile(request)
        with _observe_query_stage(
            self.metrics,
            request,
            stage="query_vector",
            ranking_profile=profile,
        ):
            vectors = _generate_query_vectors(request, self.query_vector_adapter)
        body, pipeline, profile = build_vector_search_dsl(request, vectors)
        with _observe_query_stage(
            self.metrics,
            request,
            stage="opensearch",
            ranking_profile=profile,
        ):
            raw = self.repository.search(body, search_pipeline=pipeline)
        _record_opensearch_took(self.metrics, request, profile, raw)
        return _map_semantic_response(request, raw, profile, budget)


class HybridSearchStrategy:
    """把现有布尔子查询与一个或多个向量子查询交给 OpenSearch RRF。"""

    def __init__(
        self,
        repository: OpenSearchRepository,
        query_vector_adapter: QueryVectorAdapter,
        metrics=None,
    ) -> None:
        self.repository = repository
        self.query_vector_adapter = query_vector_adapter
        self.metrics = metrics

    def search(self, request: SearchRequest, *, budget: QueryBudget) -> dict:
        boolean_query = build_hybrid_boolean_query(request, budget=budget)
        profile = semantic_ranking_profile(request)
        with _observe_query_stage(
            self.metrics,
            request,
            stage="query_vector",
            ranking_profile=profile,
        ):
            vectors = _generate_query_vectors(request, self.query_vector_adapter)
        body, pipeline, profile = build_hybrid_search_dsl(
            request,
            vectors,
            boolean_query=boolean_query,
        )
        with _observe_query_stage(
            self.metrics,
            request,
            stage="opensearch",
            ranking_profile=profile,
        ):
            raw = self.repository.search(body, search_pipeline=pipeline)
        _record_opensearch_took(self.metrics, request, profile, raw)
        return _map_semantic_response(request, raw, profile, budget)


class SearchService:
    """编排搜索与目标排名，不保存单个请求的可变业务状态。

    Service 是查询编译、Repository I/O 与公共响应映射之间的业务边界。它不处理 HTTP
    鉴权或序列化细节，但保证同一次操作中的所有查询使用同一份预算快照。
    """

    def __init__(
        self,
        repository: OpenSearchRepository,
        query_budget_provider: QueryBudgetProvider = DEFAULT_QUERY_BUDGET_PROVIDER,
        search_strategies: Mapping[SearchMode, SearchStrategy] | None = None,
        query_vector_adapter: QueryVectorAdapter | None = None,
        metrics=None,
    ):
        self.repository = repository
        self.query_budget_provider = query_budget_provider
        self._search_strategies: dict[SearchMode, SearchStrategy] = {
            "boolean": BooleanSearchStrategy(repository, metrics=metrics),
        }
        self._metrics = metrics
        if query_vector_adapter is not None:
            self._search_strategies.update(
                {
                    "vector": VectorSearchStrategy(
                        repository,
                        query_vector_adapter,
                        metrics=metrics,
                    ),
                    "hybrid": HybridSearchStrategy(
                        repository,
                        query_vector_adapter,
                        metrics=metrics,
                    ),
                }
            )
        self._search_strategies.update(search_strategies or {})

    def search(self, request: SearchRequest) -> dict:
        """执行普通列表搜索，并把 OpenSearch 响应映射为公开搜索合同。"""
        profile = (
            "boolean"
            if request.mode == "boolean"
            else semantic_ranking_profile(request)
        )
        with _observe_query_stage(
            self._metrics,
            request,
            stage="end_to_end",
            ranking_profile=profile,
        ):
            # 在本次调用开始时取得不可变预算快照，保证 DSL 构建和分页校验使用同一边界。
            budget = self.query_budget_provider.snapshot()
            if request.mode != "boolean":
                # semantic_text 和布尔 q 共用本次请求冻结的字符预算；校验必须早于
                # 后续向量模型、策略和 OpenSearch 调用。
                budget.validate_query_length(request.semantic_text)
                # 向量结果只能在 top_k 窗口内翻页。
                budget.validate_pagination(page=request.page, page_size=request.page_size)
                if request.offset + request.page_size > request.top_k:
                    raise PaginationOutOfRangeError

            strategy = self._search_strategies.get(request.mode)
            if strategy is None:
                # 未注册模式不得回退到布尔检索。
                raise RuntimeError(f"search strategy is not configured for mode {request.mode}")
            return strategy.search(request, budget=budget)

    def target_rank(self, request: TargetRankRequest) -> dict:
        """计算一个明确目标在当前查询和排序条件下的位置与并列数。

        返回的 status 区分目标不存在、标识符歧义、不在结果集和成功命中；相关性排序
        通过浮点分数计数，日期排序则通过更优/同值文档计数，二者不能共用同一规则。
        """
        budget = self.query_budget_provider.snapshot()
        # 先解析查询，再解析目标专利。这样即使目标不存在，非法 q 也总是稳定返回
        # 查询语法错误，而不会被数据查找顺序掩盖。
        base_dsl = build_search_dsl(
            SearchRequest(q=request.q, ds=request.ds, sort=request.sort, page=1, page_size=1),
            budget=budget,
        )
        identifier = request.target_identifier.strip()
        identifier_field, target, match_count = self.repository.find_target(identifier)
        # 目标可能按内部 patent_id 或公开号命中；两者都先在 Repository 中
        # 做唯一性检查，避免同一排名请求对应多个文档。
        if target is None:
            return {"status": "target_not_found", "in_results": False, "rank": None, "tied_count": 0,
                    "sort_value": None, "target": None}
        if match_count > 1:
            return {"status": "ambiguous_target", "in_results": False, "rank": None, "tied_count": match_count,
                    "sort_value": None, "target": self._target_summary(target)}

        dsl = build_target_rank_dsl(
            request,
            identifier_field,
            target,
            budget=budget,
        )
        target_in_query = self.repository.find_in_query(
            base_dsl["query"], dsl["identity_clause"]
        )
        in_results = target_in_query is not None
        summary = self._target_summary(target)
        if not in_results:
            return {"status": "not_in_results", "in_results": False, "rank": None, "tied_count": 0,
                    "sort_value": None, "target": summary}

        dsl = build_target_rank_dsl(
            request,
            identifier_field,
            target_in_query,
            budget=budget,
        )
        # 相关性排序使用浮点分数的“严格更高”计数；日期排序则分别计算更早/更晚
        # 和同值文档，最终 rank 从 1 开始。_next_float32 用来排除浮点边界上的同分误差。
        if dsl["relevance_sort"]:
            target_score = float(dsl["sort_value"])
            better = self.repository.count_with_min_score(
                dsl["base_query"], _next_float32(target_score)
            )
            not_lower = self.repository.count_with_min_score(dsl["base_query"], target_score)
            tied = max(not_lower - better - 1, 0)
        else:
            better = self.repository.count(dsl["better_query"])
            tied = self.repository.count(dsl["tied_query"])

        return {"status": "matched", "in_results": True, "rank": better + 1, "tied_count": tied,
                "sort_value": dsl["sort_value"], "target": summary}

    @staticmethod
    def _target_summary(hit: dict) -> dict:
        """从候选命中提取目标排名响应允许暴露的最小专利摘要。"""
        # 目标排名返回最小摘要，既供 Console 展示，也避免把完整 source 带回调用方。
        source = hit.get("_source", {})
        return {
            "patent_id": str(source.get("patent_id") or ""),
            "documentNumber": str(source.get("PublicationNumber") or ""),
            "title": str(source.get("Title") or ""),
        }


@contextmanager
def _observe_query_stage(
    metrics,
    request: SearchRequest,
    *,
    stage: str,
    ranking_profile: str,
):
    """用固定请求分类记录一个阶段，观测失败不得改变检索结果。"""
    started = monotonic()
    error = None
    try:
        yield
    except Exception as exc:
        error = exc
        raise
    finally:
        call_metrics(
            metrics,
            "record_query_stage",
            stage=stage,
            mode=request.mode,
            vector_field_count=len(request.vector_fields or []),
            sort_type=(
                "relevance"
                if request.sort in {"relation", "rank", "relevance", "score"}
                else "date"
            ),
            ranking_profile=ranking_profile,
            outcome=_query_stage_outcome(error),
            elapsed_seconds=max(0.0, monotonic() - started),
        )


def _query_stage_outcome(error: Exception | None) -> str:
    if error is None:
        return "success"
    if isinstance(error, (QueryVectorTimeoutError, SearchDependencyTimeoutError)):
        return "timeout"
    if isinstance(
        error,
        (PaginationOutOfRangeError, QueryComplexityError, QuerySyntaxError),
    ):
        return "rejected"
    return "failure"


def _record_opensearch_took(
    metrics,
    request: SearchRequest,
    ranking_profile: str,
    raw: dict,
) -> None:
    took_ms = raw.get("took")
    if (
        type(took_ms) not in (int, float)
        or not math.isfinite(float(took_ms))
        or took_ms < 0
    ):
        return
    call_metrics(
        metrics,
        "record_query_stage",
        stage="opensearch_took",
        mode=request.mode,
        vector_field_count=len(request.vector_fields or []),
        sort_type=(
            "relevance"
            if request.sort in {"relation", "rank", "relevance", "score"}
            else "date"
        ),
        ranking_profile=ranking_profile,
        outcome="success",
        elapsed_seconds=float(took_ms) / 1000,
    )


def _next_float32(value: float) -> float:
    """返回严格大于 ``value`` 的下一个 IEEE-754 float32，用于相关性排名边界。"""
    # OpenSearch/底层评分通常按 IEEE-754 单精度语义比较。取下一个 float32
    # 可把“严格大于目标分数”表达成 min_score，同时不依赖任意 epsilon。
    bits = struct.unpack("!I", struct.pack("!f", value))[0]
    return struct.unpack("!f", struct.pack("!I", bits + 1))[0]


def _generate_query_vectors(
    request: SearchRequest,
    adapter: QueryVectorAdapter,
) -> dict[str, tuple[object, ...]]:
    """按模型和维度分组；同配置在一次请求内只调用一次供应商。"""
    deadline = current_request_deadline()
    if deadline is None:
        raise QueryVectorTimeoutError("query vector request deadline is unavailable")
    grouped: dict[QueryVectorConfig, list[str]] = {}
    for public_field in request.vector_fields or []:
        definition = VECTOR_FIELD_REGISTRY[public_field]
        config = QueryVectorConfig(
            model=definition.embedding_model,
            dimensions=definition.dimensions,
        )
        grouped.setdefault(config, []).append(public_field)

    vectors: dict[str, tuple[object, ...]] = {}
    for config, public_fields in grouped.items():
        result = validate_query_vector_result(
            adapter.generate(
                request.semantic_text,
                config=config,
                deadline=deadline,
            ),
            config=config,
        )
        for public_field in public_fields:
            vectors[public_field] = result.vector
    return vectors


def _map_semantic_response(
    request: SearchRequest,
    raw: dict,
    ranking_profile: str,
    budget: QueryBudget,
) -> dict:
    return map_search_response(
        raw,
        page=request.page,
        page_size=request.page_size,
        budget=budget,
        result_window=request.top_k,
        search_context={
            "mode": request.mode,
            "vector_fields": request.vector_fields,
            "top_k": request.top_k,
            "ranking_profile": ranking_profile,
            "sort": request.sort,
        },
    )
