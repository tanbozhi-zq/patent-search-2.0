TEXT_FIELD_MAPPING = {
    "title": ["Title", "TitleCN", "TitleEN"],
    "ab": ["Abstract", "AbstractCN", "AbstractEN"],
    "tscd": ["Title", "Abstract", "MainClaim", "Requirement", "Instructions"],
    "applicant": ["Applicant", "ApplicantNormalized", "FirstApplicant"],
    "currentAssignee": ["Assignee", "AssigneeNormalized"],
    "type": ["PatentType"],
}

IPC_FIELDS = ["IPC", "IPCList", "IPCSmallCategory", "IPCLargeGroup", "IPCSmallGroup"]
RANGE_FIELDS = {"ad", "documentYear"}
LEGAL_STATUS_FIELD = "legalStatus"
SUPPORTED_FIELDS = set(TEXT_FIELD_MAPPING) | {"ipc", LEGAL_STATUS_FIELD} | RANGE_FIELDS
