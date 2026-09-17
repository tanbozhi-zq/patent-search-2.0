"""验证 vector/hybrid 的 OpenSearch 3.3 DSL 与静态 RRF profiles。"""

import json
from pathlib import Path

import pytest

from app.mappings.source_fields import SEARCH_SOURCE_FIELDS
from app.query.budget import DEFAULT_QUERY_BUDGET
from app.query.semantic_dsl_builder import (
    build_hybrid_boolean_query,
    build_hybrid_search_dsl,
    build_vector_search_dsl,
)
from app.schemas.search import SearchRequest


def test_single_vector_uses_knn_global_dataset_filter_and_no_pipeline():
    request = SearchRequest(
        mode="vector",
        semantic_text="阀门",
        vector_fields=["abstract"],
        top_k=20,
        ds="us",
        sort="documentDate",
        page=2,
        page_size=5,
    )

    body, pipeline, profile = build_vector_search_dsl(
        request,
        {"abstract": (1.0, 0.0)},
    )

    assert body == {
        "from": 5,
        "size": 5,
        "_source": list(SEARCH_SOURCE_FIELDS),
        "track_total_hits": 20,
        "query": {
            "knn": {
                "AbstractVector1024": {
                    "vector": [1.0, 0.0],
                    "k": 20,
                    "filter": {"term": {"PublicationCountry": "US"}},
                }
            }
        },
        "sort": [{"PublicationDate": {"order": "asc"}}],
    }
    assert pipeline is None
    assert profile == "patent-knn-cosine-v1"


def test_multi_vector_uses_one_hybrid_branch_per_field_and_fixed_depth():
    request = SearchRequest(
        mode="vector",
        semantic_text="阀门",
        vector_fields=["abstract", "main_claim"],
        top_k=100,
        ds="cn",
        page=3,
        page_size=20,
    )

    body, pipeline, profile = build_vector_search_dsl(
        request,
        {"abstract": (1.0, 0.0), "main_claim": (1.0, 0.0)},
    )

    hybrid = body["query"]["hybrid"]
    assert body["from"] == 40
    assert body["size"] == 20
    assert body["track_total_hits"] == 100
    assert hybrid == {
        "queries": [
            {"knn": {"AbstractVector1024": {"vector": [1.0, 0.0], "k": 100}}},
            {"knn": {"MainClaimVector1024": {"vector": [1.0, 0.0], "k": 100}}},
        ],
        "pagination_depth": 100,
        "filter": {"term": {"PublicationCountry": "CN"}},
    }
    assert pipeline == profile == "patent-vector-rrf-v1-2"


def test_hybrid_keeps_not_boolean_local_and_dataset_filter_global():
    request = SearchRequest(
        mode="hybrid",
        q="title:阀门 AND NOT applicant:甲公司",
        semantic_text="阀门",
        vector_fields=["abstract", "main_claim"],
        top_k=80,
        ds="cn",
        page_size=20,
    )

    body, pipeline, profile = _build_hybrid_search_dsl(
        request,
        {"abstract": (1.0, 0.0), "main_claim": (0.0, 1.0)},
    )

    hybrid = body["query"]["hybrid"]
    assert body["track_total_hits"] == 80
    boolean_json = json.dumps(hybrid["queries"][0], ensure_ascii=False)
    assert "must_not" in boolean_json
    assert "甲公司" in boolean_json
    assert "PublicationCountry" not in boolean_json
    assert hybrid["filter"] == {"term": {"PublicationCountry": "CN"}}
    assert len(hybrid["queries"]) == 3
    assert hybrid["pagination_depth"] == 80
    assert pipeline == profile == "patent-hybrid-rrf-v1-2"


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("relation", ["_score"]),
        ("rank", ["_score"]),
        ("relevance", ["_score"]),
        ("score", ["_score"]),
        ("applicationDate", [{"ApplicationDate": {"order": "asc"}}]),
        ("!applicationDate", [{"ApplicationDate": {"order": "desc"}}]),
        ("documentDate", [{"PublicationDate": {"order": "asc"}}]),
        ("!documentDate", [{"PublicationDate": {"order": "desc"}}]),
    ],
)
def test_semantic_modes_reuse_exact_existing_sort_contract(sort, expected):
    vector, _, _ = build_vector_search_dsl(
        SearchRequest(
            mode="vector",
            semantic_text="阀门",
            vector_fields=["abstract"],
            sort=sort,
        ),
        {"abstract": (1.0, 0.0)},
    )
    hybrid, _, _ = _build_hybrid_search_dsl(
        SearchRequest(
            mode="hybrid",
            q="阀门",
            semantic_text="阀门",
            vector_fields=["abstract"],
            sort=sort,
        ),
        {"abstract": (1.0, 0.0)},
    )

    assert vector["sort"] == expected
    assert hybrid["sort"] == expected
    assert len(vector["sort"]) == 1
    assert len(hybrid["sort"]) == 1


def test_versioned_rrf_pipelines_have_exact_branch_weights():
    asset = json.loads(
        (
            Path(__file__).parents[1]
            / "deployment/opensearch/search_pipelines_v1.json"
        ).read_text()
    )
    pipelines = asset["pipelines"]
    assert asset["version"] == 1
    assert set(pipelines) == {
        *(f"patent-vector-rrf-v1-{count}" for count in range(2, 6)),
        *(f"patent-hybrid-rrf-v1-{count}" for count in range(1, 5)),
    }

    for vector_count in range(2, 6):
        weights = _weights(pipelines[f"patent-vector-rrf-v1-{vector_count}"])
        assert len(weights) == vector_count
        assert sum(weights) == pytest.approx(1.0)
        assert weights == pytest.approx([1 / vector_count] * vector_count)

    for vector_count in range(1, 5):
        weights = _weights(pipelines[f"patent-hybrid-rrf-v1-{vector_count}"])
        assert len(weights) == vector_count + 1
        assert sum(weights) == pytest.approx(1.0)
        assert weights[0] == 0.5
        assert weights[1:] == pytest.approx([0.5 / vector_count] * vector_count)


def _weights(pipeline: dict) -> list[float]:
    ranker = pipeline["phase_results_processors"][0]["score-ranker-processor"]
    assert ranker["combination"]["technique"] == "rrf"
    assert ranker["combination"]["rank_constant"] == 60
    return ranker["combination"]["parameters"]["weights"]


def _build_hybrid_search_dsl(request, vectors):
    return build_hybrid_search_dsl(
        request,
        vectors,
        boolean_query=build_hybrid_boolean_query(
            request,
            budget=DEFAULT_QUERY_BUDGET,
        ),
    )
