"""DSL builder 是查询语义的最后一层：AST 在这里映射到 OpenSearch bool/multi_match/
term/range/sort。字段白名单、IPC 层级和来源字段都集中在此处，避免路由直接拼 DSL。
"""

import re
from datetime import date
from enum import Enum

from app.core.exceptions import QuerySyntaxError
from app.mappings.legal_status_mapping import build_legal_status_clause
from app.mappings.source_fields import SEARCH_SOURCE_FIELDS
from app.mappings.query_field_mapping import (
    IDENTIFIER_FIELD_MAPPING,
    LEGAL_STATUS_FIELD,
    MAIN_IPC_FIELD,
    SUPPORTED_FIELDS,
    TEXT_FIELD_MAPPING,
)
from app.query.ast import AndNode, FieldQuery, NotNode, OrNode, PhraseNode, QueryNode, RangeQuery, WordNode
from app.query.budget import DEFAULT_QUERY_BUDGET, QueryBudget
from app.query.ipc import looks_like_ipc, normalize_ipc
from app.query.parser import parse_query
from app.query.text_field_router import (
    ROUTED_TEXT_QUERY_FIELDS,
    route_text_leaf,
    routed_text_fields,
)
from app.schemas.search import SearchRequest


_TSCD_HAN_PHRASE_SLOP = 1
_HAN_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


class TextQueryStrategy(str, Enum):
    """A/B/C 实验使用的文本字段与跨语言 OR 组合策略。"""

    BASELINE = "baseline"
    ROUTED_BOOL = "routed_bool"
    ROUTED_DIS_MAX = "routed_dis_max"


def build_search_dsl(
    request: SearchRequest,
    budget: QueryBudget = DEFAULT_QUERY_BUDGET,
    *,
    text_query_strategy: TextQueryStrategy = TextQueryStrategy.ROUTED_DIS_MAX,
) -> dict:
    """将已校验的搜索请求编译为列表查询使用的 OpenSearch DSL。

    该函数不执行 I/O：它先验证分页、解析 ``q`` 并把 AST 映射为受字段白名单约束的
    bool 查询，同时固定列表所需的 ``_source`` 和排序。语法或预算错误会在发请求前抛出。
    """
    # 先校验分页，再解析 q；任何语法/预算错误都在发出 OpenSearch 请求前抛出。
    # _source 固定为公共列表所需字段，防止搜索列表意外拉取完整说明书。
    budget.validate_pagination(page=request.page, page_size=request.page_size)
    must = [
        _build_node_clause(
            parse_query(request.q, budget=budget),
            text_query_strategy=text_query_strategy,
            allow_dis_max=True,
        )
    ]
    filters = []

    ds = request.ds.lower()
    if ds != "all":
        filters.append({"term": {"PublicationCountry": ds.upper()}})

    return {
        "from": request.offset,
        "size": request.page_size,
        "_source": list(SEARCH_SOURCE_FIELDS),
        "track_total_hits": True,
        "query": {
            "bool": {
                "must": must,
                "filter": filters,
            }
        },
        "sort": build_search_sort(request.sort),
    }


