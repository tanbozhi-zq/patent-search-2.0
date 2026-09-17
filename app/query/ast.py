"""AST 是解析器和 DSL 构建器之间的稳定中间表示。节点不可变，避免构建查询时
意外修改已经解析好的子树，也让复杂度统计只发生在 Parser._node。
"""

from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class WordNode:
    """不带字段或引号的查询值，DSL 层可将完整 IPC 识别为专用检索。"""

    # 裸词可能是普通全文词，也可能在 DSL 阶段被识别为完整 IPC。
    value: str


@dataclass(frozen=True)
class PhraseNode:
    """保留引号内原文的短语节点，实际匹配策略由字段 DSL 决定。"""

    # 短语保留引号内原文；是否允许 slop 由字段和语言在 DSL 层决定。
    value: str


@dataclass(frozen=True)
class FieldQuery:
    """将一个字段限定到任意子查询树的节点，支持嵌套布尔表达式。"""

    # 字段值本身仍是一棵 QueryNode，因此 field:(a OR b) 可以复用布尔语法。
    field: str
    value: "QueryNode"


@dataclass(frozen=True)
class RangeQuery:
    """尚未解释具体字段类型的闭区间节点，由 DSL 层映射为日期范围查询。"""

    # 范围只在 DSL 层映射到日期字段；parser 先保留字符串，避免把非法日期当成词。
    field: str
    start: str
    end: str


@dataclass(frozen=True)
class AndNode:
    """显式逻辑与的二叉节点；查询语言不提供隐式 AND。"""

    # 显式 AND 形成二叉树；没有隐式 AND，调用方必须写出运算符。
    left: "QueryNode"
    right: "QueryNode"


@dataclass(frozen=True)
class OrNode:
    """逻辑或的二叉节点，其优先级由 parser 固定为低于 AND。"""

    # OR 在 parser 中优先级低于 AND，与常见布尔检索语义一致。
    left: "QueryNode"
    right: "QueryNode"


@dataclass(frozen=True)
class NotNode:
    """前缀逻辑非节点；parser 会独立约束连续 NOT 的嵌套深度。"""

    # NOT 是前缀一元节点，parser 会单独计算其嵌套深度。
    child: "QueryNode"


QueryNode = Union[WordNode, PhraseNode, FieldQuery, RangeQuery, AndNode, OrNode, NotNode]
