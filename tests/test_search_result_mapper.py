"""验证搜索结果 mapper 的固定字段、文本回退、类型和可访问分页元数据输出。"""

from dataclasses import replace

import pytest

from app.mappings.result_mapper import SEARCH_RECORD_FIELDS, map_search_response
from app.query.budget import DEFAULT_QUERY_BUDGET


def _response(source: dict, *, score: float = 12.3, total: int = 1) -> dict:
    return {
        "took": 8,
        "hits": {
            "total": {"value": total},
            "hits": [{"_score": score, "_source": source}],
        },
    }


def _source(**overrides) -> dict:
    source = {
        "patent_id": "cn-1",
        "ApplicationNumber": "CN1",
        "PublicationNumber": "CN1A",
        "Title": "标题",
        "Abstract": "摘要",
        "Applicant": "申请人",
        "Assignee": "当前权利人",
        "Inventor": "发明人",
        "IPC": "H05K 5/03 (2006.01)I",
        "IPCSmallGroup": "H05K5/02",
        "IPCList": ["h05k 5/02 (2006.01)i", "B23P 15/00 (2006.01)", "invalid"],
        "MainClaim": "首权",
        "IndependentClaimsCN": "独立权利要求",
        "Requirement": "完整权利要求书",
        "ApplicationDate": "2020-01-01",
        "PublicationDate": "2020-02-01",
        "LatestLegalStatus": "授权",
        "Type": "发明申请",
    }
    source.update(overrides)
    return source


def test_search_record_uses_exact_snake_case_contract():
    record = map_search_response(
        _response(_source()),
        page=1,
        page_size=50,
        budget=DEFAULT_QUERY_BUDGET,
    )["records"][0]

    assert tuple(record) == SEARCH_RECORD_FIELDS
    assert record == {
        "id": "cn-1",
        "application_number": "CN1",
        "publication_number": "CN1A",
        "title": "标题",
        "abstract": "摘要",
        "applicant": "申请人",
        "current_assignee": "当前权利人",
        "inventor": "发明人",
        "main_ipc": "H05K5/02",
        "ipc_list": ["H05K5/02", "B23P15/00"],
        "main_claim": "首权",
        "application_date": "2020-01-01",
        "publication_date": "2020-02-01",
        "legal_status": "授权",
        "type": "发明专利",
        "score": 12.3,
    }
    assert "independent_claims" not in record
    assert "claims" not in record
    assert "IPCListBase" not in record


def test_search_record_uses_main_claim_language_fallback_without_claim_body_fields():
    source = _source(
        MainClaim=None,
        MainClaimCN="中文首权",
        MainClaimEN="English main claim",
        IndependentClaimsCN="独权",
        Requirement="完整权要",
    )

    record = map_search_response(
        _response(source),
        page=1,
        page_size=10,
        budget=DEFAULT_QUERY_BUDGET,
    )["records"][0]

    assert record["main_claim"] == "中文首权"
    assert set(record) == set(SEARCH_RECORD_FIELDS)


def test_search_record_normalizes_array_text_fields_without_inventing_assignee():
    record = map_search_response(
        _response(
            _source(
                Applicant=["鲁贝里股份公司", "鲁贝里股份公司"],
                Assignee=["受让人甲", "受让人乙"],
                Inventor=["发明人甲", "发明人乙"],
            )
        ),
        page=1,
        page_size=10,
        budget=DEFAULT_QUERY_BUDGET,
    )["records"][0]

    assert record["applicant"] == "鲁贝里股份公司;鲁贝里股份公司"
    assert record["current_assignee"] == "受让人甲;受让人乙"
    assert record["inventor"] == "发明人甲;发明人乙"

    no_assignee = map_search_response(
        _response(_source(Applicant=["鲁贝里股份公司"], Assignee=None)),
        page=1,
        page_size=10,
        budget=DEFAULT_QUERY_BUDGET,
    )["records"][0]

    assert no_assignee["applicant"] == "鲁贝里股份公司"
    assert no_assignee["current_assignee"] == ""


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ({"Type": None, "PatentTypeCode": "1"}, "发明专利"),
        ({"Type": None, "PatentTypeCode": "2"}, "实用新型"),
        ({"Type": None, "PatentTypeCode": "3"}, "外观设计"),
        ({"Type": None, "PatentTypeCode": None, "PublicationCountry": "DE", "Kind": "D1"}, "发明专利"),
        ({"Type": None, "PatentTypeCode": None, "Kind": "D1"}, ""),
    ],
)
def test_search_record_type_uses_explicit_type_code_or_national_kind_mapping(source, expected):
    record = map_search_response(
        _response(_source(**source)),
        page=1,
        page_size=10,
        budget=DEFAULT_QUERY_BUDGET,
    )["records"][0]

    assert record["type"] == expected


