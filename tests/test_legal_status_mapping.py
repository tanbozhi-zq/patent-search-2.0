from app.mappings.legal_status_mapping import build_legal_status_clause
from app.mappings.query_field_mapping import TEXT_FIELD_MAPPING


def test_text_field_mapping_contains_stage_six_fields():
    assert TEXT_FIELD_MAPPING["tscd"] == ["Title", "Abstract", "MainClaim", "Requirement", "Instructions"]
    assert TEXT_FIELD_MAPPING["applicant"] == ["Applicant", "ApplicantNormalized", "FirstApplicant"]
    assert TEXT_FIELD_MAPPING["currentAssignee"] == ["Assignee", "AssigneeNormalized"]
    assert TEXT_FIELD_MAPPING["type"] == ["Type", "PatentTypeCode", "Kind"]


def test_effective_patent_legal_status_mapping():
    clause = build_legal_status_clause("有效专利")

    should = clause["bool"]["should"]
    assert {"match": {"LatestLegalStatus": "授权"}} in should
    assert {"match": {"LatestLegalStatus": "有效"}} in should
    assert clause["bool"]["minimum_should_match"] == 1


def test_unknown_legal_status_falls_back_to_text_match():
    assert build_legal_status_clause("部分无效") == {
        "multi_match": {
            "query": "部分无效",
            "fields": ["LatestLegalStatus", "LegalStatus"],
        }
    }


from app.mappings.query_field_mapping import get_normal_analyzer_fields, get_risky_analyzer_fields


def test_stage_6_5_splits_tscd_normal_and_risky_fields():
    assert get_normal_analyzer_fields("tscd") == ["Title", "Abstract"]
    assert get_risky_analyzer_fields("tscd") == ["MainClaim", "Requirement", "Instructions"]


def test_stage_6_5_splits_title_and_abstract_cn_fields():
    assert get_normal_analyzer_fields("title") == ["Title", "TitleEN"]
    assert get_risky_analyzer_fields("title") == ["TitleCN"]
    assert get_normal_analyzer_fields("ab") == ["Abstract", "AbstractEN"]
    assert get_risky_analyzer_fields("ab") == ["AbstractCN"]


def test_stage_6_5_splits_type_fields():
    assert get_normal_analyzer_fields("type") == ["PatentTypeCode", "Kind"]
    assert get_risky_analyzer_fields("type") == ["Type"]


def test_stage_6_5_keeps_applicant_fields_normal():
    assert get_normal_analyzer_fields("applicant") == ["Applicant", "ApplicantNormalized", "FirstApplicant"]
    assert get_risky_analyzer_fields("applicant") == []
