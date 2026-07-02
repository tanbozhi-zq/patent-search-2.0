import pytest

from app.query.dsl_builder import UnsupportedQuerySyntax, build_search_dsl
from app.schemas.search import SearchRequest


def test_plain_keyword_searches_title_and_abstract():
    dsl = build_search_dsl(SearchRequest(q="阀门"))

    assert dsl["query"]["bool"]["must"][0]["multi_match"]["query"] == "阀门"
    assert dsl["query"]["bool"]["must"][0]["multi_match"]["fields"] == ["Title", "Abstract"]


def test_title_query_uses_title_fields():
    dsl = build_search_dsl(SearchRequest(q="title:(阀门)"))

    fields = dsl["query"]["bool"]["must"][0]["multi_match"]["fields"]
    assert fields == ["Title", "TitleCN", "TitleEN"]


def test_ipc_query_uses_should_terms():
    dsl = build_search_dsl(SearchRequest(q="ipc:H02M"))

    should = dsl["query"]["bool"]["must"][0]["bool"]["should"]
    assert {"term": {"IPC": "H02M"}} in should
    assert {"match": {"IPCList": "H02M"}} in should


def test_application_date_range_query():
    dsl = build_search_dsl(SearchRequest(q="ad:[2020-01-01 TO 2020-12-31]"))

    assert dsl["query"]["bool"]["must"][0]["range"]["ApplicationDate"] == {
        "gte": "2020-01-01",
        "lte": "2020-12-31",
    }


def test_rejects_stage_six_syntax():
    with pytest.raises(UnsupportedQuerySyntax):
        build_search_dsl(SearchRequest(q="tscd:(均衡)"))