def test_search_response_keeps_pagination_metadata():
    raw = {"took": 35, "hits": {"total": {"value": 101}, "hits": []}}

    page_one = map_search_response(
        raw,
        page=1,
        page_size=50,
        budget=DEFAULT_QUERY_BUDGET,
    )
    page_three = map_search_response(
        raw,
        page=3,
        page_size=50,
        budget=DEFAULT_QUERY_BUDGET,
    )
    empty = map_search_response(
        {"hits": {"total": {"value": 0}, "hits": []}},
        page=1,
        page_size=50,
        budget=DEFAULT_QUERY_BUDGET,
    )

    assert page_one == {
        "total": 101,
        "page": 1,
        "page_size": 50,
        "total_pages": 3,
        "accessible_pages": 3,
        "next_page": 2,
        "took_ms": 35,
        "records": [],
    }
    assert page_three["next_page"] is None
    assert empty["total_pages"] == 0
    assert empty["accessible_pages"] == 0
    assert empty["next_page"] is None


@pytest.mark.parametrize(
    (
        "total",
        "page",
        "expected_total_pages",
        "expected_accessible_pages",
        "expected_next_page",
    ),
    (
        (154_812, 999, 15_482, 1_000, 1_000),
        (9_999, 1_000, 1_000, 1_000, None),
        (10_000, 1_000, 1_000, 1_000, None),
        (10_001, 1_000, 1_001, 1_000, None),
    ),
)
def test_search_response_stops_next_page_at_accessible_window(
    total,
    page,
    expected_total_pages,
    expected_accessible_pages,
    expected_next_page,
):
    raw = {"hits": {"total": {"value": total}, "hits": []}}

    mapped = map_search_response(
        raw,
        page=page,
        page_size=10,
        budget=DEFAULT_QUERY_BUDGET,
    )

    assert mapped["total_pages"] == expected_total_pages
    assert mapped["accessible_pages"] == expected_accessible_pages
    assert mapped["next_page"] == expected_next_page


def test_accessible_pages_follows_non_divisible_result_window_contract():
    budget = replace(
        DEFAULT_QUERY_BUDGET,
        max_page_size=6,
        max_result_window=10,
    )

    mapped = map_search_response(
        {"hits": {"total": {"value": 7}, "hits": []}},
        page=1,
        page_size=6,
        budget=budget,
    )

    assert mapped["total_pages"] == 2
    assert mapped["accessible_pages"] == 1
    assert mapped["next_page"] is None


def test_semantic_result_window_caps_total_and_rejects_partial_tail_page_link():
    context = {
        "mode": "vector",
        "vector_fields": ["abstract"],
        "top_k": 105,
        "ranking_profile": "patent-knn-cosine-v1",
        "sort": "relation",
    }

    mapped = map_search_response(
        {"hits": {"total": {"value": 500}, "hits": []}},
        page=2,
        page_size=50,
        budget=DEFAULT_QUERY_BUDGET,
        result_window=105,
        search_context=context,
    )

    assert mapped["total"] == 105
    assert mapped["total_pages"] == 3
    assert mapped["accessible_pages"] == 2
    assert mapped["next_page"] is None
    assert mapped["search_context"] == context


def test_semantic_gte_total_maps_to_bounded_public_top_k():
    mapped = map_search_response(
        {
            "hits": {
                "total": {"value": 100, "relation": "gte"},
                "hits": [],
            }
        },
        page=1,
        page_size=20,
        budget=DEFAULT_QUERY_BUDGET,
        result_window=100,
    )

    assert mapped["total"] == 100
    assert mapped["total_pages"] == 5
    assert mapped["accessible_pages"] == 5
