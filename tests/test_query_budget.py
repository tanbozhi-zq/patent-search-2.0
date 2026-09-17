"""验证查询预算的硬上限、配置快照和跨解析/DSL 阶段的一致执行。"""

from dataclasses import replace
import random

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
import pytest

from app.api.search import get_search_service
from app.core.error_handlers import REQUEST_ID_HEADER, register_error_handlers
from app.core.exceptions import (
    PaginationOutOfRangeError,
    QueryComplexityError,
    QuerySyntaxError,
)
from app.core.request_body_limit import QueryRequestBodyLimitMiddleware
from app.core.security import require_api_key, require_console_access
from app.main import app
from app.query.budget import (
    DEFAULT_QUERY_BUDGET,
    QueryBudget,
    StaticQueryBudgetProvider,
)
from app.query.parser import parse_query
from app.query.tokenizer import tokenize
from app.schemas.search import SearchRequest
from app.services.search_service import SearchService


def roomy_budget(**changes: int) -> QueryBudget:
    values = {
        "max_request_body_bytes": 1_000_000,
        "max_query_chars": 100_000,
        "max_nesting_depth": 1_000,
        "max_tokens": 10_000,
        "max_ast_nodes": 10_000,
        "max_boolean_clauses": 10_000,
        "max_page_size": 10_000,
        "max_result_window": 1_000_000,
    }
    values.update(changes)
    return replace(DEFAULT_QUERY_BUDGET, **values)


@pytest.mark.parametrize(
    ("length", "accepted"),
    [(999, True), (1000, True), (1001, False)],
)
def test_query_character_budget_limit_minus_one_limit_and_limit_plus_one(
    length,
    accepted,
):
    budget = roomy_budget(max_query_chars=1000)
    if accepted:
        assert tokenize("x" * length, budget=budget)[0].value == "x" * length
    else:
        with pytest.raises(QueryComplexityError):
            tokenize("x" * length, budget=budget)


@pytest.mark.parametrize(
    ("token_count", "accepted"),
    [(255, True), (256, True), (257, False)],
)
def test_token_budget_limit_minus_one_limit_and_limit_plus_one(
    token_count,
    accepted,
):
    budget = roomy_budget(max_tokens=256)
    query = " ".join(["x"] * token_count)
    if accepted:
        assert len(tokenize(query, budget=budget)) == token_count + 1
    else:
        with pytest.raises(QueryComplexityError):
            tokenize(query, budget=budget)


@pytest.mark.parametrize(
    ("depth", "accepted"),
    [(31, True), (32, True), (33, False)],
)
def test_nesting_budget_limit_minus_one_limit_and_limit_plus_one(
    depth,
    accepted,
):
    budget = roomy_budget(max_nesting_depth=32)
    query = "(" * depth + "x" + ")" * depth
    if accepted:
        parse_query(query, budget=budget)
    else:
        with pytest.raises(QueryComplexityError):
            parse_query(query, budget=budget)


@pytest.mark.parametrize(
    ("node_count", "accepted"),
    [(255, True), (256, True), (257, False)],
)
def test_ast_budget_limit_minus_one_limit_and_limit_plus_one(
    node_count,
    accepted,
):
    budget = roomy_budget(max_ast_nodes=256)
    query = "NOT " * (node_count - 1) + "x"
    if accepted:
        parse_query(query, budget=budget)
    else:
        with pytest.raises(QueryComplexityError):
            parse_query(query, budget=budget)


@pytest.mark.parametrize(
    ("boolean_count", "accepted"),
    [(127, True), (128, True), (129, False)],
)
def test_boolean_budget_limit_minus_one_limit_and_limit_plus_one(
    boolean_count,
    accepted,
):
    budget = roomy_budget(max_boolean_clauses=128)
    query = "NOT " * boolean_count + "x"
    if accepted:
        parse_query(query, budget=budget)
    else:
        with pytest.raises(QueryComplexityError):
            parse_query(query, budget=budget)


@pytest.mark.parametrize(
    ("page", "page_size", "accepted"),
    [
        (9_999, 1, True),
        (10_000, 1, True),
        (10_001, 1, False),
        (1_000, 10, True),
        (1_001, 10, False),
        (3_333, 3, True),
        (3_334, 3, False),
    ],
)
def test_result_window_limit_minus_one_limit_and_limit_plus_one(
    page,
    page_size,
    accepted,
):
    budget = DEFAULT_QUERY_BUDGET
    if accepted:
        budget.validate_pagination(page=page, page_size=page_size)
    else:
        with pytest.raises(PaginationOutOfRangeError):
            budget.validate_pagination(page=page, page_size=page_size)