def build_target_rank_dsl(
    request,
    identifier_field: str,
    target: dict,
    budget: QueryBudget = DEFAULT_QUERY_BUDGET,
) -> dict:
    """为目标专利排名准备基础命中、严格更优和并列计数所需的查询集合。

    相关性排序与日期排序的“更优”定义不同，因此返回的是供 Service 分步执行的
    查询材料，而不是直接给出排名；函数不读取 OpenSearch，也不会猜测缺失日期。
    """
    # 目标排名复用同一基础查询，但额外构造身份、命中、优于目标和并列四个子查询。
    # SearchService 负责按 sort 类型选择真正执行的计数路径。
    base = build_search_dsl(
        SearchRequest(q=request.q, ds=request.ds, sort=request.sort, page=1, page_size=1),
        budget=budget,
    )
    base_query = base["query"]
    target_source = target.get("_source", {})
    target_identity = target_source.get("patent_id")
    identity_field = "patent_id" if target_identity else identifier_field
    identity_value = target_identity or target_source.get("PublicationNumber")
    identity_clause = {"term": {identity_field: identity_value}}
    sort_value = None

    relevance_sort = request.sort in {"relation", "rank", "relevance", "score"}
    if relevance_sort:
        # 相关性排序的“更好”由 min_score 计算，日期排序则需要明确 field/range。
        sort_value = target.get("_score")
        better_query = None
        tied_query = None
    else:
        field, descending = _date_sort_details(request.sort)
        sort_value = target_source.get(field)
        if sort_value:
            # 有日期的目标：更优文档按排序方向比较日期，同日期文档排除目标自身后计数。
            better_operator = "gt" if descending else "lt"
            tie_query = {"term": {field: sort_value}}
            better_query = {
                "bool": {
                    "must": [base_query, {"range": {field: {better_operator: sort_value}}}],
                }
            }
            tied_query = {
                "bool": {
                    "must": [base_query, tie_query],
                    "must_not": [identity_clause],
                }
            }
        else:
            # 目标没有日期时，无法用一个具体日期做 tie；按“有日期优先/无日期并列”
            # 的现有契约构建两个查询，保持排名可解释且不伪造日期。
            better_query = {
                "bool": {
                    "must": [base_query],
                    "filter": [{"exists": {"field": field}}],
                }
            }
            tied_query = {
                "bool": {
                    "must": [base_query],
                    "must_not": [identity_clause, {"exists": {"field": field}}],
                }
            }

    return {
        "base_query": base_query,
        "identity_clause": identity_clause,
        "match_query": {"query": {"bool": {"must": [base_query, identity_clause]}}},
        "better_query": {"query": better_query},
        "tied_query": {"query": tied_query},
        "sort_value": sort_value,
        "relevance_sort": relevance_sort,
    }


def _date_sort_details(sort: str) -> tuple[str, bool]:
    # 返回底层字段名和是否降序；调用方只处理已被 schema 限定过的 sort 值。
    if sort in {"applicationDate", "!applicationDate"}:
        return "ApplicationDate", sort.startswith("!")
    return "PublicationDate", sort.startswith("!")


def _build_node_clause(
    node: QueryNode,
    *,
    text_query_strategy: TextQueryStrategy,
    allow_dis_max: bool,
) -> dict:
    """递归把无字段的查询 AST 节点转换为等价 OpenSearch 子句。

    该层决定 AND/OR/NOT 的 bool 语义及裸词、短语、完整 IPC 的默认查询方式；字段
    限定和范围语义委托给专用 builder，未知节点视为内部语法错误。
    """
    # 裸词的 IPC 识别只接受完整代码；显式 ipc: 才允许 group family，保持
    # “H02M” 裸词按全文/IPC 兼容和 “ipc:H02M” 按分类语义的边界。
    if isinstance(node, WordNode):
        if looks_like_ipc(node.value):
            return _build_ipc_clause(node.value, "ipc")
        fields = (
            ["Title", "Abstract"]
            if text_query_strategy is TextQueryStrategy.BASELINE
            else routed_text_fields(node.value)
        )
        return _multi_match(node.value, fields)
    if isinstance(node, PhraseNode):
        fields = (
            ["Title", "Abstract"]
            if text_query_strategy is TextQueryStrategy.BASELINE
            else routed_text_fields(node.value)
        )
        return _phrase_multi_match(node.value, fields)
    if isinstance(node, FieldQuery):
        return _build_field_clause(
            node,
            text_query_strategy=text_query_strategy,
            allow_dis_max=allow_dis_max,
        )
    if isinstance(node, RangeQuery):
        return _build_range_clause(node)
    if isinstance(node, AndNode):
        # 二叉 AST 直接映射为 bool.must；没有隐式 AND，所有结构都来自用户显式语法。
        return {
            "bool": {
                "must": [
                    _build_node_clause(
                        node.left,
                        text_query_strategy=text_query_strategy,
                        allow_dis_max=allow_dis_max,
                    ),
                    _build_node_clause(
                        node.right,
                        text_query_strategy=text_query_strategy,
                        allow_dis_max=allow_dis_max,
                    ),
                ]
            }
        }
    if isinstance(node, OrNode):
        # 一个 OR 的孩子不得再自行切换评分组合，避免三项 OR 因二叉 AST 结合顺序
        # 不同而产生不同分数；当前节点只有在正向上下文且两个孩子都是叶子时才可用。
        clauses = [
            _build_node_clause(
                node.left,
                text_query_strategy=text_query_strategy,
                allow_dis_max=False,
            ),
            _build_node_clause(
                node.right,
                text_query_strategy=text_query_strategy,
                allow_dis_max=False,
            ),
        ]
        if allow_dis_max and _uses_cross_language_dis_max(
            node.left,
            node.right,
            query_field=None,
            text_query_strategy=text_query_strategy,
        ):
            return {"dis_max": {"queries": clauses, "tie_breaker": 0.0}}
        # minimum_should_match 控制逻辑命中；多个 should 命中时分数仍会累加。
        return {
            "bool": {
                "should": clauses,
                "minimum_should_match": 1,
            }
        }
    if isinstance(node, NotNode):
        # NOT 使用 must_not，保留“排除匹配项”的语义，不额外引入 match_all。
        return {
            "bool": {
                "must_not": [
                    _build_node_clause(
                        node.child,
                        text_query_strategy=text_query_strategy,
                        allow_dis_max=False,
                    )
                ]
            }
        }
    raise QuerySyntaxError("q 查询语法错误：无法解析查询式")


