"""这些模型是 HTTP 和 MCP 之间的稳定数据契约。内部 OpenSearch 字段名、数组
形态和兼容别名都应在 mappings 层消化，不能让它们穿透到这里。
"""

from typing import Any, Generic, Literal, Optional, TypeVar

from pydantic import BaseModel

from app.core.exceptions import ErrorCode


T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """旧式健康与兼容端点使用的通用响应信封。

    新的检索接口使用专用响应模型，以避免把所有业务字段塞进一个可选 ``data``
    容器；保留此模型是为了维持既有轻量端点的稳定形状。
    """

    # 仅保留旧式健康/兼容接口需要的通用信封；业务搜索成功响应使用专用模型。
    success: bool
    code: int
    message: str
    data: Optional[T] = None


class ErrorResponse(BaseModel):
    """所有 HTTP 失败路径共享的可机器处理错误响应。

    ``code``、``retryable`` 和可选响应头共同构成客户端的重试契约；``request_id``
    必须与响应头一致，供调用方在日志、MCP 和后端链路间关联一次请求。
    """

    # 错误码是机器契约，message 只用于人类阅读；request_id 与响应头必须一致。
    success: Literal[False] = False
    code: ErrorCode
    message: str
    data: None = None
    request_id: str
    retryable: bool


class PatentSearchRecord(BaseModel):
    """搜索列表中一条轻量专利记录的固定字段集合。

    所有字段都有值，即使源索引缺失内容也由 mapper 填入约定空值；大字段如完整
    权利要求与说明书不属于列表契约，调用方需要时应改用详情接口。
    """

    # 搜索列表故意是固定字段集，不返回完整权利要求和说明书等重内容。
    id: str
    application_number: str
    publication_number: str
    title: str
    abstract: str
    applicant: str
    current_assignee: str
    inventor: str
    main_ipc: str
    ipc_list: list[str]
    main_claim: str
    application_date: str
    publication_date: str
    legal_status: str
    type: str
    score: float | None


class SearchContext(BaseModel):
    """向量/混合响应公开的稳定执行上下文，不含向量或内部候选。"""

    mode: Literal["vector", "hybrid"]
    vector_fields: list[str]
    top_k: int
    ranking_profile: str
    sort: str


class SearchResponse(BaseModel):
    """普通分页检索的总数、页信息、耗时与标准化记录列表。"""

    # total_pages 为 0 表示没有命中，next_page 用 null 表示没有后续页。
    total: int
    page: int
    page_size: int
    total_pages: int
    accessible_pages: int
    next_page: int | None
    took_ms: int | None
    records: list[PatentSearchRecord]
    search_context: SearchContext | None = None


class TargetRankTarget(BaseModel):
    patent_id: str
    documentNumber: str
    title: str


class TargetRankResponse(BaseModel):
    """目标专利在给定查询与排序下的定位结果。

    ``status`` 说明目标不存在、歧义、未落入可查结果或已匹配；只有 ``matched``
    语境下 rank/排序值才具有完整业务意义，调用方不应把空值误读为第零名。
    """

    status: Literal["target_not_found", "ambiguous_target", "not_in_results", "matched"]
    in_results: bool
    rank: int | None
    tied_count: int
    sort_value: float | int | str | None
    target: TargetRankTarget | None


class PatentDetailResponse(BaseModel):
    """一件专利的按可靠性稀疏输出详情。

    除 ID 外的字段都允许缺失，表示索引中没有足以安全展示的值而不是空字符串数据；
    ``description`` 仅在接口调用方显式请求后才会由 mapper 纳入响应。
    """

    # 详情字段全部可选（id 除外），映射器会在没有可靠值时省略字段。
    id: str
    application_number: str | None = None
    publication_number: str | None = None
    title: str | None = None
    abstract: str | None = None
    applicant: str | None = None
    first_applicant: str | None = None
    current_assignee: str | None = None
    inventor: str | None = None
    first_inventor: str | None = None
    applicant_address: str | None = None
    agency: str | None = None
    agent: str | None = None
    main_ipc: str | None = None
    ipc_list: list[str] | None = None
    main_claim: str | None = None
    independent_claims: str | None = None
    claims: str | None = None
    application_date: str | None = None
    publication_date: str | None = None
    legal_status: str | None = None
    type: str | None = None
    priority_numbers: list[str] | None = None
    pct_application_date: str | None = None
    pct_application_number: str | None = None
    pct_publication_number: str | None = None
    image_path: str | None = None
    images: list[str] | None = None
    family: list[Any] | None = None
    description: str | None = None


class PatentCitationRecord(BaseModel):
    id: str
    title: str
    applicant: str
    application_date: str
    application_number: str
    type: str
    legal_status: str
    main_ipc: str


class CitationResponse(BaseModel):
    """归一化引证摘要与历史兼容引用字段并存的响应模型。

    前三个列表适合新客户端直接显示；大小写敏感的旧字段保留给需要原始兼容结构的
    调用方，不能假定它们与摘要列表一一对应。
    """

    # cited_by/patent_references 是新的归一化摘要；后四个字段保留历史兼容数据。
    patent_id: str
    cited_by: list[PatentCitationRecord]
    patent_references: list[PatentCitationRecord]
    non_patent_references: list[str]
    referencesCited: list[Any]
    referencesCitedRaw: str
    referencesCitedText: str
    relatedDocuments: list[Any]


class LegalHistoryResponse(BaseModel):
    """专利法律历史的最小稳定外壳，保留来源交易条目。"""

    # transactions 保留来源条目，以免法律历史接口在服务层擅自丢失供应商字段。
    patent_id: str
    transaction_count: int
    transactions: list[Any]


class HealthData(BaseModel):
    status: Literal["healthy"]
    service: Literal["patent-search-service"]


class HealthResponse(BaseModel):
    """轻量健康检查的固定成功响应，不包含外部依赖诊断。"""

    success: Literal[True]
    code: Literal[0]
    message: Literal["ok"]
    data: HealthData


class ProbeResponse(BaseModel):
    """供编排系统消费的最小生命周期或依赖探针结果。"""

    # 探针故意使用最小模型，避免把内部依赖信息放进编排接口。
    status: str
