"""验证搜索应用服务的解析、预算、仓储调用与映射边界。"""

from dataclasses import replace
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import struct
from threading import Thread
from time import monotonic, sleep

import httpx
import pytest

from app.core.deadline import request_deadline
from app.core.exceptions import (
    PaginationOutOfRangeError,
    QueryComplexityError,
    QuerySyntaxError,
    QueryVectorInvalidResponseError,
    QueryVectorTimeoutError,
    SearchDependencyError,
    SearchDependencyTimeoutError,
)
from app.integrations.query_vector import ArkQueryVectorAdapter, QueryVectorResult
from app.mappings.query_field_mapping import VECTOR_FIELD_REGISTRY
from app.mappings.source_fields import SEARCH_SOURCE_FIELDS
from app.query.budget import DEFAULT_QUERY_BUDGET, StaticQueryBudgetProvider
from app.schemas.search import SearchRequest
from app.services.search_service import SearchService


class FakeRepository:
    def __init__(self, raw=None):
        self.body = None
        self.raw = raw or {"hits": {"total": {"value": 0}, "hits": []}}

    def search(self, body):
        self.body = body
        return self.raw


class RecordingStrategy:
    def __init__(self):
        self.calls = []

    def search(self, request, *, budget):
        self.calls.append((request, budget))
        return {"mode": request.mode}


class RecordingSemanticRepository:
    def __init__(self, raw=None, error=None):
        self.raw = raw or {"hits": {"total": {"value": 0}, "hits": []}}
        self.error = error
        self.calls = []

    def search(self, body, *, search_pipeline=None):
        self.calls.append((body, search_pipeline))
        if self.error is not None:
            raise self.error
        return self.raw


class RecordingQueryVectorAdapter:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def generate(self, semantic_text, *, config, deadline):
        self.calls.append((semantic_text, config, deadline))
        if self.error is not None:
            raise self.error
        return QueryVectorResult(
            model=config.model,
            vector=(1.0,) + (0.0,) * (config.dimensions - 1),
        )


class RecordingMetrics:
    def __init__(self):
        self.calls = []

    def record_query_stage(self, **fields):
        self.calls.append(fields)


def test_semantic_search_records_query_vector_opensearch_and_end_to_end_stages():
    metrics = RecordingMetrics()
    service = SearchService(
        RecordingSemanticRepository(
            raw={"took": 7, "hits": {"total": {"value": 0}, "hits": []}}
        ),
        query_vector_adapter=RecordingQueryVectorAdapter(),
        metrics=metrics,
    )
    request = SearchRequest(
        mode="vector",
        semantic_text="敏感语义文本",
        vector_fields=["abstract", "main_claim"],
        top_k=40,
        page_size=10,
        sort="!documentDate",
    )

    with request_deadline(monotonic() + 1):
        service.search(request)

    assert [call["stage"] for call in metrics.calls] == [
        "query_vector",
        "opensearch",
        "opensearch_took",
        "end_to_end",
    ]
    for call in metrics.calls:
        assert call["mode"] == "vector"
        assert call["vector_field_count"] == 2
        assert call["sort_type"] == "date"
        assert call["ranking_profile"] == "patent-vector-rrf-v1-2"
        assert call["outcome"] == "success"
        assert call["elapsed_seconds"] >= 0
        assert "敏感语义文本" not in str(call)
    assert metrics.calls[2]["elapsed_seconds"] == 0.007


def test_query_vector_timeout_records_timeout_without_opensearch_stage():
    metrics = RecordingMetrics()
    service = SearchService(
        RecordingSemanticRepository(),
        query_vector_adapter=RecordingQueryVectorAdapter(
            error=QueryVectorTimeoutError("controlled timeout")
        ),
        metrics=metrics,
    )
    request = SearchRequest(
        mode="vector",
        semantic_text="敏感语义文本",
        vector_fields=["abstract"],
    )

    with request_deadline(monotonic() + 1):
        with pytest.raises(QueryVectorTimeoutError):
            service.search(request)

    assert [(call["stage"], call["outcome"]) for call in metrics.calls] == [
        ("query_vector", "timeout"),
        ("end_to_end", "timeout"),
    ]