def _build_field_clause(
    node: FieldQuery,
    *,
    text_query_strategy: TextQueryStrategy,
    allow_dis_max: bool,
) -> dict:
    """按业务字段类别编译 ``field:value`` AST，并在此处实施字段白名单。

    文本、IPC、主分类号、标识符和法律状态的底层索引契约不同，不能用一个通用
    ``multi_match`` 兜底；不支持的字段必须显式失败而非静默扩大检索范围。
    """
    # 字段先过白名单，再根据字段类别分流；未知字段不能落入一个通用全文查询。
    field = node.field
    if field not in SUPPORTED_FIELDS:
        raise QuerySyntaxError(f"q 查询语法错误：不支持字段 {field}")

    value = _node_value(node.value)
    if not value and not isinstance(node.value, (AndNode, OrNode, NotNode)):
        raise QuerySyntaxError(f"q 查询语法错误：字段 {field} 的值不能为空")

    if field in TEXT_FIELD_MAPPING:
        # 文本字段可能含括号布尔表达式，因此交给字段值递归构建。
        return _build_field_value_clause(
            node.value,
            field,
            text_query_strategy=text_query_strategy,
            allow_dis_max=allow_dis_max,
        )
    if field == "ipc":
        return _build_ipc_value_clause(node.value, field)
    if field == MAIN_IPC_FIELD:
        return _build_main_ipc_value_clause(node.value, field)
    if field in IDENTIFIER_FIELD_MAPPING:
        # 标识符只用 keyword term，不做分词，避免申请号/公开号被拆成部分命中。
        return _build_identifier_value_clause(node.value, IDENTIFIER_FIELD_MAPPING[field], field)
    if field == LEGAL_STATUS_FIELD:
        # 法律状态由专用映射处理“有效专利/在审/失效”等业务集合。
        return build_legal_status_clause(value)

    raise QuerySyntaxError(f"q 查询语法错误：不支持字段 {field}")


