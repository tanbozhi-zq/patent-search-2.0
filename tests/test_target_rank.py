import pytest

from app.api.console import get_search_service
from app.core.exceptions import OpenSearchQueryError, QuerySyntaxError
from app.main import app
from app.schemas.search import TargetRankRequest
from app.services.search_service import SearchService


def hit(patent_id="patent-1", publication_number="CN100B", score=4.5, **source):
    return {
        "_score": score,
        "_source": {
            "patent_id": patent_id,
            "PublicationNumber": publication_number,
            "Title": "目标专利",
            **source,
        },
    }


class FakeTargetRepository:
    def __init__(self, target=None, match_count=1, target_in_query=None, counts=None):
        self.target = target
        self.match_count = match_count
        self.target_in_query = target_in_query
        self.counts = list(counts or [])
        self.min_score_calls = []

    def find_target(self, identifier):
        self.identifier = identifier
        return "patent_id", self.target, self.match_count

    def find_in_query(self, query, identity):
        self.identity = identity
        return self.target_in_query

    def count(self, body):
        return self.counts.pop(0)

    def count_with_min_score(self, query, min_score):
        self.min_score_calls.append(min_score)
        return self.counts.pop(0)


class QueryParsingRepository:
    def find_target(self, identifier):
        raise AssertionError("target lookup must not run before query parsing")


def test_target_rank_returns_target_not_found():
    service = SearchService(repository=FakeTargetRepository(target=None, match_count=0))

    result = service.target_rank(TargetRankRequest(q="阀门", target_identifier=" missing "))

    assert result["status"] == "target_not_found"
    assert result["target"] is None


def test_target_rank_parses_query_before_target_lookup():
    service = SearchService(repository=QueryParsingRepository())

    with pytest.raises(QuerySyntaxError):
        service.target_rank(TargetRankRequest(q="AND", target_identifier="missing"))


def test_target_rank_returns_ambiguous_target():
    service = SearchService(repository=FakeTargetRepository(target=hit(), match_count=2))

    result = service.target_rank(TargetRankRequest(q="阀门", target_identifier="CN100B"))

    assert result["status"] == "ambiguous_target"
    assert result["tied_count"] == 2


def test_target_rank_distinguishes_existing_target_outside_query():
    target = hit()
    service = SearchService(repository=FakeTargetRepository(target=target, target_in_query=None))

    result = service.target_rank(TargetRankRequest(q="阀门", target_identifier="CN100B"))

    assert result["status"] == "not_in_results"
    assert result["target"]["documentNumber"] == "CN100B"


@pytest.mark.parametrize("sort", ["relation", "rank", "relevance", "score"])
def test_relevance_target_rank_uses_strict_score_threshold_and_ties(sort):
    target = hit(score=4.5)
    repository = FakeTargetRepository(target=target, target_in_query=target, counts=[3, 6])
    service = SearchService(repository=repository)

    result = service.target_rank(TargetRankRequest(q="阀门", sort=sort, target_identifier="CN100B"))

    assert result["status"] == "matched"
    assert result["rank"] == 4
    assert result["tied_count"] == 2
    assert repository.min_score_calls[0] > 4.5
    assert repository.min_score_calls[1] == 4.5


@pytest.mark.parametrize(
    ("sort", "field", "operator"),
    [
        ("applicationDate", "ApplicationDate", "lt"),
        ("!applicationDate", "ApplicationDate", "gt"),
        ("documentDate", "PublicationDate", "lt"),
        ("!documentDate", "PublicationDate", "gt"),
    ],
)
def test_date_target_rank_counts_better_and_tied_records(sort, field, operator):
    target = hit(**{field: "2024-01-02"})
    repository = FakeTargetRepository(target=target, target_in_query=target, counts=[7, 2])
    service = SearchService(repository=repository)

    result = service.target_rank(TargetRankRequest(q="阀门", sort=sort, target_identifier="CN100B"))

    assert result["rank"] == 8
    assert result["tied_count"] == 2
    assert result["sort_value"] == "2024-01-02"


@pytest.mark.parametrize("sort", ["applicationDate", "!applicationDate", "documentDate", "!documentDate"])
def test_missing_date_is_last_tied_group(sort):
    target = hit()
    repository = FakeTargetRepository(target=target, target_in_query=target, counts=[12, 3])
    service = SearchService(repository=repository)

    result = service.target_rank(TargetRankRequest(q="阀门", sort=sort, target_identifier="CN100B"))

    assert result["rank"] == 13
    assert result["tied_count"] == 3
    assert result["sort_value"] is None


class FakeTargetRankService:
    def target_rank(self, request):
        return {
            "status": "matched",
            "in_results": True,
            "rank": 3,
            "tied_count": 1,
            "sort_value": 4.5,
            "target": {"patent_id": "patent-1", "documentNumber": "CN100B", "title": "目标专利"},
        }


def test_console_target_rank_endpoint(client):
    app.dependency_overrides[get_search_service] = lambda: FakeTargetRankService()
    try:
        response = client().post(
            "/console-api/test/target-rank",
            json={"q": "阀门", "ds": "cn", "sort": "relation", "target_identifier": "CN100B"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["rank"] == 3


class FailingTargetRankService:
    def target_rank(self, request):
        raise OpenSearchQueryError("OpenSearch 查询异常")


def test_console_target_rank_maps_opensearch_failure(client):
    app.dependency_overrides[get_search_service] = lambda: FailingTargetRankService()
    try:
        response = client().post(
            "/console-api/test/target-rank",
            json={"q": "阀门", "target_identifier": "CN100B"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json()["code"] == 50001


class SyntaxFailingTargetRankService:
    def target_rank(self, request):
        raise QuerySyntaxError("q 查询语法错误")


def test_console_target_rank_maps_query_syntax_failure(client):
    app.dependency_overrides[get_search_service] = lambda: SyntaxFailingTargetRankService()
    try:
        response = client().post(
            "/console-api/test/target-rank",
            json={"q": "AND", "target_identifier": "CN100B"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["code"] == 40001