def test_boolean_search_records_zero_fields_boolean_profile_and_server_took():
    metrics = RecordingMetrics()
    service = SearchService(
        FakeRepository(raw={"took": 5, "hits": {"total": {"value": 0}, "hits": []}}),
        metrics=metrics,
    )

    service.search(SearchRequest(q="阀门"))

    assert [call["stage"] for call in metrics.calls] == [
        "opensearch",
        "opensearch_took",
        "end_to_end",
    ]
    assert all(call["mode"] == "boolean" for call in metrics.calls)
    assert all(call["vector_field_count"] == 0 for call in metrics.calls)
    assert all(call["ranking_profile"] == "boolean" for call in metrics.calls)
    assert metrics.calls[1]["elapsed_seconds"] == 0.005


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (SearchDependencyTimeoutError("controlled timeout"), "timeout"),
        (SearchDependencyError("controlled failure"), "failure"),
    ],
)
def test_opensearch_failures_record_outcome_without_server_took(error, outcome):
    metrics = RecordingMetrics()
    service = SearchService(
        RecordingSemanticRepository(error=error),
        query_vector_adapter=RecordingQueryVectorAdapter(),
        metrics=metrics,
    )

    with request_deadline(monotonic() + 1):
        with pytest.raises(type(error)):
            service.search(
                SearchRequest(
                    mode="vector",
                    semantic_text="敏感语义文本",
                    vector_fields=["abstract"],
                )
            )

    assert [(call["stage"], call["outcome"]) for call in metrics.calls] == [
        ("query_vector", "success"),
        ("opensearch", outcome),
        ("end_to_end", outcome),
    ]
    assert all(call["stage"] != "opensearch_took" for call in metrics.calls)


def test_local_semantic_rejection_is_observed_before_external_calls():
    metrics = RecordingMetrics()
    repository = RecordingSemanticRepository()
    adapter = RecordingQueryVectorAdapter()
    service = SearchService(
        repository,
        query_vector_adapter=adapter,
        metrics=metrics,
    )

    with request_deadline(monotonic() + 1):
        with pytest.raises(PaginationOutOfRangeError):
            service.search(
                SearchRequest(
                    mode="vector",
                    semantic_text="敏感语义文本",
                    vector_fields=["abstract"],
                    top_k=20,
                    page=2,
                    page_size=20,
                )
            )

    assert adapter.calls == []
    assert repository.calls == []
    assert [(call["stage"], call["outcome"]) for call in metrics.calls] == [
        ("end_to_end", "rejected")
    ]


def test_search_service_builds_dsl_and_maps_response():
    repository = FakeRepository()
    service = SearchService(repository=repository)

    result = service.search(SearchRequest(q="阀门"))

    assert repository.body["size"] == 50
    assert result == {
        "total": 0,
        "page": 1,
        "page_size": 50,
        "total_pages": 0,
        "accessible_pages": 0,
        "next_page": None,
        "took_ms": None,
        "records": [],
    }


@pytest.mark.parametrize(
    ("mode", "payload"),
    [
        (
            "vector",
            {
                "mode": "vector",
                "semantic_text": "阀门",
                "vector_fields": ["abstract"],
            },
        ),
        (
            "hybrid",
            {
                "mode": "hybrid",
                "q": "ipc:H02M",
                "semantic_text": "阀门",
                "vector_fields": ["abstract"],
            },
        ),
    ],
)
def test_search_service_dispatches_non_boolean_modes_without_fallback(mode, payload):
    repository = FakeRepository()
    strategy = RecordingStrategy()
    service = SearchService(repository, search_strategies={mode: strategy})
    request = SearchRequest(**payload)

    result = service.search(request)

    assert result == {"mode": mode}
    assert len(strategy.calls) == 1
    assert strategy.calls[0][0] is request
    assert strategy.calls[0][1] is service.query_budget_provider.snapshot()
    assert repository.body is None


def test_search_service_never_falls_back_to_boolean_for_unregistered_mode_strategy():
    repository = FakeRepository()
    service = SearchService(repository)
    request = SearchRequest(
        mode="vector",
        semantic_text="阀门",
        vector_fields=["abstract"],
    )

    with pytest.raises(RuntimeError, match="not configured for mode vector"):
        service.search(request)

    assert repository.body is None


