from pydantic import ValidationError
import pytest

from app.schemas.search import SearchRequest


def test_search_request_defaults():
    request = SearchRequest(q="阀门")

    assert request.ds == "cn"
    assert request.sort == "relation"
    assert request.page == 1
    assert request.page_size == 50
    assert request.highlight == 0
    assert request.offset == 0


def test_search_request_rejects_invalid_page_size():
    with pytest.raises(ValidationError):
        SearchRequest(q="阀门", page_size=101)


from app.api.search import get_search_service
from app.core.security import require_api_key
from app.main import app
from app.services.search_service import SearchService


class FakeSearchService:
    def search(self, request):
        return {"total": 0, "page": request.page, "page_size": request.page_size, "records": []}


def test_search_endpoint_returns_vendor_like_shape(client):
    app.dependency_overrides[get_search_service] = lambda: FakeSearchService()
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        response = client().post("/api/patent/search", json={"q": "阀门"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"total": 0, "page": 1, "page_size": 50, "records": []}


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


@pytest.mark.parametrize(
    "q",
    [
        "ipc:H02M AND AND tscd:(均衡)",
        "AND tscd:(均衡)",
        "tscd:(均衡) OR",
        'tscd:("均衡)',
        "tscd:()",
        "ipc:",
        "foo:(均衡)",
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
    assert response.json()["detail"]["success"] is False
    assert response.json()["detail"]["code"] == 40001
    assert response.json()["detail"]["data"] is None


def test_search_request_defaults_to_index_analyzer_compat_mode():
    request = SearchRequest(q="阀门")

    assert request.index_analyzer_mode == "compat"


def test_search_request_accepts_normal_index_analyzer_mode():
    request = SearchRequest(q="阀门", index_analyzer_mode="normal")

    assert request.index_analyzer_mode == "normal"


def test_search_request_rejects_invalid_index_analyzer_mode():
    with pytest.raises(ValidationError):
        SearchRequest(q="阀门", index_analyzer_mode="broken")
