"""查询预算同时保护请求体、语法解析和 OpenSearch result window。运行配置只能
收紧它；硬上限写在代码里，避免运维配置无意间把解析/资源边界放大。
"""

from dataclasses import dataclass, fields
from typing import Protocol

from app.core.exceptions import (
    PaginationOutOfRangeError,
    QueryComplexityError,
)


@dataclass(frozen=True, slots=True)
class QueryBudget:
    """一次请求使用的不可变查询资源合同。"""

    max_request_body_bytes: int
    max_query_chars: int
    max_nesting_depth: int
    max_tokens: int
    max_ast_nodes: int
    max_boolean_clauses: int
    max_page_size: int
    max_result_window: int

    def __post_init__(self) -> None:
        # 先验证每个值为正，再验证字段之间的关系；否则 page_size 可能合法但
        # result_window 太小，最终仍会产生无法服务的分页区间。
        for field in fields(self):
            if getattr(self, field.name) < 1:
                raise ValueError(f"{field.name} must be greater than or equal to 1")
        if self.max_result_window < self.max_page_size:
            raise ValueError(
                "max_result_window must be greater than or equal to max_page_size"
            )

    def validate_query_length(self, query: str) -> None:
        # 这是字符数预算，不等同于 UTF-8 字节数；请求体字节预算由 ASGI 中间件负责。
        if len(query) > self.max_query_chars:
            raise QueryComplexityError

    def max_accessible_page(self, *, page_size: int) -> int:
        """返回给定页大小在 result window 内可读取的最大页码。

        这将 OpenSearch 的 ``from + size`` 上限显式投影为分页契约；页大小无效时
        立即拒绝，调用方不能仅凭总命中数推断可继续翻页。
        """
        if page_size < 1 or page_size > self.max_page_size:
            raise PaginationOutOfRangeError
        return self.max_result_window // page_size

    def validate_pagination(self, *, page: int, page_size: int) -> None:
        """校验页大小和页码均未越过本次预算允许的结果窗口。"""
        # 先通过 max_accessible_page 统一检查单页上限与累计窗口，避免只限制
        # page_size 却允许请求落到 OpenSearch 无法返回的深分页区域。
        if page > self.max_accessible_page(page_size=page_size):
            raise PaginationOutOfRangeError

    def ensure_within(self, hard_limit: "QueryBudget") -> None:
        # 每个字段逐一比较，便于错误明确指出是哪条部署配置越过代码边界。
        for field in fields(self):
            value = getattr(self, field.name)
            hard_value = getattr(hard_limit, field.name)
            if value > hard_value:
                raise ValueError(
                    f"{field.name}={value} exceeds code hard limit {hard_value}"
                )


# 提高硬上限会改变服务的资源安全边界，需要代码 review 和代表性查询测量；运行
# 配置只能保持或收紧这些值，不能仅靠环境变量放宽。
HARD_QUERY_BUDGET = QueryBudget(
    max_request_body_bytes=16 * 1024,
    max_query_chars=1000,
    max_nesting_depth=32,
    max_tokens=256,
    max_ast_nodes=256,
    max_boolean_clauses=128,
    max_page_size=100,
    max_result_window=10_000,
)
DEFAULT_QUERY_BUDGET = HARD_QUERY_BUDGET


class QueryBudgetProvider(Protocol):
    """为一次请求提供不可变预算快照的抽象边界。

    请求处理过程中不能反复读取可变配置，否则同一条查询可能在解析、分页和 DSL
    构建之间使用不同阈值。
    """

    def snapshot(self) -> QueryBudget:
        """返回覆盖完整请求生命周期的一份不可变预算。"""


@dataclass(frozen=True, slots=True)
class StaticQueryBudgetProvider:
    """始终返回同一预算的默认 provider，供生产启动和测试替换共用。"""

    # 当前实现是静态 provider；保留协议是为了让未来的运行时配置/测试替换仍需
    # 遵守同一个 snapshot 契约，而不是在请求中直接读取可变 Settings。
    budget: QueryBudget

    def __post_init__(self) -> None:
        # 即使 provider 由测试或其他调用方直接构造，也不能绕过硬上限。
        self.budget.ensure_within(HARD_QUERY_BUDGET)

    def snapshot(self) -> QueryBudget:
        return self.budget


DEFAULT_QUERY_BUDGET_PROVIDER = StaticQueryBudgetProvider(DEFAULT_QUERY_BUDGET)
