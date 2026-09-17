"""向量与混合检索 DSL；布尔子查询仍由现有 builder 生成。"""

from collections.abc import Mapping, Sequence

from app.mappings.query_field_mapping import VECTOR_FIELD_REGISTRY
from app.mappings.source_fields import SEARCH_SOURCE_FIELDS
from app.query.budget import QueryBudget
from app.query.dsl_builder import build_search_dsl, build_search_sort
from app.schemas.search import SearchRequest


SINGLE_VECTOR_PROFILE = "patent-knn-cosine-v1"


def build_vector_search_dsl(
    request: SearchRequest,
    vectors: Mapping[str, Sequence[float]],
) -> tuple[dict, str | None, str]:
    """构造单字段 k-NN 或多字段 Hybrid Query 及其固定排名 profile。"""
    fields = request.vector_fields or []
    queries = [_knn_query(field, vectors[field], request.top_k) for field in fields]
    if len(queries) == 1:
        query = queries[0]
        dataset_filter = _dataset_filter(request.ds)
        if dataset_filter is not None:
            query["knn"][VECTOR_FIELD_REGISTRY[fields[0]].opensearch_field][
                "filter"
            ] = dataset_filter
        pipeline = None
        profile = semantic_ranking_profile(request)
    else:
        hybrid = {
            "queries": queries,
            "pagination_depth": request.top_k,
        }
        dataset_filter = _dataset_filter(request.ds)
        if dataset_filter is not None:
            hybrid["filter"] = dataset_filter
        query = {"hybrid": hybrid}
        profile = semantic_ranking_profile(request)
        pipeline = profile
    return _search_body(request, query), pipeline, profile


def build_hybrid_search_dsl(
    request: SearchRequest,
    vectors: Mapping[str, Sequence[float]],
    *,
    boolean_query: dict,
) -> tuple[dict, str, str]:
    """组合现有布尔子查询与向量子查询，ds 仅放在全局 hybrid.filter。"""
    fields = request.vector_fields or []
    hybrid = {
        "queries": [
            boolean_query,
            *[_knn_query(field, vectors[field], request.top_k) for field in fields],
        ],
        "pagination_depth": request.top_k,
    }
    dataset_filter = _dataset_filter(request.ds)
    if dataset_filter is not None:
        hybrid["filter"] = dataset_filter
    profile = semantic_ranking_profile(request)
    return _search_body(request, {"hybrid": hybrid}), profile, profile


def semantic_ranking_profile(request: SearchRequest) -> str:
    """返回当前已校验语义请求对应的固定排名 profile。"""
    field_count = len(request.vector_fields or [])
    if request.mode == "vector":
        if field_count == 1:
            return SINGLE_VECTOR_PROFILE
        return f"patent-vector-rrf-v1-{field_count}"
    if request.mode == "hybrid":
        return f"patent-hybrid-rrf-v1-{field_count}"
    raise ValueError("semantic ranking profile requires vector or hybrid mode")


def build_hybrid_boolean_query(
    request: SearchRequest,
    *,
    budget: QueryBudget,
) -> dict:
    """在请求向量前完成现有布尔语法和复杂度校验。"""
    return build_search_dsl(
        SearchRequest(
            q=request.q,
            ds="all",
            sort=request.sort,
            page=1,
            page_size=1,
        ),
        budget=budget,
    )["query"]


def _search_body(request: SearchRequest, query: dict) -> dict:
    return {
        "from": request.offset,
        "size": request.page_size,
        "_source": list(SEARCH_SOURCE_FIELDS),
        "track_total_hits": request.top_k,
        "query": query,
        "sort": build_search_sort(request.sort),
    }


def _knn_query(
    public_field: str,
    vector: Sequence[float],
    top_k: int,
) -> dict:
    definition = VECTOR_FIELD_REGISTRY[public_field]
    return {
        "knn": {
            definition.opensearch_field: {
                "vector": list(vector),
                "k": top_k,
            }
        }
    }


def _dataset_filter(ds: str) -> dict | None:
    if ds.lower() == "all":
        return None
    return {"term": {"PublicationCountry": ds.upper()}}
