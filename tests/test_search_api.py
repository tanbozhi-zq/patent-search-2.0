"""验证搜索请求模型、端点响应、查询语法失败与下游异常的契约。"""

from jsonschema import Draft202012Validator
from pydantic import ValidationError
import pytest

import app.schemas.search as search_schema
from app.mappings.query_field_mapping import VECTOR_FIELD_REGISTRY, VectorFieldDefinition
from app.query.budget import HARD_QUERY_BUDGET
from app.schemas.search import DEFAULT_TOP_K, MAX_TOP_K, SearchRequest, TargetRankRequest


def _search_mode_schema(mode: str) -> dict:
    schema = SearchRequest.model_json_schema()
    ref = schema["discriminator"]["mapping"][mode]
    return schema["$defs"][ref.rsplit("/", 1)[-1]]


def test_search_request_defaults():
    request = SearchRequest(q="阀门")

    assert request.mode == "boolean"
    assert request.q == "阀门"
    assert request.semantic_text is None
    assert request.vector_fields is None
    assert request.top_k is None
    assert request.ds == "cn"
    assert request.sort == "relation"
    assert request.page == 1
    assert request.page_size == 50
    assert request.highlight == 0
    assert request.offset == 0


def test_search_request_rejects_invalid_page_size():
    with pytest.raises(ValidationError):
        SearchRequest(
            q="阀门",
            page_size=HARD_QUERY_BUDGET.max_page_size + 1,
        )


def test_request_model_constraints_share_hard_query_budget_source():
    boolean_properties = _search_mode_schema("boolean")["properties"]
    vector_properties = _search_mode_schema("vector")["properties"]
    target_rank_properties = TargetRankRequest.model_json_schema()["properties"]

    assert (
        boolean_properties["q"]["maxLength"]
        == HARD_QUERY_BUDGET.max_query_chars
    )
    assert (
        vector_properties["semantic_text"]["maxLength"]
        == HARD_QUERY_BUDGET.max_query_chars
    )
    assert (
        target_rank_properties["q"]["maxLength"]
        == HARD_QUERY_BUDGET.max_query_chars
    )
    assert (
        boolean_properties["page_size"]["maximum"]
        == HARD_QUERY_BUDGET.max_page_size
    )


@pytest.mark.parametrize(
    ("payload", "mode"),
    [
        ({"q": "阀门"}, "boolean"),
        ({"mode": "boolean", "q": "阀门"}, "boolean"),
        (
            {
                "mode": "vector",
                "semantic_text": "  提高逆变器效率  ",
                "vector_fields": ["abstract"],
            },
            "vector",
        ),
        (
            {
                "mode": "hybrid",
                "q": "ipc:H02M",
                "semantic_text": "提高逆变器效率",
                "vector_fields": ["abstract", "main_claim"],
            },
            "hybrid",
        ),
    ],
)
def test_search_request_accepts_mode_matrix(payload, mode):
    request = SearchRequest(**payload)

    assert request.mode == mode
    assert request.top_k == (None if mode == "boolean" else DEFAULT_TOP_K)
    if request.semantic_text is not None:
        assert request.semantic_text == "提高逆变器效率"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"mode": "boolean", "q": "阀门", "semantic_text": None},
        {"mode": "boolean", "q": "阀门", "vector_fields": None},
        {"mode": "boolean", "q": "阀门", "top_k": 100},
        {
            "mode": "vector",
            "q": None,
            "semantic_text": "阀门",
            "vector_fields": ["abstract"],
        },
        {
            "mode": "vector",
            "q": "阀门",
            "semantic_text": "阀门",
            "vector_fields": ["abstract"],
        },
        {"mode": "vector", "vector_fields": ["abstract"]},
        {"mode": "vector", "semantic_text": "   ", "vector_fields": ["abstract"]},
        {
            "mode": "vector",
            "semantic_text": " " + "向" * HARD_QUERY_BUDGET.max_query_chars,
            "vector_fields": ["abstract"],
        },
        {"mode": "vector", "semantic_text": "阀门"},
        {"mode": "vector", "semantic_text": "阀门", "vector_fields": []},
        {
            "mode": "vector",
            "semantic_text": "阀门",
            "vector_fields": ["abstract", "abstract"],
        },
        {"mode": "vector", "semantic_text": "阀门", "vector_fields": ["unknown"]},
        {
            "mode": "vector",
            "semantic_text": "阀门",
            "vector_fields": ["AbstractVector"],
        },
        {
            "mode": "hybrid",
            "semantic_text": "阀门",
            "vector_fields": ["abstract"],
        },
        {"mode": "hybrid", "q": "阀门", "vector_fields": ["abstract"]},
        {"mode": "hybrid", "q": "阀门", "semantic_text": "阀门"},
        {"mode": "semantic", "semantic_text": "阀门", "vector_fields": ["abstract"]},
    ],
)
def test_search_request_rejects_invalid_mode_field_combinations(payload):
    with pytest.raises(ValidationError):
        SearchRequest(**payload)