def _build_field_value_clause(
    value_node: QueryNode,
    query_field: str,
    *,
    text_query_strategy: TextQueryStrategy,
    allow_dis_max: bool,
) -> dict:
    """在一个文本业务字段内递归编译叶子值和布尔结构。

    它保留用户在 ``field:(a OR b)`` 中写出的逻辑优先级，同时按字段选择全文、短语
    或枚举 keyword 语义；调用方已确认 ``query_field`` 位于文本字段白名单中。
    """
    # 文本字段的递归结构与顶层 AST 同构，但字段类型会决定 phrase/keyword/multi_match。
    if isinstance(value_node, (WordNode, PhraseNode)):
        if query_field in {"applicant", "currentAssignee", "agency", "agent"}:
            # 申请人/权利人/机构不是精确 keyword；短语查询比普通全文更接近用户
            # 对实体名称的预期，同时保留多字段兼容。
            return _phrase_multi_match(value_node.value, TEXT_FIELD_MAPPING[query_field])
        if query_field == "type":
            # type 是业务枚举，必须走精确 term，不能让分词器把“实用新型”拆开。
            return _build_keyword_clause(value_node.value, TEXT_FIELD_MAPPING[query_field])
        fields = (
            routed_text_fields(value_node.value, query_field=query_field)
            if query_field in ROUTED_TEXT_QUERY_FIELDS
            and text_query_strategy is not TextQueryStrategy.BASELINE
            else TEXT_FIELD_MAPPING[query_field]
        )
        if isinstance(value_node, PhraseNode):
            # 仅 tscd 的中文短语放宽一个词位，适配 IK 中文复合词重叠分词；其他
            # 字段保持连续 phrase，避免短语查询悄悄变宽。
            slop = (
                _TSCD_HAN_PHRASE_SLOP
                if query_field == "tscd" and _HAN_PATTERN.search(value_node.value)
                else None
            )
            return _phrase_multi_match(
                value_node.value,
                fields,
                slop=slop,
            )
        return _multi_match(value_node.value, fields)
    if isinstance(value_node, AndNode):
        return {
            "bool": {
                "must": [
                    _build_field_value_clause(
                        value_node.left,
                        query_field,
                        text_query_strategy=text_query_strategy,
                        allow_dis_max=allow_dis_max,
                    ),
                    _build_field_value_clause(
                        value_node.right,
                        query_field,
                        text_query_strategy=text_query_strategy,
                        allow_dis_max=allow_dis_max,
                    ),
                ]
            }
        }
    if isinstance(value_node, OrNode):
        clauses = [
            _build_field_value_clause(
                value_node.left,
                query_field,
                text_query_strategy=text_query_strategy,
                allow_dis_max=False,
            ),
            _build_field_value_clause(
                value_node.right,
                query_field,
                text_query_strategy=text_query_strategy,
                allow_dis_max=False,
            ),
        ]
        if allow_dis_max and _uses_cross_language_dis_max(
            value_node.left,
            value_node.right,
            query_field=query_field,
            text_query_strategy=text_query_strategy,
        ):
            return {"dis_max": {"queries": clauses, "tie_breaker": 0.0}}
        return {
            "bool": {
                "should": clauses,
                "minimum_should_match": 1,
            }
        }
    if isinstance(value_node, NotNode):
        return {
            "bool": {
                "must_not": [
                    _build_field_value_clause(
                        value_node.child,
                        query_field,
                        text_query_strategy=text_query_strategy,
                        allow_dis_max=False,
                    )
                ]
            }
        }
    raise QuerySyntaxError("q 查询语法错误：字段值不支持该表达式")


def _uses_cross_language_dis_max(
    left: QueryNode,
    right: QueryNode,
    *,
    query_field: str | None,
    text_query_strategy: TextQueryStrategy,
) -> bool:
    """仅将同一查询范围内的直接中英文正向叶子 OR 编译为 dis_max。"""
    if text_query_strategy is not TextQueryStrategy.ROUTED_DIS_MAX:
        return False
    left_context = _routed_leaf_context(left, query_field=query_field)
    right_context = _routed_leaf_context(right, query_field=query_field)
    if left_context is None or right_context is None:
        return False
    left_field, left_language = left_context
    right_field, right_language = right_context
    return left_field == right_field and left_language != right_language


def _routed_leaf_context(
    node: QueryNode,
    *,
    query_field: str | None,
) -> tuple[str | None, str] | None:
    if isinstance(node, (WordNode, PhraseNode)):
        if query_field is None and isinstance(node, WordNode) and looks_like_ipc(node.value):
            return None
        if query_field is None or query_field in ROUTED_TEXT_QUERY_FIELDS:
            return query_field, route_text_leaf(node.value)
        return None
    if (
        query_field is None
        and isinstance(node, FieldQuery)
        and node.field in ROUTED_TEXT_QUERY_FIELDS
        and isinstance(node.value, (WordNode, PhraseNode))
    ):
        return node.field, route_text_leaf(node.value.value)
    return None


def _phrase_multi_match(
    query: str,
    fields: list[str],
    *,
    slop: int | None = None,
) -> dict:
    # 统一生成 phrase multi_match，slop 只在调用方确认语义后传入。
    clause = {"query": query, "fields": fields, "type": "phrase"}
    if slop is not None:
        clause["slop"] = slop
    return {"multi_match": clause}


