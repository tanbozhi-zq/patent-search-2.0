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
    assert {"term": {"LatestLegalStatus": "授权"}} in should
    assert {"term": {"LatestLegalStatus": "有效"}} in should
    assert clause["bool"]["minimum_should_match"] == 1


def test_unknown_legal_status_falls_back_to_text_match():
    assert build_legal_status_clause("部分无效") == {
        "bool": {
            "should": [
                {"term": {"LatestLegalStatus": "部分无效"}},
                {"term": {"LegalStatus": "部分无效"}},
            ],
            "minimum_should_match": 1,
        }
    }
