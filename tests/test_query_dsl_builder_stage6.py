import pytest

from app.core.exceptions import QuerySyntaxError
from app.query.dsl_builder import build_search_dsl
from app.schemas.search import SearchRequest


def query_clause(q: str) -> dict:
    return build_search_dsl(SearchRequest(q=q))["query"]["bool"]["must"][0]


def test_and_query_maps_to_bool_must():
    clause = query_clause("ipc:H02M AND tscd:(均衡)")

    must = clause["bool"]["must"]
    assert len(must) == 2
    assert must[0]["bool"]["minimum_should_match"] == 1
    assert must[1]["multi_match"]["fields"] == ["Title", "Abstract", "MainClaim", "Requirement", "Instructions"]


def test_or_query_maps_to_bool_should():
    clause = query_clause("title:(均衡) OR title:(平衡)")

    assert clause["bool"]["minimum_should_match"] == 1
    assert [item["multi_match"]["query"] for item in clause["bool"]["should"]] == ["均衡", "平衡"]


def test_not_query_maps_to_bool_must_not():
    clause = query_clause("ipc:H02M AND NOT tscd:(均衡)")

    assert "must_not" in clause["bool"]["must"][1]["bool"]
    assert clause["bool"]["must"][1]["bool"]["must_not"][0]["multi_match"]["query"] == "均衡"


def test_applicant_current_assignee_and_type_fields():
    assert query_clause("applicant:(华为技术有限公司)")["multi_match"]["fields"] == [
        "Applicant",
        "ApplicantNormalized",
        "FirstApplicant",
    ]
    assert query_clause("currentAssignee:(华为技术有限公司)")["multi_match"]["fields"] == [
        "Assignee",
        "AssigneeNormalized",
    ]
    assert query_clause("type:(发明专利)")["multi_match"]["fields"] == ["PatentType"]


def test_document_year_maps_to_publication_date_range():
    assert query_clause("documentYear:[2020 TO 2024]") == {
        "range": {
            "PublicationDate": {
                "gte": "2020-01-01",
                "lte": "2024-12-31",
            }
        }
    }


@pytest.mark.parametrize(
    "q,error",
    [
        ("foo:(均衡)", "不支持字段 foo"),
        ("ad:[2020-13-01 TO 2020-12-31]", "日期格式非法"),
        ("ad:[2021-01-01 TO 2020-12-31]", "范围起始值不能晚于结束值"),
        ("documentYear:[2024 TO 2020]", "范围起始值不能晚于结束值"),
    ],
)
def test_rejects_invalid_dsl_inputs(q, error):
    with pytest.raises(QuerySyntaxError, match=error):
        build_search_dsl(SearchRequest(q=q))