def _build_range_clause(node: RangeQuery) -> dict:
    """把受支持的日期或年份闭区间转换为索引日期字段上的 ``range`` 子句。

    日期格式和起止顺序在应用侧先验证，避免依赖不同 OpenSearch 版本对非法日期的
    宽松解析；其他字段即使语法上形成 RangeQuery，也不能越过业务字段边界。
    """
    # 日期先在应用侧严格解析和比较，再写入 ISO 日期范围，避免 OpenSearch 接受
    # 方向错误或非日期字符串后给出不一致结果。
    if node.field == "ad":
        start = _parse_date(node.start)
        end = _parse_date(node.end)
        if start > end:
            raise QuerySyntaxError("q 查询语法错误：范围起始值不能晚于结束值")
        return {"range": {"ApplicationDate": {"gte": node.start, "lte": node.end}}}

    if node.field == "documentYear":
        # 年范围展开到整年首尾日期，查询仍命中 PublicationDate 的日期字段。
        start_year = _parse_year(node.start)
        end_year = _parse_year(node.end)
        if start_year > end_year:
            raise QuerySyntaxError("q 查询语法错误：范围起始值不能晚于结束值")
        return {
            "range": {
                "PublicationDate": {
                    "gte": f"{start_year}-01-01",
                    "lte": f"{end_year}-12-31",
                }
            }
        }

    raise QuerySyntaxError(f"q 查询语法错误：不支持字段 {node.field}")


def _build_ipc_clause(
    value: str,
    query_field: str,
    *,
    allow_group_family: bool = False,
) -> dict:
    # ipc 的 serving 索引契约统一使用 IPCListBase；normalize_ipc 负责处理完整值
    # 和显式组族，builder 不重复推断层级。
    normalized = normalize_ipc(
        value,
        query_field=query_field,
        allow_group_family=allow_group_family,
    )
    return {"term": {"IPCListBase": normalized.canonical}}


def _build_ipc_value_clause(value_node: QueryNode, query_field: str) -> dict:
    """递归编译 ``ipc:`` 字段值，并允许显式 IPC 组族语义。

    叶子统一规范化到 ``IPCListBase``，布尔节点只组合已规范化的叶子；这与裸词 IPC
    的更保守识别规则不同，原因是用户已经明确声明了字段意图。
    """
    # ipc 字段允许 field:(a OR b) 等布尔表达式，叶子统一落到 term IPCListBase。
    if isinstance(value_node, (WordNode, PhraseNode)):
        return _build_ipc_clause(
            value_node.value,
            query_field,
            allow_group_family=True,
        )
    if isinstance(value_node, AndNode):
        return {
            "bool": {
                "must": [
                    _build_ipc_value_clause(value_node.left, query_field),
                    _build_ipc_value_clause(value_node.right, query_field),
                ]
            }
        }
    if isinstance(value_node, OrNode):
        return {
            "bool": {
                "should": [
                    _build_ipc_value_clause(value_node.left, query_field),
                    _build_ipc_value_clause(value_node.right, query_field),
                ],
                "minimum_should_match": 1,
            }
        }
    if isinstance(value_node, NotNode):
        return {"bool": {"must_not": [_build_ipc_value_clause(value_node.child, query_field)]}}
    raise QuerySyntaxError("q 查询语法错误：字段值不支持该表达式")


def _build_main_ipc_value_clause(value_node: QueryNode, query_field: str) -> dict:
    """递归编译 ``mainIpc:`` 字段值到其规范化后的主层级索引字段。

    同一 IPC 文本会依粒度落入大组或小组字段，函数必须复用 ``normalize_ipc`` 的结果，
    而不是由调用方手工截断字符串，否则会破坏分类层级的查询契约。
    """
    # mainIpc 与 ipc 的叶子值相同，但使用 normalize_ipc 返回的主层级字段；这就是
    # A01B1 → IPCLargeGroup=A01B1/00、A01B1/02 → IPCSmallGroup=A01B1/02 的来源。
    if isinstance(value_node, (WordNode, PhraseNode)):
        normalized = normalize_ipc(
            value_node.value,
            query_field=query_field,
            allow_group_family=True,
        )
        return {"term": {normalized.main_field: normalized.main_value}}
    if isinstance(value_node, AndNode):
        return {
            "bool": {
                "must": [
                    _build_main_ipc_value_clause(value_node.left, query_field),
                    _build_main_ipc_value_clause(value_node.right, query_field),
                ]
            }
        }
    if isinstance(value_node, OrNode):
        return {
            "bool": {
                "should": [
                    _build_main_ipc_value_clause(value_node.left, query_field),
                    _build_main_ipc_value_clause(value_node.right, query_field),
                ],
                "minimum_should_match": 1,
            }
        }
    if isinstance(value_node, NotNode):
        return {"bool": {"must_not": [_build_main_ipc_value_clause(value_node.child, query_field)]}}
    raise QuerySyntaxError("q 查询语法错误：字段值不支持该表达式")