@pytest.mark.parametrize(
    ("page_size", "expected"),
    ((1, 10_000), (3, 3_333), (10, 1_000), (100, 100)),
)
def test_accessible_page_limit_uses_floor_division(page_size, expected):
    assert DEFAULT_QUERY_BUDGET.max_accessible_page(page_size=page_size) == expected


@pytest.mark.parametrize(
    ("page_size", "accepted"),
    [(99, True), (100, True), (101, False)],
)
def test_page_size_limit_minus_one_limit_and_limit_plus_one(page_size, accepted):
    budget = DEFAULT_QUERY_BUDGET
    if accepted:
        budget.validate_pagination(page=1, page_size=page_size)
    else:
        with pytest.raises(PaginationOutOfRangeError):
            budget.validate_pagination(page=1, page_size=page_size)


def test_mixed_parentheses_and_not_share_the_recursive_nesting_budget():
    budget = roomy_budget(max_nesting_depth=8)
    parse_query("(" * 4 + "NOT " * 4 + "x" + ")" * 4, budget=budget)

    with pytest.raises(QueryComplexityError):
        parse_query(
            "(" * 4 + "NOT " * 5 + "x" + ")" * 4,
            budget=budget,
        )


def test_deterministic_adversarial_queries_never_escape_as_recursion_error():
    generator = random.Random(31)
    alphabet = ["阀门", "😀", "NOT ", "(", ")", " OR ", " AND ", '"']

    for _ in range(200):
        query = "".join(generator.choice(alphabet) for _ in range(400))
        try:
            parse_query(query)
        except (QueryComplexityError, QuerySyntaxError):
            pass


class RecordingRepository:
    def __init__(self, *, total=0):
        self.calls = 0
        self.total = total

    def search(self, body):
        self.calls += 1
        return {"hits": {"total": {"value": self.total}, "hits": []}}

    def find_target(self, identifier):
        self.calls += 1
        raise AssertionError("target lookup must not run for a rejected query")


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/api/patent/search",
            {"q": "(" * 33 + "阀门" + ")" * 33},
        ),
        (
            "/console-api/search",
            {"q": "title:(" + " OR ".join(["阀门"] * 130) + ")"},
        ),
        (
            "/console-api/test/target-rank",
            {
                "q": "(" * 33 + "阀门" + ")" * 33,
                "target_identifier": "CN1A",
            },
        ),
    ],
)
def test_http_and_console_query_complexity_is_40004_before_repository_call(
    client,
    path,
    payload,
):
    repository = RecordingRepository()
    app.dependency_overrides[get_search_service] = lambda: SearchService(repository)
    app.dependency_overrides[require_api_key] = lambda: None
    app.dependency_overrides[require_console_access] = lambda: None
    try:
        response = client().post(path, json=payload)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["code"] == 40004
    assert response.json()["request_id"] == response.headers[REQUEST_ID_HEADER]
    assert repository.calls == 0


def test_query_length_over_hard_limit_is_40004_before_service_call(client):
    class UnexpectedService:
        def search(self, request):
            raise AssertionError("service must not run for an overlong query")

    app.dependency_overrides[get_search_service] = lambda: UnexpectedService()
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        response = client().post("/api/patent/search", json={"q": "阀" * 1001})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["code"] == 40004


