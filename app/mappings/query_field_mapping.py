TEXT_FIELD_MAPPING = {
    "title": ["Title", "TitleCN", "TitleEN"],
    "ab": ["Abstract", "AbstractCN", "AbstractEN"],
    "tscd": ["Title", "Abstract", "MainClaim", "Requirement", "Instructions"],
    "mainClaim": ["MainClaim"],
    "claims": ["Requirement"],
    "description": ["Instructions"],
    "applicant": ["Applicant", "ApplicantNormalized", "FirstApplicant"],
    "currentAssignee": ["Assignee", "AssigneeNormalized"],
    "agency": ["Agency", "AgencyRaw"],
    "agent": ["Agent"],
    "type": ["Type", "PatentTypeCode", "Kind"],
}

NORMAL_ANALYZER_FIELDS_BY_QUERY_FIELD = {
    "title": ["Title", "TitleEN"],
    "ab": ["Abstract", "AbstractEN"],
    "tscd": ["Title", "Abstract"],
    "mainClaim": [],
    "claims": [],
    "description": [],
    "applicant": [],
    "currentAssignee": [],
    "agency": [],
    "agent": ["Agent"],
    "type": ["PatentTypeCode", "Kind"],
}

RISKY_ANALYZER_FIELDS_BY_QUERY_FIELD = {
    "title": ["TitleCN"],
    "ab": ["AbstractCN"],
    "tscd": ["MainClaim", "Requirement", "Instructions"],
    "mainClaim": ["MainClaim"],
    "claims": ["Requirement"],
    "description": ["Instructions"],
    "applicant": ["Applicant", "ApplicantNormalized", "FirstApplicant"],
    "currentAssignee": ["Assignee", "AssigneeNormalized"],
    "agency": ["Agency", "Agency.keyword", "AgencyRaw"],
    "type": ["Type"],
}

IPC_FIELDS = ["IPC", "IPCList", "IPCSmallCategory", "IPCLargeGroup", "IPCSmallGroup"]
MAIN_IPC_FIELD = "mainIpc"
IDENTIFIER_FIELD_MAPPING = {
    "applicationNumber": ["ApplicationNumber", "ApplicationNumberAliases"],
    "documentNumber": [
        "PublicationNumber",
        "PublicationNumberAliases",
        "FirstPublicationNumber",
        "GrantPublicationNumber",
    ],
    "publicationNumber": [
        "PublicationNumber",
        "PublicationNumberAliases",
        "FirstPublicationNumber",
        "GrantPublicationNumber",
    ],
    "patentId": ["patent_id"],
}
RANGE_FIELDS = {"ad", "documentYear"}
LEGAL_STATUS_FIELD = "legalStatus"
SUPPORTED_FIELDS = (
    set(TEXT_FIELD_MAPPING)
    | {"ipc", MAIN_IPC_FIELD, LEGAL_STATUS_FIELD}
    | set(IDENTIFIER_FIELD_MAPPING)
    | RANGE_FIELDS
)


def get_normal_analyzer_fields(query_field: str) -> list[str]:
    return NORMAL_ANALYZER_FIELDS_BY_QUERY_FIELD.get(query_field, TEXT_FIELD_MAPPING.get(query_field, []))


def get_risky_analyzer_fields(query_field: str) -> list[str]:
    return RISKY_ANALYZER_FIELDS_BY_QUERY_FIELD.get(query_field, [])
