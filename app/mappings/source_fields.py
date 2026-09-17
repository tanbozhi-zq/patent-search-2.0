"""_source 列表是读路径的成本边界，也是字段契约的集中清单。搜索列表、详情和
目标排名分别只取自己需要的字段；不要为了方便把整个文档 source 拉回应用。
"""

SEARCH_SOURCE_FIELDS = (
    "patent_id",
    "ApplicationNumber",
    "PublicationNumber",
    "Title",
    "Abstract",
    "Applicant",
    "Assignee",
    "Inventor",
    "IPC",
    "IPCSection",
    "IPCLargeCategory",
    "IPCSmallCategory",
    "IPCLargeGroup",
    "IPCSmallGroup",
    "IPCList",
    "MainClaim",
    "MainClaimCN",
    "MainClaimEN",
    "ApplicationDate",
    "PublicationDate",
    "LatestLegalStatus",
    "LegalStatus",
    "Type",
    "PatentTypeCode",
    "Kind",
    "PublicationCountry",
)

DETAIL_SOURCE_FIELDS = (
    # 详情复用搜索字段，再追加申请人/权利要求/图片/家族等重字段。
    *SEARCH_SOURCE_FIELDS,
    "FirstApplicant",
    "FirstInventor",
    "ApplicantAddress",
    "Agency",
    "Agent",
    "IndependentClaimsCN",
    "IndependentClaimsOriginal",
    "IndependentClaimsEN",
    "Requirement",
    "RequirementCN",
    "RequirementEN",
    "Priority",
    "PCT",
    "PCTApplicationDate",
    "PCTApplicationNumber",
    "PCTPublicationNumber",
    "AbstractFigureUrl",
    "PatentImage",
    "PatentImages",
    "Family",
    "SimpleFamily",
    "ExtendedFamily",
    "DocDBFamily",
)


def detail_source_fields(include_description: bool) -> tuple[str, ...]:
    # 说明书只有显式 include_description=true 时才加载，避免默认详情响应携带大文本。
    if include_description:
        return (*DETAIL_SOURCE_FIELDS, "Instructions")
    return DETAIL_SOURCE_FIELDS


TARGET_RANK_SOURCE_FIELDS = (
    # 目标排名只需要稳定身份、标题和排序日期，不需要列表/详情全文字段。
    "patent_id",
    "PublicationNumber",
    "Title",
    "ApplicationDate",
    "PublicationDate",
)