@pytest.mark.parametrize("top_k", [0, MAX_TOP_K + 1, True, "100"])
def test_search_request_rejects_top_k_outside_hard_bounds(top_k):
    with pytest.raises(ValidationError):
        SearchRequest(
            mode="vector",
            semantic_text="阀门",
            vector_fields=["abstract"],
            top_k=top_k,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"q": "阀门"},
        {
            "mode": "vector",
            "semantic_text": "提高逆变器效率",
            "vector_fields": ["abstract"],
        },
        {
            "mode": "hybrid",
            "q": "ipc:H02M",
            "semantic_text": "提高逆变器效率",
            "vector_fields": ["abstract", "main_claim"],
        },
    ],
)
def test_search_request_json_schema_and_model_dump_round_trip(payload):
    schema = SearchRequest.model_json_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)

    validator.validate(payload)
    request = SearchRequest(**payload)
    dumped = request.model_dump()
    validator.validate(dumped)

    assert SearchRequest.model_validate(dumped) == request
    if request.mode == "boolean":
        assert {"semantic_text", "vector_fields", "top_k"}.isdisjoint(dumped)
    else:
        assert dumped["top_k"] == DEFAULT_TOP_K


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"mode": "vector"},
        {"mode": "boolean", "q": "阀门", "top_k": 100},
        {
            "mode": "vector",
            "q": None,
            "semantic_text": "阀门",
            "vector_fields": ["abstract"],
        },
        {
            "mode": "hybrid",
            "semantic_text": "阀门",
            "vector_fields": ["abstract"],
        },
        {
            "mode": "vector",
            "semantic_text": "   ",
            "vector_fields": ["abstract"],
        },
        {
            "mode": "vector",
            "semantic_text": " " + "向" * HARD_QUERY_BUDGET.max_query_chars,
            "vector_fields": ["abstract"],
        },
    ],
)
def test_search_request_json_schema_rejects_runtime_invalid_mode_shapes(payload):
    validator = Draft202012Validator(SearchRequest.model_json_schema())

    assert not validator.is_valid(payload)

    with pytest.raises(ValidationError):
        SearchRequest(**payload)


def test_search_request_preserves_flat_copy_and_mapping_compatibility():
    request = SearchRequest(q="阀门")

    copied = request.model_copy(update={"page": 2})

    assert copied.page == 2
    assert copied.q == "阀门"
    assert dict(request) == request.model_dump()


def test_search_request_repr_does_not_expose_semantic_text():
    semantic_text = "semantic-secret-b7e4"
    request = SearchRequest(
        mode="vector",
        semantic_text=semantic_text,
        vector_fields=["abstract"],
    )

    assert semantic_text not in repr(request)


def test_vector_field_validation_follows_registry_and_backend_branch_limit(monkeypatch):
    registry = dict(VECTOR_FIELD_REGISTRY)
    for number in range(4, 7):
        name = f"future_{number}"
        registry[name] = VectorFieldDefinition(
            public_name=name,
            opensearch_field=f"FutureVector{number}",
            dimensions=2048,
            space_type="cosinesimil",
            embedding_model="doubao-embedding-vision-250615",
        )
    monkeypatch.setattr(search_schema, "VECTOR_FIELD_REGISTRY", registry)

    vector_five = ["abstract", "main_claim", "independent_claims", "future_4", "future_5"]
    hybrid_four = ["abstract", "main_claim", "independent_claims", "future_4"]
    assert SearchRequest(
        mode="vector", semantic_text="阀门", vector_fields=vector_five
    ).vector_fields == vector_five
    assert SearchRequest(
        mode="hybrid", q="阀门", semantic_text="阀门", vector_fields=hybrid_four
    ).vector_fields == hybrid_four

    with pytest.raises(ValidationError):
        SearchRequest(
            mode="vector",
            semantic_text="阀门",
            vector_fields=[*vector_five, "future_6"],
        )
    with pytest.raises(ValidationError):
        SearchRequest(
            mode="hybrid",
            q="阀门",
            semantic_text="阀门",
            vector_fields=[*hybrid_four, "future_5"],
        )


