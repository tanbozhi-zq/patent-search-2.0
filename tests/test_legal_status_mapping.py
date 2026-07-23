from app.mappings.legal_status_mapping import build_legal_status_clause
from app.mappings.query_field_mapping import TEXT_FIELD_MAPPING


def test_text_field_mapping_contains_stage_six_fields():
    assert TEXT_FIELD_MAPPING["tscd"] == ["Title", "Abstract", "MainClaim", "Requirement", "Instructions"]
    assert TEXT_FIELD_MAPPING["mainClaim"] == ["MainClaim"]
    assert TEXT_FIELD_MAPPING["claims"] == ["Requirement"]
    assert TEXT_FIELD_MAPPING["description"] == ["Instructions"]
    assert TEXT_FIELD_MAPPING["applicant"] == ["Applicant", "ApplicantNormalized", "FirstApplicant"]
    assert TEXT_FIELD_MAPPING["currentAssignee"] == ["Assignee", "AssigneeNormalized"]
    assert TEXT_FIELD_MAPPING["agency"] == ["Agency", "AgencyRaw"]
    assert TEXT_FIELD_MAPPING["agent"] == ["Agent"]
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


def test_stage_10_5_fine_grained_fields_are_risky_only():
    assert get_normal_analyzer_fields("mainClaim") == []
    assert get_risky_analyzer_fields("mainClaim") == ["MainClaim"]
    assert get_normal_analyzer_fields("claims") == []
    assert get_risky_analyzer_fields("claims") == ["Requirement"]
    assert get_normal_analyzer_fields("description") == []
    assert get_risky_analyzer_fields("description") == ["Instructions"]


def test_stage_6_5_splits_title_and_abstract_cn_fields():
    assert get_normal_analyzer_fields("title") == ["Title", "TitleEN"]
    assert get_risky_analyzer_fields("title") == ["TitleCN"]
    assert get_normal_analyzer_fields("ab") == ["Abstract", "AbstractEN"]
    assert get_risky_analyzer_fields("ab") == ["AbstractCN"]


def test_stage_6_5_splits_type_fields():
    assert get_normal_analyzer_fields("type") == ["PatentTypeCode", "Kind"]
    assert get_risky_analyzer_fields("type") == ["Type"]


def test_applicant_fields_use_phrase_compatibility():
    assert get_normal_analyzer_fields("applicant") == []
    assert get_risky_analyzer_fields("applicant") == ["Applicant", "ApplicantNormalized", "FirstApplicant"]


def test_agency_uses_phrase_compatibility_and_agent_stays_normal():
    assert get_normal_analyzer_fields("agency") == []
    assert get_risky_analyzer_fields("agency") == ["Agency", "Agency.keyword", "AgencyRaw"]
    assert get_normal_analyzer_fields("agent") == ["Agent"]
    assert get_risky_analyzer_fields("agent") == []
