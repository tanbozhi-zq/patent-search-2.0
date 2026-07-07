import pytest

from app.core.exceptions import OpenSearchQueryError
from app.schemas.search import SearchRequest
from app.services.search_service import SearchService


class FakeRepository:
    def __init__(self):
        self.body = None

    def search(self, body):
        self.body = body
        return {"hits": {"total": {"value": 0}, "hits": []}}


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
        "next_page": None,
        "took_ms": None,
        "records": [],
    }


class FailingRepository:
    def search(self, body):
        raise RuntimeError("opensearch connection refused")


def test_search_service_wraps_repository_failure():
    service = SearchService(repository=FailingRepository())

    with pytest.raises(OpenSearchQueryError):
        service.search(SearchRequest(q="阀门"))