def test_search_openapi_exposes_generic_vector_contract_without_field_enum():
    schema = SearchRequest.model_json_schema()
    boolean = _search_mode_schema("boolean")
    vector = _search_mode_schema("vector")
    hybrid = _search_mode_schema("hybrid")

    assert schema["discriminator"]["propertyName"] == "mode"
    assert set(schema["discriminator"]["mapping"]) == {"boolean", "vector", "hybrid"}
    assert len(schema["oneOf"]) == 3
    assert all(
        branch["additionalProperties"] is False
        for branch in (boolean, vector, hybrid)
    )
    assert boolean["required"] == ["q"]
    assert boolean["properties"]["mode"]["default"] == "boolean"
    assert {"semantic_text", "vector_fields", "top_k"}.isdisjoint(
        boolean["properties"]
    )
    assert set(vector["required"]) == {"mode", "semantic_text", "vector_fields"}
    assert "q" not in vector["properties"]
    assert set(hybrid["required"]) == {
        "mode",
        "q",
        "semantic_text",
        "vector_fields",
    }
    assert vector["properties"]["vector_fields"]["maxItems"] == 5
    assert hybrid["properties"]["vector_fields"]["maxItems"] == 4
    assert "enum" not in vector["properties"]["vector_fields"]["items"]
    assert vector["properties"]["top_k"] == {
        "default": 100,
        "maximum": 1000,
        "minimum": 1,
        "title": "Top K",
        "type": "integer",
    }
    assert hybrid["properties"]["top_k"] == vector["properties"]["top_k"]

    openapi_schema = app.openapi()["components"]["schemas"]["SearchRequest"]
    assert openapi_schema["discriminator"]["propertyName"] == "mode"
    assert len(openapi_schema["oneOf"]) == 3


@pytest.mark.parametrize(
    "sort",
    [
        "relation",
        "rank",
        "relevance",
        "score",
        "!applicationDate",
        "applicationDate",
        "!documentDate",
        "documentDate",
    ],
)
def test_search_request_accepts_stage_12_sort_values(sort):
    assert SearchRequest(q="阀门", sort=sort).sort == sort


def test_search_request_rejects_unknown_sort_value():
    with pytest.raises(ValidationError):
        SearchRequest(q="阀门", sort="unknown")


from app.api.search import get_search_service
from app.core.exceptions import OpenSearchQueryError
from app.core.security import require_api_key
from app.main import app
from app.services.search_service import SearchService


class FakeSearchService:
    def search(self, request):
        return {
            "total": 0,
            "page": request.page,
            "page_size": request.page_size,
            "total_pages": 0,
            "accessible_pages": 0,
            "next_page": None,
            "took_ms": None,
            "records": [],
        }


class OpenSearchFailingService:
    def search(self, request):
        raise OpenSearchQueryError("OpenSearch 查询异常")


class ExplodingSearchService:
    def search(self, request):
        raise AssertionError("invalid mode fields must be rejected before SearchService")