def test_vector_top_k_window_is_checked_before_strategy_or_repository():
    repository = FakeRepository()
    strategy = RecordingStrategy()
    service = SearchService(repository, search_strategies={"vector": strategy})

    allowed = service.search(
        SearchRequest(
            mode="vector",
            semantic_text="阀门",
            vector_fields=["abstract"],
            top_k=100,
            page=2,
            page_size=50,
        )
    )
    assert allowed == {"mode": "vector"}

    with pytest.raises(PaginationOutOfRangeError):
        service.search(
            SearchRequest(
                mode="vector",
                semantic_text="阀门",
                vector_fields=["abstract"],
                top_k=100,
                page=3,
                page_size=50,
            )
        )

    with pytest.raises(PaginationOutOfRangeError):
        service.search(
            SearchRequest(
                mode="vector",
                semantic_text="阀门",
                vector_fields=["abstract"],
                top_k=105,
                page=3,
                page_size=50,
            )
        )

    assert len(strategy.calls) == 1
    assert repository.body is None


@pytest.mark.parametrize(
    "payload",
    [
        {
            "mode": "vector",
            "semantic_text": "阀门阀",
            "vector_fields": ["abstract"],
        },
        {
            "mode": "hybrid",
            "q": "阀门",
            "semantic_text": "阀门阀",
            "vector_fields": ["abstract"],
        },
    ],
)
def test_semantic_text_uses_frozen_query_budget_before_strategy_or_repository(payload):
    repository = FakeRepository()
    strategy = RecordingStrategy()
    budget = replace(DEFAULT_QUERY_BUDGET, max_query_chars=2)
    service = SearchService(
        repository,
        query_budget_provider=StaticQueryBudgetProvider(budget),
        search_strategies={payload["mode"]: strategy},
    )

    with pytest.raises(QueryComplexityError):
        service.search(SearchRequest(**payload))

    assert strategy.calls == []
    assert repository.body is None


def test_default_vector_strategy_reuses_one_vector_and_caps_fused_total():
    repository = RecordingSemanticRepository(
        {"took": 4, "hits": {"total": {"value": 250}, "hits": []}}
    )
    adapter = RecordingQueryVectorAdapter()
    service = SearchService(repository, query_vector_adapter=adapter)

    with request_deadline(10):
        result = service.search(
            SearchRequest(
                mode="vector",
                semantic_text="阀门",
                vector_fields=["abstract", "main_claim"],
                top_k=100,
                page_size=20,
            )
        )

    assert len(adapter.calls) == 1
    assert adapter.calls[0][0] == "阀门"
    assert adapter.calls[0][2] > monotonic()
    body, pipeline = repository.calls[0]
    assert pipeline == "patent-vector-rrf-v1-2"
    queries = body["query"]["hybrid"]["queries"]
    assert queries[0]["knn"]["AbstractVector1024"]["vector"] == queries[1]["knn"][
        "MainClaimVector1024"
    ]["vector"]
    assert result["total"] == 100
    assert result["total_pages"] == 5
    assert result["accessible_pages"] == 5
    assert result["search_context"] == {
        "mode": "vector",
        "vector_fields": ["abstract", "main_claim"],
        "top_k": 100,
        "ranking_profile": "patent-vector-rrf-v1-2",
        "sort": "relation",
    }


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
@pytest.mark.parametrize(
    "fields", [["abstract"], ["main_claim"], ["independent_claims"],
               ["abstract", "main_claim", "independent_claims"]],
)
@pytest.mark.parametrize("response_dimensions", [1024, 2048])
def test_native_1024_request_routes_new_fields_and_rejects_legacy_response(
    mode, fields, response_dimensions,
):
    calls = []

    def provider(request):
        calls.append(json.loads(request.content))
        vector = [1.0] + [0.0] * (response_dimensions - 1)
        return httpx.Response(200, json={
            "model": "doubao-embedding-vision-250615",
            "data": {"embedding": base64.b64encode(
                struct.pack(f"<{response_dimensions}f", *vector)
            ).decode()},
        })

    repository = RecordingSemanticRepository()
    adapter = ArkQueryVectorAdapter(
        api_url="https://ark.example/embeddings",
        api_key="key",
        model_endpoints={"doubao-embedding-vision-250615": "endpoint"},
        max_connections=1,
        client=httpx.AsyncClient(transport=httpx.MockTransport(provider)),
    )
    request = SearchRequest(
        mode=mode, semantic_text="低泄漏流体控制阀", vector_fields=fields,
        **({"q": "title:阀门"} if mode == "hybrid" else {}),
    )
    try:
        with request_deadline(10):
            if response_dimensions == 2048:
                with pytest.raises(QueryVectorInvalidResponseError):
                    SearchService(repository, query_vector_adapter=adapter).search(request)
            else:
                SearchService(repository, query_vector_adapter=adapter).search(request)
    finally:
        adapter.close()

    assert calls == [{
        "model": "endpoint",
        "input": [{"type": "text", "text": request.semantic_text}],
        "dimensions": 1024, "encoding_format": "base64",
    }]
    if response_dimensions == 2048:
        assert repository.calls == []
        return
    body, _ = repository.calls[0]
    query = body["query"]
    branches = query["hybrid"]["queries"] if "hybrid" in query else [query]
    vector_branches = [branch["knn"] for branch in branches if "knn" in branch]
    expected_fields = {
        "abstract": "AbstractVector1024", "main_claim": "MainClaimVector1024",
        "independent_claims": "IndependentClaimsVector1024",
    }
    assert [list(branch) for branch in vector_branches] == [
        [expected_fields[field]] for field in fields
    ]
    assert all(len(next(iter(branch.values()))["vector"]) == 1024
               for branch in vector_branches)