def test_pagination_error_keeps_precedence_when_multiple_fields_are_invalid(client):
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        response = client().post(
            "/api/patent/search",
            json={"q": "阀" * 1001, "page": 0},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["code"] == 40003


@pytest.mark.parametrize(
    "path",
    ("/api/patent/search", "/console-api/search"),
)
def test_page_1001_is_40003_before_repository_call(client, path):
    repository = RecordingRepository()
    app.dependency_overrides[get_search_service] = lambda: SearchService(repository)
    app.dependency_overrides[require_api_key] = lambda: None
    app.dependency_overrides[require_console_access] = lambda: None
    try:
        response = client().post(
            path,
            json={"q": "阀门", "page": 1_001, "page_size": 10},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["code"] == 40003
    assert repository.calls == 0


class RecordingSearchService:
    def __init__(self):
        self.calls = 0

    def search(self, request):
        self.calls += 1
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


@pytest.mark.parametrize(
    ("body_size", "expected_status", "expected_code", "expected_calls"),
    [
        (16 * 1024 - 1, 200, None, 1),
        (16 * 1024, 200, None, 1),
        (16 * 1024 + 1, 413, 41301, 0),
    ],
)
def test_request_body_budget_limit_minus_one_limit_and_limit_plus_one(
    client,
    body_size,
    expected_status,
    expected_code,
    expected_calls,
):
    service = RecordingSearchService()
    app.dependency_overrides[get_search_service] = lambda: service
    app.dependency_overrides[require_api_key] = lambda: None
    base = b'{"q":"x"}'
    body = base + b" " * (body_size - len(base))
    try:
        response = client().post(
            "/api/patent/search",
            content=body,
            headers={"content-type": "application/json"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == expected_status
    assert service.calls == expected_calls
    if expected_code is not None:
        assert response.json()["code"] == expected_code
        assert response.json()["request_id"] == response.headers[REQUEST_ID_HEADER]


def test_streamed_body_without_content_length_is_still_limited():
    test_app = FastAPI()
    provider = StaticQueryBudgetProvider(
        replace(DEFAULT_QUERY_BUDGET, max_request_body_bytes=10)
    )
    test_app.add_middleware(
        QueryRequestBodyLimitMiddleware,
        budget_provider=provider,
    )
    register_error_handlers(test_app)

    @test_app.post("/api/patent/search")
    async def read_body(request: Request):
        await request.body()
        return {"ok": True}

    def chunks():
        yield b"123456"
        yield b"78901"

    response = TestClient(test_app, raise_server_exceptions=False).post(
        "/api/patent/search",
        content=chunks(),
    )

    assert response.status_code == 413
    assert response.json()["code"] == 41301
    assert response.json()["request_id"] == response.headers[REQUEST_ID_HEADER]


@pytest.mark.parametrize(
    ("body_chunks", "expected_status", "expected_code", "expected_calls"),
    (
        ((b'{"q":"', b'valve"}'), 200, None, 1),
        (
            (
                b'{"q":"' + b"x" * (9 * 1024),
                b"x" * (9 * 1024) + b'"}',
            ),
            413,
            41301,
            0,
        ),
    ),
)
def test_public_search_streamed_body_limit_contract_is_unchanged(
    client,
    body_chunks,
    expected_status,
    expected_code,
    expected_calls,
):
    service = RecordingSearchService()
    app.dependency_overrides[get_search_service] = lambda: service
    app.dependency_overrides[require_api_key] = lambda: None

    def chunks():
        yield from body_chunks

    try:
        response = client().post(
            "/api/patent/search",
            content=chunks(),
            headers={"content-type": "application/json"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == expected_status
    assert service.calls == expected_calls
    if expected_code is not None:
        assert response.json()["code"] == expected_code
        assert response.json()["request_id"] == response.headers[REQUEST_ID_HEADER]


def test_service_uses_one_budget_snapshot_for_the_complete_request():
    repository = RecordingRepository(total=101)
    budget = replace(
        DEFAULT_QUERY_BUDGET,
        max_page_size=10,
        max_result_window=50,
    )

    class CountingProvider:
        def __init__(self):
            self.calls = 0

        def snapshot(self):
            self.calls += 1
            return budget

    provider = CountingProvider()
    service = SearchService(repository, query_budget_provider=provider)

    result = service.search(SearchRequest(q="阀门", page=5, page_size=10))

    assert provider.calls == 1
    assert repository.calls == 1
    assert result["total_pages"] == 11
    assert result["accessible_pages"] == 5
    assert result["next_page"] is None


def test_tightened_runtime_budget_is_used_before_repository_call():
    repository = RecordingRepository()
    budget = replace(DEFAULT_QUERY_BUDGET, max_query_chars=2)
    service = SearchService(
        repository,
        query_budget_provider=StaticQueryBudgetProvider(budget),
    )

    with pytest.raises(QueryComplexityError):
        service.search(SearchRequest(q="阀门阀"))

    assert repository.calls == 0


def test_static_provider_rejects_values_above_code_hard_limits():
    unsafe_budget = replace(
        DEFAULT_QUERY_BUDGET,
        max_nesting_depth=DEFAULT_QUERY_BUDGET.max_nesting_depth + 1,
    )

    with pytest.raises(ValueError, match="exceeds code hard limit"):
        StaticQueryBudgetProvider(unsafe_budget)


@pytest.mark.parametrize(
    "path",
    [
        "/api/patent/search",
        "/console-api/search",
        "/console-api/test/target-rank",
    ],
)
def test_oversized_body_limit_is_shared_by_all_query_entrypoints(client, path):
    response = client().post(
        path,
        content=b"x" * (DEFAULT_QUERY_BUDGET.max_request_body_bytes + 1),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["code"] == 41301
    assert response.json()["request_id"] == response.headers[REQUEST_ID_HEADER]
