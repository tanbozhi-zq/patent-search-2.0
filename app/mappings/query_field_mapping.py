"""用户可见字段到 serving mapping 字段的唯一映射表。DSL builder 只接受这里的
白名单，新增查询字段必须同时考虑 analyzer、source 数据和 API 文档/测试。
"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping


@dataclass(frozen=True, slots=True)
class VectorFieldDefinition:
    """一个稳定业务名对应的向量检索契约。"""

    public_name: str
    opensearch_field: str
    dimensions: int
    space_type: str
    embedding_model: str


DEFAULT_VECTOR_EMBEDDING_MODEL: Final = "doubao-embedding-vision-250615"


VECTOR_FIELD_REGISTRY: Final[Mapping[str, VectorFieldDefinition]] = MappingProxyType(
    {
        "abstract": VectorFieldDefinition(
            public_name="abstract",
            opensearch_field="AbstractVector1024",
            dimensions=1024,
            space_type="cosinesimil",
            embedding_model=DEFAULT_VECTOR_EMBEDDING_MODEL,
        ),
        "main_claim": VectorFieldDefinition(
            public_name="main_claim",
            opensearch_field="MainClaimVector1024",
            dimensions=1024,
            space_type="cosinesimil",
            embedding_model=DEFAULT_VECTOR_EMBEDDING_MODEL,
        ),
        "independent_claims": VectorFieldDefinition(
            public_name="independent_claims",
            opensearch_field="IndependentClaimsVector1024",
            dimensions=1024,
            space_type="cosinesimil",
            embedding_model=DEFAULT_VECTOR_EMBEDDING_MODEL,
        ),
    }
)

TEXT_FIELD_MAPPING = {
    "title": ["Title", "TitleCN", "TitleEN"],
    "ab": ["Abstract", "AbstractCN", "AbstractEN"],
    "tscd": [
        "Title",
        "TitleCN",
        "TitleEN",
        "Abstract",
        "AbstractCN",
        "AbstractEN",
        "MainClaim",
        "MainClaimCN",
        "MainClaimEN",
        "Requirement",
        "RequirementCN",
        "RequirementEN",
        "Instructions",
    ],
    "mainClaim": ["MainClaim", "MainClaimCN", "MainClaimEN"],
    "claims": ["Requirement", "RequirementCN", "RequirementEN"],
    "description": ["Instructions"],
    "independentClaims": ["IndependentClaimsCN", "IndependentClaimsEN"],
    "dependentClaims": ["DependentClaimsCN", "DependentClaimsEN"],
    "applicant": ["Applicant", "ApplicantNormalized", "FirstApplicant"],
    "currentAssignee": ["Assignee", "AssigneeNormalized"],
    "inventor": ["Inventor"],
    "agency": ["Agency", "AgencyRaw"],
    "agent": ["Agent"],
    "type": ["Type", "PatentTypeCode", "Kind"],
}

MAIN_IPC_FIELD = "mainIpc"
# 标识符查询使用多个历史别名字段，以兼容不同版本索引中的同一业务编号。
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
# SUPPORTED_FIELDS 是 parser 之后的第二道字段边界；tokenizer 接受任意词，但 DSL
# 只有在这里登记的字段才能进入 OpenSearch。
LEGAL_STATUS_FIELD = "legalStatus"
SUPPORTED_FIELDS = (
    set(TEXT_FIELD_MAPPING)
    | {"ipc", MAIN_IPC_FIELD, LEGAL_STATUS_FIELD}
    | set(IDENTIFIER_FIELD_MAPPING)
    | RANGE_FIELDS
)
