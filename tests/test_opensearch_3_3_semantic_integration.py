"""Opt-in controlled OpenSearch 3.3 execution test for Issue 75 checkpoint 2.

Run only against an isolated loopback/tunnel endpoint:
SEMANTIC_OPENSEARCH_URL=http://127.0.0.1:19200 \
SEMANTIC_OPENSEARCH_ALLOW_WRITES=1 pytest -q -s <this file>
"""

import json
import os
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest

from app.query.budget import DEFAULT_QUERY_BUDGET
from app.query.dsl_builder import build_search_dsl
from app.query.semantic_dsl_builder import (
    build_hybrid_boolean_query,
    build_hybrid_search_dsl,
    build_vector_search_dsl,
)
from app.schemas.search import SearchRequest


URL = os.getenv("SEMANTIC_OPENSEARCH_URL", "")
ALLOW_WRITES = os.getenv("SEMANTIC_OPENSEARCH_ALLOW_WRITES") == "1"
if not URL or not ALLOW_WRITES:
    pytest.skip("controlled OpenSearch 3.3 endpoint not configured", allow_module_level=True)
if urlsplit(URL).hostname not in {"127.0.0.1", "localhost", "::1"}:
    raise RuntimeError("semantic integration writes are restricted to loopback endpoints")


@pytest.fixture(scope="module")
def controlled_opensearch():
    with ExitStack() as cleanup:
        client = httpx.Client(base_url=URL, timeout=30)
        cleanup.callback(client.close)
        info = client.get("/")
        info.raise_for_status()
        cluster_info = info.json()
        if cluster_info.get("cluster_name") != "issue75-semantic-fixture":
            raise RuntimeError("semantic integration requires the isolated fixture cluster")
        version = cluster_info["version"]["number"]
        assert version.startswith("3.3."), version

        index = f"issue75-semantic-fixture-{uuid4().hex[:12]}"
        pipelines = json.loads(
            (
                Path(__file__).parents[1]
                / "deployment/opensearch/search_pipelines_v1.json"
            ).read_text()
        )["pipelines"]
        for pipeline_id, definition in pipelines.items():
            response = client.put(f"/_search/pipeline/{pipeline_id}", json=definition)
            response.raise_for_status()
            cleanup.callback(
                _delete_controlled_resource,
                client,
                f"/_search/pipeline/{pipeline_id}",
            )
        response = client.put(
            f"/{index}",
            json={
                "settings": {
                    "index.knn": True,
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                },
                "mappings": {
                    "properties": {
                        "patent_id": {"type": "keyword"},
                        "Title": {"type": "text"},
                        "TitleCN": {"type": "text"},
                        "PublicationCountry": {"type": "keyword"},
                        "ApplicationDate": {"type": "date"},
                        "PublicationDate": {"type": "date"},
                        "AbstractVector1024": _vector_mapping(),
                        "MainClaimVector1024": _vector_mapping(),
                    }
                },
            },
        )
        response.raise_for_status()
        cleanup.callback(_delete_controlled_resource, client, f"/{index}")
        _bulk_index(client, index, _documents())
        yield client, index, version