def test_real_ark_adapter_reuses_same_config_and_routes_distinct_models(monkeypatch):
    import app.services.search_service as search_service_module

    monkeypatch.setattr(
        search_service_module,
        "VECTOR_FIELD_REGISTRY",
        {
            "abstract": replace(
                VECTOR_FIELD_REGISTRY["abstract"],
                dimensions=2,
                embedding_model="model-a",
            ),
            "main_claim": replace(
                VECTOR_FIELD_REGISTRY["main_claim"],
                dimensions=2,
                embedding_model="model-a",
            ),
            "independent_claims": replace(
                VECTOR_FIELD_REGISTRY["independent_claims"],
                dimensions=2,
                embedding_model="model-b",
            ),
        },
    )
    provider_calls = []

    def provider(request):
        endpoint = json.loads(request.content)["model"]
        provider_calls.append(endpoint)
        return httpx.Response(
            200,
            json={
                "model": {"endpoint-a": "model-a", "endpoint-b": "model-b"}[
                    endpoint
                ],
                "data": {"embedding": [1.0, 0.0]},
            },
        )

    repository = RecordingSemanticRepository()
    adapter = ArkQueryVectorAdapter(
        api_url="https://ark.example/embeddings",
        api_key="key",
        model_endpoints={"model-a": "endpoint-a", "model-b": "endpoint-b"},
        max_connections=2,
        client=httpx.AsyncClient(transport=httpx.MockTransport(provider)),
    )
    service = SearchService(repository, query_vector_adapter=adapter)

    try:
        with request_deadline(10):
            service.search(
                SearchRequest(
                    mode="vector",
                    semantic_text="阀门",
                    vector_fields=[
                        "abstract",
                        "main_claim",
                        "independent_claims",
                    ],
                )
            )
    finally:
        adapter.close()

    assert provider_calls == ["endpoint-a", "endpoint-b"]


def test_real_ark_adapter_enforces_wall_clock_deadline_before_opensearch():
    body = json.dumps(
        {
            "model": VECTOR_FIELD_REGISTRY["abstract"].embedding_model,
            "data": {"embedding": [1.0, 0.0]},
        }
    ).encode()

    class DribbleHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length", "0")))
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            for byte in body:
                try:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
                sleep(0.01)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), DribbleHandler)
    server.daemon_threads = True
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    model = VECTOR_FIELD_REGISTRY["abstract"].embedding_model
    adapter = ArkQueryVectorAdapter(
        api_url=f"http://127.0.0.1:{server.server_port}/embeddings",
        api_key="key",
        model_endpoints={model: "endpoint"},
        max_connections=1,
    )
    repository = RecordingSemanticRepository()
    service = SearchService(repository, query_vector_adapter=adapter)
    started = monotonic()

    try:
        with request_deadline(0.08):
            with pytest.raises(QueryVectorTimeoutError):
                service.search(
                    SearchRequest(
                        mode="vector",
                        semantic_text="阀门",
                        vector_fields=["abstract"],
                    )
                )
        elapsed = monotonic() - started
    finally:
        adapter.close()
        server.shutdown()
        server.server_close()
        server_thread.join()

    assert elapsed < 0.3
    assert repository.calls == []


