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
