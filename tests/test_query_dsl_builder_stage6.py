import pytest

from app.core.exceptions import QuerySyntaxError
from app.query.dsl_builder import build_search_dsl
from app.schemas.search import SearchRequest


def query_clause(q: str) -> dict:
    return build_search_dsl(SearchRequest(q=q, index_analyzer_mode="normal"))["query"]["bool"]["must"][0]


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


def test_standalone_not_maps_to_bool_must_not_at_root():
    clause = query_clause("NOT title:(外观)")
    assert "must_not" in clause["bool"]
    assert clause["bool"]["must_not"][0]["multi_match"]["query"] == "外观"


def test_parenthesized_grouping_combines_with_and():
    clause = query_clause("(title:(均衡) OR title:(平衡)) AND ipc:H02M")
    assert "must" in clause["bool"]
    assert len(clause["bool"]["must"]) == 2
    assert clause["bool"]["must"][0]["bool"]["minimum_should_match"] == 1
    assert clause["bool"]["must"][1]["bool"]["minimum_should_match"] == 1


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
    assert query_clause("type:(发明专利)")["multi_match"]["fields"] == ["Type", "PatentTypeCode", "Kind"]


def test_stage_12_agency_and_agent_fields():
    assert query_clause("agency:(知识产权代理)")["multi_match"]["fields"] == ["Agency", "AgencyRaw"]
    assert query_clause("agent:(张)")["multi_match"]["fields"] == ["Agent"]


def test_stage_10_5_fine_grained_text_fields_in_normal_mode():
    assert query_clause("mainClaim:(均衡)")["multi_match"]["fields"] == ["MainClaim"]
    assert query_clause("claims:(均衡)")["multi_match"]["fields"] == ["Requirement"]
    assert query_clause("description:(均衡)")["multi_match"]["fields"] == ["Instructions"]


def test_stage_10_5_or_phrase_queries_in_normal_mode():
    clause = query_clause('claims:("均衡" OR "平衡")')

    assert clause["bool"]["minimum_should_match"] == 1
    assert clause["bool"]["should"] == [
        {"multi_match": {"query": "均衡", "fields": ["Requirement"]}},
        {"multi_match": {"query": "平衡", "fields": ["Requirement"]}},
    ]


def test_stage_10_5_combines_with_ipc_and_not_in_normal_mode():
    ipc_claims = query_clause("ipc:H02M AND claims:(均衡)")
    assert ipc_claims["bool"]["must"][1]["multi_match"]["fields"] == ["Requirement"]

    main_claim_not_description = query_clause("mainClaim:(电路) AND NOT description:(外观)")
    assert main_claim_not_description["bool"]["must"][0]["multi_match"]["fields"] == ["MainClaim"]
    assert main_claim_not_description["bool"]["must"][1]["bool"]["must_not"][0]["multi_match"]["fields"] == [
        "Instructions"
    ]


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
        ("mainClaim:", "字段 mainClaim 的值不能为空"),
        ("claims:()", "字段 claims 的值不能为空"),
        ("description:(均衡) AND AND ipc:H02M", "AND 后缺少查询条件"),
        ("ad:[2020-13-01 TO 2020-12-31]", "日期格式非法"),
        ("ad:[2021-01-01 TO 2020-12-31]", "范围起始值不能晚于结束值"),
        ("documentYear:[2024 TO 2020]", "范围起始值不能晚于结束值"),
    ],
)
def test_rejects_invalid_dsl_inputs(q, error):
    with pytest.raises(QuerySyntaxError, match=error):
        build_search_dsl(SearchRequest(q=q))
