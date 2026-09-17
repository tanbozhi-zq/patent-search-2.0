"""Schema 是第一道输入边界：只做类型、长度、枚举和分页基本约束；查询式的
语法、AST 复杂度和结果窗口仍由 query.budget/parser 在统一入口处理。
"""

from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    model_validator,
)

from app.mappings.query_field_mapping import VECTOR_FIELD_REGISTRY
from app.query.budget import HARD_QUERY_BUDGET


SearchMode = Literal["boolean", "vector", "hybrid"]
DEFAULT_TOP_K = 100
MAX_TOP_K = 1000
MAX_HYBRID_SUBQUERIES = 5


QueryText = Annotated[
    str,
    Field(min_length=1, max_length=HARD_QUERY_BUDGET.max_query_chars),
]
SemanticText = Annotated[
    str,
    Field(
        min_length=1,
        max_length=HARD_QUERY_BUDGET.max_query_chars,
        pattern=r"\S",
    ),
    AfterValidator(str.strip),
]
VectorFieldName = Annotated[str, Field(min_length=1)]
TopK = Annotated[int, Field(ge=1, le=MAX_TOP_K, strict=True)]


class _SearchRequestBase(BaseModel):
    """三种搜索模式共享的分页、数据集和排序字段。"""

    model_config = ConfigDict(extra="forbid")

    ds: str = Field(default="cn", pattern="^([Aa][Ll][Ll]|[A-Za-z]{2})$")
    sort: str = Field(
        default="relation",
        pattern="^(relation|rank|relevance|score|!?applicationDate|!?documentDate)$",
    )
    page: int = Field(default=1, ge=1)
    page_size: int = Field(
        default=50,
        ge=1,
        le=HARD_QUERY_BUDGET.max_page_size,
    )
    highlight: int = Field(default=0, ge=0, le=1)

    @property
    def offset(self) -> int:
        # 页码对外从 1 开始，OpenSearch 的 from 从 0 开始；预算校验在使用前
        # 负责保证这个乘法结果仍处于允许的结果窗口内。
        return (self.page - 1) * self.page_size


class BooleanSearchRequest(_SearchRequestBase):
    """现有布尔请求；不声明向量字段，因此 extra=forbid 会明确拒绝它们。"""

    mode: Literal["boolean"] = "boolean"
    q: QueryText


class _SemanticSearchRequestBase(_SearchRequestBase):
    """vector/hybrid 共用的语义文本、注册字段和 top_k 合同。"""

    mode: SearchMode
    semantic_text: SemanticText
    vector_fields: list[VectorFieldName]
    top_k: TopK = DEFAULT_TOP_K

    def __repr_args__(self):
        # 避免调试 repr 或异常日志带出完整语义文本。
        return [
            (name, value)
            for name, value in super().__repr_args__()
            if name != "semantic_text"
        ]

    @model_validator(mode="after")
    def validate_registered_vector_fields(self) -> "_SemanticSearchRequestBase":
        """字段值随注册表扩展，通用请求模型不冻结当前三个公开名。"""
        if len(set(self.vector_fields)) != len(self.vector_fields):
            raise ValueError("vector_fields must not contain duplicates")

        unknown_fields = set(self.vector_fields) - VECTOR_FIELD_REGISTRY.keys()
        if unknown_fields:
            raise ValueError("vector_fields contains an unregistered field")
        return self


class VectorSearchRequest(_SemanticSearchRequestBase):
    mode: Literal["vector"]
    vector_fields: list[VectorFieldName] = Field(
        min_length=1,
        max_length=MAX_HYBRID_SUBQUERIES,
        json_schema_extra={"uniqueItems": True},
    )


class HybridSearchRequest(_SemanticSearchRequestBase):
    mode: Literal["hybrid"]
    q: QueryText
    vector_fields: list[VectorFieldName] = Field(
        min_length=1,
        max_length=MAX_HYBRID_SUBQUERIES - 1,
        json_schema_extra={"uniqueItems": True},
    )


_SearchRequestVariant = Annotated[
    BooleanSearchRequest | VectorSearchRequest | HybridSearchRequest,
    Field(discriminator="mode"),
]


class SearchRequest(RootModel[_SearchRequestVariant]):
    """统一搜索请求；OpenAPI 以 mode 判别的 oneOf 精确描述三个分支。

    省略 mode 时在进入判别联合前补为 boolean，保留旧客户端行为。RootModel
    序列化为扁平请求对象，因此默认 ``model_dump()`` 可以直接重新校验。
    """

    @model_validator(mode="before")
    @classmethod
    def default_boolean_mode(cls, value):
        if isinstance(value, dict) and "mode" not in value:
            return {"mode": "boolean", **value}
        return value

    def __init__(self, **data):
        # 保留大量内部调用使用的 SearchRequest(q=...) 构造方式。
        super().__init__(root=data)

    def __iter__(self):
        # 保留原 BaseModel 的 dict(request) 扁平行为，不暴露 RootModel 包装层。
        return iter(self.root)

    def model_copy(self, *, update=None, deep=False) -> "SearchRequest":
        # Pydantic RootModel 默认只接受 root 更新；兼容原 SearchRequest 的扁平字段更新。
        if not update:
            return super().model_copy(deep=deep)
        payload = self.model_dump()
        payload.update(update)
        return type(self).model_validate(payload)

    @property
    def mode(self) -> SearchMode:
        return self.root.mode

    @property
    def q(self) -> str | None:
        return getattr(self.root, "q", None)

    @property
    def semantic_text(self) -> str | None:
        return getattr(self.root, "semantic_text", None)

    @property
    def vector_fields(self) -> list[str] | None:
        return getattr(self.root, "vector_fields", None)

    @property
    def top_k(self) -> int | None:
        return getattr(self.root, "top_k", None)

    @property
    def ds(self) -> str:
        return self.root.ds

    @property
    def sort(self) -> str:
        return self.root.sort

    @property
    def page(self) -> int:
        return self.root.page

    @property
    def page_size(self) -> int:
        return self.root.page_size

    @property
    def highlight(self) -> int:
        return self.root.highlight

    @property
    def offset(self) -> int:
        return self.root.offset


class TargetRankRequest(BaseModel):
    """目标专利排名接口的请求模型，不承载普通分页参数。

    ``target_identifier`` 与查询/数据集/排序共同确定排名语境；服务层会先解析查询
    并定位目标，再给出匹配、歧义或未进入结果集等不同状态。
    """

    # 目标排名沿用搜索的 q/ds/sort 约束，但不接受 page/page_size，因为排名
    # 是对一个指定目标计算，而不是普通分页读取。
    model_config = ConfigDict(extra="forbid")

    q: str = Field(
        min_length=1,
        max_length=HARD_QUERY_BUDGET.max_query_chars,
    )
    ds: str = Field(default="cn", pattern="^([Aa][Ll][Ll]|[A-Za-z]{2})$")
    sort: str = Field(
        default="relation",
        pattern="^(relation|rank|relevance|score|!?applicationDate|!?documentDate)$",
    )
    target_identifier: str = Field(min_length=1, max_length=200)