def _build_identifier_value_clause(value_node: QueryNode, fields: list[str], query_field: str) -> dict:
    """递归编译标识符字段值，并兼容同一业务编号的多个索引别名。

    叶子使用精确 keyword 匹配，外层 AND/OR/NOT 保持 AST 原义；空值或不支持的节点
    直接报告查询语法错误，避免标识符检索退化为全文匹配。
    """
    # 一个业务标识符可能对应多个历史字段别名，叶子构建 OR，外层布尔结构仍照 AST 保留。
    if isinstance(value_node, (WordNode, PhraseNode)):
        value = value_node.value.strip()
        if not value:
            raise QuerySyntaxError(f"q 查询语法错误：字段 {query_field} 的值不能为空")
        return _build_identifier_clause(value, fields)
    if isinstance(value_node, AndNode):
        return {
            "bool": {
                "must": [
                    _build_identifier_value_clause(value_node.left, fields, query_field),
                    _build_identifier_value_clause(value_node.right, fields, query_field),
                ]
            }
        }
    if isinstance(value_node, OrNode):
        return {
            "bool": {
                "should": [
                    _build_identifier_value_clause(value_node.left, fields, query_field),
                    _build_identifier_value_clause(value_node.right, fields, query_field),
                ],
                "minimum_should_match": 1,
            }
        }
    if isinstance(value_node, NotNode):
        return {"bool": {"must_not": [_build_identifier_value_clause(value_node.child, fields, query_field)]}}
    raise QuerySyntaxError("q 查询语法错误：字段值不支持该表达式")


def _build_identifier_clause(value: str, fields: list[str]) -> dict:
    return _build_keyword_clause(value, fields)


def _build_keyword_clause(value: str, fields: list[str]) -> dict:
    # 多个 keyword 字段的兼容匹配使用 should+minimum_should_match=1，避免 term
    # 查询落到不存在的旧字段时整条请求失去结果。
    return {
        "bool": {
            "should": [{"term": {field: value}} for field in fields],
            "minimum_should_match": 1,
        }
    }


def _node_value(node: QueryNode) -> str:
    # 只提取叶子值用于空值/法律状态判断；布尔节点的具体结构由递归 builder 处理。
    if isinstance(node, (WordNode, PhraseNode)):
        return node.value.strip()
    return ""


def _multi_match(query: str, fields: list[str]) -> dict:
    # 默认全文查询保留 OpenSearch analyzer 语义；字段列表由 mapping 白名单提供。
    return {"multi_match": {"query": query, "fields": fields}}


def _parse_date(value: str) -> date:
    # 使用 Python ISO parser 做前置校验，统一把非法日期转换成公共查询语法错误。
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise QuerySyntaxError("q 查询语法错误：日期格式非法") from exc


def _parse_year(value: str) -> int:
    # documentYear 只接受四位数字，不把“2020-01-01”等混合值当年份。
    if len(value) != 4 or not value.isdigit():
        raise QuerySyntaxError("q 查询语法错误：日期格式非法")
    return int(value)


def build_search_sort(sort: str) -> list:
    """把公开排序别名转换为稳定的 OpenSearch sort 形状。

    Schema 已在入口约束正常值；末尾的相关性回退仅防御内部调用绕过 schema，不能被
    当作新增排序别名的兼容机制。
    """
    # relation/rank/relevance/score 共用 _score；日期排序明确指定方向，最后的
    # fallback 只是防御性兜底，正常请求已被 SearchRequest 正则限制。
    if sort in {"relation", "rank", "relevance", "score"}:
        return ["_score"]
    if sort == "applicationDate":
        return [{"ApplicationDate": {"order": "asc"}}]
    if sort == "!applicationDate":
        return [{"ApplicationDate": {"order": "desc"}}]
    if sort == "documentDate":
        return [{"PublicationDate": {"order": "asc"}}]
    if sort == "!documentDate":
        return [{"PublicationDate": {"order": "desc"}}]
    return ["_score"]
