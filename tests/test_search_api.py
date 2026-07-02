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
