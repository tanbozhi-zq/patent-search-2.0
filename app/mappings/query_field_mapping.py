TEXT_FIELD_MAPPING = {
    "title": ["Title", "TitleCN", "TitleEN"],
    "ab": ["Abstract", "AbstractCN", "AbstractEN"],
    "tscd": ["Title", "Abstract", "MainClaim", "Requirement", "Instructions"],
    "mainClaim": ["MainClaim"],
    "claims": ["Requirement"],
    "description": ["Instructions"],
    "applicant": ["Applicant", "ApplicantNormalized", "FirstApplicant"],
    "currentAssignee": ["Assignee", "AssigneeNormalized"],
    "type": ["Type", "PatentTypeCode", "Kind"],
}

NORMAL_ANALYZER_FIELDS_BY_QUERY_FIELD = {
    "title": ["Title", "TitleEN"],
    "ab": ["Abstract", "AbstractEN"],
    "tscd": ["Title", "Abstract"],
    "mainClaim": [],
    "claims": [],
    "description": [],
    "applicant": ["Applicant", "ApplicantNormalized", "FirstApplicant"],
    "currentAssignee": ["Assignee", "AssigneeNormalized"],
    "type": ["PatentTypeCode", "Kind"],
}

RISKY_ANALYZER_FIELDS_BY_QUERY_FIELD = {
    "title": ["TitleCN"],
    "ab": ["AbstractCN"],
    "tscd": ["MainClaim", "Requirement", "Instructions"],
    "mainClaim": ["MainClaim"],
    "claims": ["Requirement"],
    "description": ["Instructions"],
    "type": ["Type"],
}

IPC_FIELDS = ["IPC", "IPCList", "IPCSmallCategory", "IPCLargeGroup", "IPCSmallGroup"]
RANGE_FIELDS = {"ad", "documentYear"}
LEGAL_STATUS_FIELD = "legalStatus"
SUPPORTED_FIELDS = set(TEXT_FIELD_MAPPING) | {"ipc", LEGAL_STATUS_FIELD} | RANGE_FIELDS


def get_normal_analyzer_fields(query_field: str) -> list[str]:
    return NORMAL_ANALYZER_FIELDS_BY_QUERY_FIELD.get(query_field, TEXT_FIELD_MAPPING.get(query_field, []))


def get_risky_analyzer_fields(query_field: str) -> list[str]:
    return RISKY_ANALYZER_FIELDS_BY_QUERY_FIELD.get(query_field, [])