def test_real_opensearch_3_3_knn_hybrid_rrf_pagination_and_date_sort(
    controlled_opensearch,
):
    client, index, version = controlled_opensearch
    vectors = {"abstract": (1.0, 0.0, 0.0), "main_claim": (0.0, 1.0, 0.0)}

    single_body, single_pipeline, _ = build_vector_search_dsl(
        SearchRequest(
            mode="vector",
            semantic_text="query",
            vector_fields=["abstract"],
            top_k=4,
            ds="cn",
            page_size=4,
        ),
        vectors,
    )
    single = _search(client, index, single_body, single_pipeline)
    single_ids = _ids(single)
    assert single_pipeline is None
    assert "us-perfect" not in single_ids
    assert single_ids[0] in {"a", "union-vector"}
    assert all(hit["_score"] is not None for hit in single["hits"]["hits"])

    multi_request = SearchRequest(
        mode="vector",
        semantic_text="query",
        vector_fields=["abstract", "main_claim"],
        top_k=4,
        ds="cn",
        page_size=4,
    )
    multi_body, multi_pipeline, _ = build_vector_search_dsl(multi_request, vectors)
    multi = _search(client, index, multi_body, multi_pipeline)
    multi_ids = _ids(multi)
    assert multi_pipeline == "patent-vector-rrf-v1-2"
    assert multi_ids[0] == "a"
    assert len(multi_ids) == len(set(multi_ids))
    assert "us-perfect" not in multi_ids

    union_request = SearchRequest(
        mode="hybrid",
        q="title:并集验证",
        semantic_text="query",
        vector_fields=["abstract"],
        top_k=4,
        ds="cn",
        page_size=4,
    )
    union_vector_body, _, _ = build_vector_search_dsl(
        SearchRequest(
            mode="vector",
            semantic_text="query",
            vector_fields=["abstract"],
            top_k=4,
            ds="cn",
            page_size=4,
        ),
        vectors,
    )
    union_boolean_body = build_search_dsl(
        SearchRequest(q="title:并集验证", ds="cn", page_size=4),
        budget=DEFAULT_QUERY_BUDGET,
    )
    union_body, union_pipeline, _ = _build_hybrid_search_dsl(
        union_request,
        vectors,
    )
    union_vector_ids = _ids(_search(client, index, union_vector_body, None))
    union_boolean_ids = _ids(_search(client, index, union_boolean_body, None))
    union_ids = _ids(_search(client, index, union_body, union_pipeline))
    assert "union-boolean" in union_boolean_ids
    assert "union-boolean" not in union_vector_ids
    assert "union-vector" in union_vector_ids
    assert "union-vector" not in union_boolean_ids
    assert "union-both" in union_boolean_ids
    assert "union-both" in union_vector_ids
    assert {"union-boolean", "union-vector", "union-both"} <= set(union_ids)
    assert union_ids.count("union-both") == 1

    hybrid_request = SearchRequest(
        mode="hybrid",
        q="title:逆变器",
        semantic_text="query",
        vector_fields=["abstract", "main_claim"],
        top_k=4,
        ds="cn",
        page_size=2,
    )
    page_one_body, pipeline, _ = _build_hybrid_search_dsl(
        hybrid_request,
        vectors,
    )
    page_two_body, _, _ = _build_hybrid_search_dsl(
        hybrid_request.model_copy(update={"page": 2}),
        vectors,
    )
    all_body, _, _ = _build_hybrid_search_dsl(
        hybrid_request.model_copy(update={"page_size": 4}),
        vectors,
    )
    page_one = _ids(_search(client, index, page_one_body, pipeline))
    page_two = _ids(_search(client, index, page_two_body, pipeline))
    all_ids = _ids(_search(client, index, all_body, pipeline))
    assert pipeline == "patent-hybrid-rrf-v1-2"
    assert page_one + page_two == all_ids
    assert not set(page_one) & set(page_two)
    assert page_one_body["query"]["hybrid"]["pagination_depth"] == 4
    assert page_two_body["query"]["hybrid"]["pagination_depth"] == 4
    assert len(all_ids) == len(set(all_ids))

    date_body, date_pipeline, _ = _build_hybrid_search_dsl(
        hybrid_request.model_copy(
            update={"sort": "!applicationDate", "page_size": 4}
        ),
        vectors,
    )
    date_result = _search(client, index, date_body, date_pipeline)
    dates = [hit["_source"]["ApplicationDate"] for hit in date_result["hits"]["hits"]]
    assert dates == sorted(dates, reverse=True)
    assert all(hit["_score"] is None for hit in date_result["hits"]["hits"])

    missing = client.post(
        f"/{index}/_search",
        params={"search_pipeline": "issue75-missing-pipeline"},
        json=multi_body,
    )
    assert missing.status_code >= 400

    print(
        json.dumps(
            {
                "version": version,
                "index": index,
                "single_knn_ids": single_ids,
                "multi_rrf_ids": multi_ids,
                "union_boolean_branch": union_boolean_ids,
                "union_vector_branch": union_vector_ids,
                "union_hybrid": union_ids,
                "hybrid_page_1": page_one,
                "hybrid_page_2": page_two,
                "hybrid_all": all_ids,
                "date_order": dates,
                "date_scores": [
                    hit["_score"] for hit in date_result["hits"]["hits"]
                ],
                "missing_pipeline_status": missing.status_code,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def _vector_mapping() -> dict:
    return {
        "type": "knn_vector",
        "dimension": 3,
        "method": {
            "name": "hnsw",
            "engine": "lucene",
            "space_type": "cosinesimil",
        },
    }


def _documents() -> list[dict]:
    return [
        _document("a", "逆变器 高频", "CN", "2024-01-01", [1, 0, 0], [0, 1, 0]),
        _document("b", "普通", "CN", "2023-01-01", [0.95, 0.05, 0], [-1, 0, 0]),
        _document("c", "普通", "CN", "2022-01-01", [-1, 0, 0], [0, 0.95, 0.05]),
        _document("d", "逆变器", "CN", "2021-01-01", [0.7, 0.7, 0], [0.7, 0.7, 0]),
        _document("e", "普通", "CN", "2020-01-01", [0, 0, 1], [0, 0, 1]),
        _document(
            "union-boolean",
            "并集验证",
            "CN",
            "2019-01-01",
            [-1, 0, 0],
            [-1, 0, 0],
        ),
        _document(
            "union-vector",
            "普通",
            "CN",
            "2018-01-01",
            [1, 0, 0],
            [-1, 0, 0],
        ),
        _document(
            "union-both",
            "并集验证",
            "CN",
            "2017-01-01",
            [0.99, 0.01, 0],
            [-1, 0, 0],
        ),
        _document("us-perfect", "逆变器", "US", "2025-01-01", [1, 0, 0], [0, 1, 0]),
    ]


def _document(doc_id, title, country, application_date, abstract, main_claim):
    return {
        "patent_id": doc_id,
        "Title": title,
        "TitleCN": title,
        "PublicationCountry": country,
        "ApplicationDate": application_date,
        "PublicationDate": application_date,
        "AbstractVector1024": abstract,
        "MainClaimVector1024": main_claim,
    }


def _bulk_index(client: httpx.Client, index: str, documents: list[dict]) -> None:
    lines = []
    for document in documents:
        lines.append(json.dumps({"index": {"_index": index, "_id": document["patent_id"]}}))
        lines.append(json.dumps(document, ensure_ascii=False))
    response = client.post(
        "/_bulk",
        params={"refresh": "true"},
        content="\n".join(lines) + "\n",
        headers={"Content-Type": "application/x-ndjson"},
    )
    response.raise_for_status()
    assert response.json()["errors"] is False


def _search(client, index, body, pipeline):
    params = {"allow_partial_search_results": "false"}
    if pipeline is not None:
        params["search_pipeline"] = pipeline
    response = client.post(f"/{index}/_search", params=params, json=body)
    response.raise_for_status()
    result = response.json()
    assert result["timed_out"] is False
    assert result["_shards"]["failed"] == 0
    return result


def _delete_controlled_resource(client: httpx.Client, path: str) -> None:
    response = client.delete(path)
    if response.status_code != 404:
        response.raise_for_status()


def _build_hybrid_search_dsl(request, vectors):
    return build_hybrid_search_dsl(
        request,
        vectors,
        boolean_query=build_hybrid_boolean_query(
            request,
            budget=DEFAULT_QUERY_BUDGET,
        ),
    )


def _ids(result: dict) -> list[str]:
    return [hit["_id"] for hit in result["hits"]["hits"]]