def test_search_endpoint_returns_vendor_like_shape(client):
    app.dependency_overrides[get_search_service] = lambda: FakeSearchService()
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        response = client().post("/api/patent/search", json={"q": "阀门"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "total": 0,
        "page": 1,
        "page_size": 50,
        "total_pages": 0,
        "accessible_pages": 0,
        "next_page": None,
        "took_ms": None,
        "records": [],
    }


class SemanticFakeSearchService(FakeSearchService):
    def search(self, request):
        response = super().search(request)
        response["search_context"] = {
            "mode": request.mode,
            "vector_fields": request.vector_fields,
            "top_k": request.top_k,
            "ranking_profile": "patent-knn-cosine-v1",
            "sort": request.sort,
        }
        return response


def test_vector_response_adds_search_context_without_changing_boolean_shape(client):
    app.dependency_overrides[get_search_service] = lambda: SemanticFakeSearchService()
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        response = client().post(
            "/api/patent/search",
            json={
                "mode": "vector",
                "semantic_text": "阀门",
                "vector_fields": ["abstract"],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["search_context"] == {
        "mode": "vector",
        "vector_fields": ["abstract"],
        "top_k": 100,
        "ranking_profile": "patent-knn-cosine-v1",
        "sort": "relation",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "boolean", "q": "阀门", "top_k": 100},
        {"mode": "vector", "semantic_text": "阀门", "vector_fields": ["unknown"]},
        {
            "mode": "vector",
            "semantic_text": "阀门",
            "vector_fields": ["abstract"],
            "top_k": True,
            "page_size": 1,
        },
        {
            "mode": "hybrid",
            "q": "阀门",
            "semantic_text": "阀门",
            "vector_fields": ["abstract", "abstract"],
        },
    ],
)
def test_invalid_mode_fields_return_40002_before_service_call(client, payload):
    app.dependency_overrides[get_search_service] = lambda: ExplodingSearchService()
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        response = client().post("/api/patent/search", json=payload)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["code"] == 40002


@pytest.mark.parametrize(
    "semantic_text",
    [
        "向" * (HARD_QUERY_BUDGET.max_query_chars + 1),
        " " + "向" * HARD_QUERY_BUDGET.max_query_chars,
    ],
)
def test_semantic_text_over_hard_limit_returns_40004_before_service_call(
    client,
    semantic_text,
):
    app.dependency_overrides[get_search_service] = lambda: ExplodingSearchService()
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        response = client().post(
            "/api/patent/search",
            json={
                "mode": "vector",
                "semantic_text": semantic_text,
                "vector_fields": ["abstract"],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["code"] == 40004


def test_search_api_returns_50001_on_opensearch_failure(client):
    app.dependency_overrides[get_search_service] = lambda: OpenSearchFailingService()
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        response = client().post("/api/patent/search", json={"q": "阀门"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json()["success"] is False
    assert response.json()["code"] == 50001
    assert "搜索依赖请求失败" == response.json()["message"]
    assert response.json()["data"] is None
    assert "connection refused" not in response.text


def test_standalone_not_query_returns_200(client):
    app.dependency_overrides[get_search_service] = lambda: FakeSearchService()
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        response = client().post("/api/patent/search", json={"q": "NOT title:(外观)"})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200


class ExplodingRepository:
    def search(self, body):
        raise AssertionError("OpenSearch must not be called for invalid query syntax")


def test_vector_top_k_window_returns_40003_before_repository_call(client):
    app.dependency_overrides[get_search_service] = lambda: SearchService(
        repository=ExplodingRepository()
    )
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        response = client().post(
            "/api/patent/search",
            json={
                "mode": "vector",
                "semantic_text": "阀门",
                "vector_fields": ["abstract"],
                "top_k": 100,
                "page": 3,
                "page_size": 50,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["code"] == 40003


@pytest.mark.parametrize(
    "q",
    [
        "ipc:H02M AND AND tscd:(均衡)",
        "AND tscd:(均衡)",
        "tscd:(均衡) OR",
        'tscd:("均衡)',
        "tscd:()",
        "ipc:",
        "ipc:A2",
        "ipc:A01B1/0",
        "mainIpc:A2",
        "mainIpc:A01B1/0",
        "foo:(均衡)",
        "mainClaim:",
        "claims:()",
        "description:(均衡) AND AND ipc:H02M",
        "ad:[2020-01-01 2020-12-31]",
        "ad:[2020-13-01 TO 2020-12-31]",
        "ad:[2021-01-01 TO 2020-12-31]",
        "documentYear:[2024 TO 2020]",
        "NOT",
        "tscd:(均衡) NOT",
    ],
)
def test_invalid_stage_six_queries_return_40001_without_repository_call(client, q):
    app.dependency_overrides[get_search_service] = lambda: SearchService(repository=ExplodingRepository())
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        response = client().post("/api/patent/search", json={"q": q})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["success"] is False
    assert response.json()["code"] == 40001
    assert response.json()["data"] is None


def test_search_request_schema_does_not_expose_index_analyzer_mode():
    assert all(
        "index_analyzer_mode" not in _search_mode_schema(mode)["properties"]
        for mode in ("boolean", "vector", "hybrid")
    )

    with pytest.raises(ValidationError):
        SearchRequest(q="阀门", index_analyzer_mode="compat")
