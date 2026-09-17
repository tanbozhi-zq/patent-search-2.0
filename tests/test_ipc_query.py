"""验证 IPC 规范化、组族边界与递归布尔 DSL 的精确查询语义。"""

import pytest

from app.core.exceptions import QuerySyntaxError
from app.query.dsl_builder import build_search_dsl
from app.query.ipc import normalize_ipc
from app.schemas.search import SearchRequest


def query_clause(q: str) -> dict:
    return build_search_dsl(SearchRequest(q=q))["query"]["bool"]["must"][0]


@pytest.mark.parametrize(
    ("value", "canonical", "level", "main_field", "main_value"),
    [
        ("a", "A", "section", "IPCSection", "A"),
        ("A01", "A01", "large_category", "IPCLargeCategory", "A01"),
        ("a01b", "A01B", "small_category", "IPCSmallCategory", "A01B"),
        ("A01B1/00", "A01B1/00", "large_group", "IPCSmallGroup", "A01B1/00"),
        ("A01B1/02", "A01B1/02", "small_group", "IPCSmallGroup", "A01B1/02"),
        ("y", "Y", "section", "IPCSection", "Y"),
        ("Y02E", "Y02E", "small_category", "IPCSmallCategory", "Y02E"),
    ],
)
def test_normalize_ipc_selects_exact_main_ipc_level(value, canonical, level, main_field, main_value):
    normalized = normalize_ipc(value, query_field="ipc")

    assert normalized.canonical == canonical
    assert normalized.level == level
    assert normalized.main_field == main_field
    assert normalized.main_value == main_value


def test_explicit_group_family_normalizes_without_changing_bare_ipc_detection():
    normalized = normalize_ipc(
        'f16k 31 (2020.01)',
        query_field="ipc",
        allow_group_family=True,
    )

    assert normalized.canonical == "F16K31"
    assert normalized.level == "group_family"
    assert normalized.main_field == "IPCLargeGroup"
    assert normalized.main_value == "F16K31/00"


def test_ipc_query_normalizes_display_format_and_uses_ipc_list_base_only():
    assert query_clause('ipc:"a01b 1/02 (2020.01)"') == {
        "term": {"IPCListBase": "A01B1/02"}
    }


@pytest.mark.parametrize(
    "keyword",
    ["B2B", "C2C", "H2O", "A320", "H264", "B2", "C4", "D3", "H2", "A2", "A01B1", "A01B1/0"],
)
def test_bare_non_complete_ipc_like_keywords_remain_full_text_queries(keyword):
    assert query_clause(keyword) == {
        "multi_match": {"query": keyword, "fields": ["TitleEN", "AbstractEN"]}
    }


def test_complete_bare_ipc_still_uses_ipc_hierarchy_query():
    assert query_clause("A01B1/02") == {"term": {"IPCListBase": "A01B1/02"}}


def test_bare_group_family_remains_a_full_text_query():
    assert query_clause("F16K31") == {
        "multi_match": {"query": "F16K31", "fields": ["TitleEN", "AbstractEN"]}
    }


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        (
            "ipc:F16K31",
            {"term": {"IPCListBase": "F16K31"}},
        ),
        (
            "ipc:F16K31/00",
            {"term": {"IPCListBase": "F16K31/00"}},
        ),
        ("ipc:F16K31/02", {"term": {"IPCListBase": "F16K31/02"}}),
    ],
)
def test_ipc_group_family_and_exact_groups_are_distinct(q, expected):
    assert query_clause(q) == expected


def test_ipc_group_family_and_exact_zero_are_distinct_in_boolean_expression():
    assert query_clause("ipc:(F16K31 OR F16K31/00)") == {
        "bool": {
            "should": [
                {"term": {"IPCListBase": "F16K31"}},
                {"term": {"IPCListBase": "F16K31/00"}},
            ],
            "minimum_should_match": 1,
        }
    }


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("mainIpc:a", {"term": {"IPCSection": "A"}}),
        ("mainIpc:A01", {"term": {"IPCLargeCategory": "A01"}}),
        ("mainIpc:a01b", {"term": {"IPCSmallCategory": "A01B"}}),
        ("mainIpc:A01B1", {"term": {"IPCLargeGroup": "A01B1/00"}}),
        ("mainIpc:A01B1/00", {"term": {"IPCSmallGroup": "A01B1/00"}}),
        ("mainIpc:y02e", {"term": {"IPCSmallCategory": "Y02E"}}),
        ("mainIpc:A01B1/02", {"term": {"IPCSmallGroup": "A01B1/02"}}),
    ],
)
def test_main_ipc_query_uses_its_matching_hierarchy_field(q, expected):
    assert query_clause(q) == expected


def test_ipc_field_or_expression_builds_recursively():
    assert query_clause("ipc:(G06V OR G06N)") == {
        "bool": {
            "should": [
                {"term": {"IPCListBase": "G06V"}},
                {"term": {"IPCListBase": "G06N"}},
            ],
            "minimum_should_match": 1,
        }
    }


def test_ipc_field_and_not_expression_builds_recursively():
    assert query_clause("ipc:(G06V AND NOT G06N)") == {
        "bool": {
            "must": [
                {"term": {"IPCListBase": "G06V"}},
                {"bool": {"must_not": [{"term": {"IPCListBase": "G06N"}}]}},
            ]
        }
    }


def test_main_ipc_field_boolean_expression_builds_recursively():
    assert query_clause("mainIpc:(A01B OR Y02E)") == {
        "bool": {
            "should": [
                {"term": {"IPCSmallCategory": "A01B"}},
                {"term": {"IPCSmallCategory": "Y02E"}},
            ],
            "minimum_should_match": 1,
        }
    }


def test_main_ipc_group_family_and_exact_zero_are_distinct_in_boolean_expression():
    assert query_clause("mainIpc:(F16K31 OR F16K31/00)") == {
        "bool": {
            "should": [
                {"term": {"IPCLargeGroup": "F16K31/00"}},
                {"term": {"IPCSmallGroup": "F16K31/00"}},
            ],
            "minimum_should_match": 1,
        }
    }


def test_publication_number_and_ipc_keep_regular_boolean_composition():
    clause = query_clause("publicationNumber:CN119188170B AND ipc:A01B1/00")

    assert clause["bool"]["must"][0]["bool"]["should"] == [
        {"term": {"PublicationNumber": "CN119188170B"}},
        {"term": {"PublicationNumberAliases": "CN119188170B"}},
        {"term": {"FirstPublicationNumber": "CN119188170B"}},
        {"term": {"GrantPublicationNumber": "CN119188170B"}},
    ]
    assert clause["bool"]["must"][1] == {"term": {"IPCListBase": "A01B1/00"}}


@pytest.mark.parametrize(
    "q",
    [
        "ipc:A2",
        "ipc:A01B1/0",
        "mainIpc:A2",
        "mainIpc:A01B1/0",
    ],
)
def test_explicit_ipc_fields_reject_incomplete_ipc_formats(q):
    with pytest.raises(QuerySyntaxError, match="IPC 格式非法"):
        build_search_dsl(SearchRequest(q=q))