def test_hybrid_strategy_uses_static_pipeline_and_never_falls_back():
    repository = RecordingSemanticRepository()
    adapter = RecordingQueryVectorAdapter()
    service = SearchService(repository, query_vector_adapter=adapter)

    with request_deadline(10):
        result = service.search(
            SearchRequest(
                mode="hybrid",
                q="阀门",
                semantic_text="阀门",
                vector_fields=["abstract"],
                ds="us",
            )
        )

    body, pipeline = repository.calls[0]
    assert pipeline == "patent-hybrid-rrf-v1-1"
    assert body["query"]["hybrid"]["filter"] == {
        "term": {"PublicationCountry": "US"}
    }
    assert result["search_context"]["ranking_profile"] == pipeline

    failing_repository = RecordingSemanticRepository()
    failing = SearchService(
        failing_repository,
        query_vector_adapter=RecordingQueryVectorAdapter(
            QueryVectorTimeoutError("timeout")
        ),
    )
    with request_deadline(10):
        with pytest.raises(QueryVectorTimeoutError):
            failing.search(
                SearchRequest(
                    mode="hybrid",
                    q="阀门",
                    semantic_text="阀门",
                    vector_fields=["abstract"],
                )
            )
    assert failing_repository.calls == []


def test_semantic_strategy_requires_request_deadline_without_resetting_one():
    repository = RecordingSemanticRepository()
    adapter = RecordingQueryVectorAdapter()
    service = SearchService(repository, query_vector_adapter=adapter)

    with pytest.raises(QueryVectorTimeoutError):
        service.search(
            SearchRequest(
                mode="vector",
                semantic_text="阀门",
                vector_fields=["abstract"],
            )
        )

    assert adapter.calls == []
    assert repository.calls == []


def test_hybrid_invalid_boolean_query_stops_before_query_vector_or_opensearch():
    repository = RecordingSemanticRepository()
    adapter = RecordingQueryVectorAdapter()
    service = SearchService(repository, query_vector_adapter=adapter)

    with request_deadline(10):
        with pytest.raises(QuerySyntaxError):
            service.search(
                SearchRequest(
                    mode="hybrid",
                    q="title:()",
                    semantic_text="阀门",
                    vector_fields=["abstract"],
                )
            )

    assert adapter.calls == []
    assert repository.calls == []


def test_boolean_search_characterization_keeps_dsl_sort_pagination_and_response():
    repository = FakeRepository(
        {"took": 7, "hits": {"total": {"value": 5}, "hits": []}}
    )
    service = SearchService(repository=repository)

    result = service.search(
        SearchRequest(
            q="title:(电池 OR battery) AND ipc:H02M",
            ds="US",
            sort="!applicationDate",
            page=2,
            page_size=2,
        )
    )

    assert repository.body == {
        "from": 2,
        "size": 2,
        "_source": list(SEARCH_SOURCE_FIELDS),
        "track_total_hits": True,
        "query": {
            "bool": {
                "must": [
                    {
                        "bool": {
                            "must": [
                                {
                                    "dis_max": {
                                        "queries": [
                                            {
                                                "multi_match": {
                                                    "query": "电池",
                                                    "fields": ["TitleCN"],
                                                }
                                            },
                                            {
                                                "multi_match": {
                                                    "query": "battery",
                                                    "fields": ["TitleEN"],
                                                }
                                            },
                                        ],
                                        "tie_breaker": 0.0,
                                    }
                                },
                                {"term": {"IPCListBase": "H02M"}},
                            ]
                        }
                    }
                ],
                "filter": [{"term": {"PublicationCountry": "US"}}],
            }
        },
        "sort": [{"ApplicationDate": {"order": "desc"}}],
    }
    assert result == {
        "total": 5,
        "page": 2,
        "page_size": 2,
        "total_pages": 3,
        "accessible_pages": 3,
        "next_page": 3,
        "took_ms": 7,
        "records": [],
    }


@pytest.mark.parametrize(
    ("search_request", "error_type"),
    [
        (SearchRequest(q="title:()"), QuerySyntaxError),
        (
            SearchRequest(q="阀门", page=1_001, page_size=10),
            PaginationOutOfRangeError,
        ),
    ],
)
def test_boolean_search_characterization_rejects_before_repository(
    search_request,
    error_type,
):
    repository = FakeRepository()
    service = SearchService(repository=repository)

    with pytest.raises(error_type):
        service.search(search_request)

    assert repository.body is None


class FailingRepository:
    def search(self, body):
        raise RuntimeError("opensearch connection refused")


def test_search_service_does_not_hide_programming_failure():
    service = SearchService(repository=FailingRepository())

    with pytest.raises(RuntimeError, match="connection refused"):
        service.search(SearchRequest(q="阀门"))
