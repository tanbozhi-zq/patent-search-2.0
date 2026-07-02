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
