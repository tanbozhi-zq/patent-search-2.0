from datetime import date

from app.core.exceptions import QuerySyntaxError
from app.mappings.legal_status_mapping import build_legal_status_clause
from app.mappings.query_field_mapping import LEGAL_STATUS_FIELD, SUPPORTED_FIELDS, TEXT_FIELD_MAPPING
from app.query.ast import AndNode, FieldQuery, NotNode, OrNode, PhraseNode, QueryNode, RangeQuery, WordNode
from app.query.parser import parse_query
from app.schemas.search import SearchRequest


def build_search_dsl(request: SearchRequest) -> dict:
    must = [_build_node_clause(parse_query(request.q))]
    filters = []

    if request.ds == "cn":
        filters.append({"term": {"Country": "CN"}})

    return {
        "from": request.offset,
        "size": request.page_size,
        "track_total_hits": True,
        "query": {
            "bool": {
                "must": must,
                "filter": filters,
            }
        },
        "sort": _build_sort(request.sort),
    }


def _build_node_clause(node: QueryNode) -> dict:
    if isinstance(node, WordNode):
        return _multi_match(node.value, ["Title", "Abstract"])
    if isinstance(node, PhraseNode):
        return _multi_match(node.value, ["Title", "Abstract"])
    if isinstance(node, FieldQuery):
        return _build_field_clause(node)
    if isinstance(node, RangeQuery):
        return _build_range_clause(node)
    if isinstance(node, AndNode):
        return {"bool": {"must": [_build_node_clause(node.left), _build_node_clause(node.right)]}}
    if isinstance(node, OrNode):
        return {
            "bool": {
                "should": [_build_node_clause(node.left), _build_node_clause(node.right)],
                "minimum_should_match": 1,
            }
        }
    if isinstance(node, NotNode):
        return {"bool": {"must_not": [_build_node_clause(node.child)]}}
    raise QuerySyntaxError("q 查询语法错误：无法解析查询式")


def _build_field_clause(node: FieldQuery) -> dict:
    field = node.field
    if field not in SUPPORTED_FIELDS:
        raise QuerySyntaxError(f"q 查询语法错误：不支持字段 {field}")

    value = _node_value(node.value)
    if not value and not isinstance(node.value, (AndNode, OrNode, NotNode)):
        raise QuerySyntaxError(f"q 查询语法错误：字段 {field} 的值不能为空")

    if field in TEXT_FIELD_MAPPING:
        return _build_field_value_clause(node.value, TEXT_FIELD_MAPPING[field])
    if field == "ipc":
        return _build_ipc_clause(value)
    if field == LEGAL_STATUS_FIELD:
        return build_legal_status_clause(value)

    raise QuerySyntaxError(f"q 查询语法错误：不支持字段 {field}")


def _build_field_value_clause(value_node: QueryNode, fields: list[str]) -> dict:
    if isinstance(value_node, (WordNode, PhraseNode)):
        return _multi_match(value_node.value, fields)
    if isinstance(value_node, AndNode):
        return {
            "bool": {
                "must": [
                    _build_field_value_clause(value_node.left, fields),
                    _build_field_value_clause(value_node.right, fields),
                ]
            }
        }
    if isinstance(value_node, OrNode):
        return {
            "bool": {
                "should": [
                    _build_field_value_clause(value_node.left, fields),
                    _build_field_value_clause(value_node.right, fields),
                ],
                "minimum_should_match": 1,
            }
        }
    if isinstance(value_node, NotNode):
        return {"bool": {"must_not": [_build_field_value_clause(value_node.child, fields)]}}
    raise QuerySyntaxError("q 查询语法错误：字段值不支持该表达式")


def _build_range_clause(node: RangeQuery) -> dict:
    if node.field == "ad":
        start = _parse_date(node.start)
        end = _parse_date(node.end)
        if start > end:
            raise QuerySyntaxError("q 查询语法错误：范围起始值不能晚于结束值")
        return {"range": {"ApplicationDate": {"gte": node.start, "lte": node.end}}}

    if node.field == "documentYear":
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


def _build_ipc_clause(code: str) -> dict:
    if not code:
        raise QuerySyntaxError("q 查询语法错误：字段 ipc 的值不能为空")
    should = [
        {"term": {"IPC": code}},
        {"match": {"IPCList": code}},
        {"term": {"IPCSmallCategory": code}},
        {"term": {"IPCLargeGroup": code}},
        {"term": {"IPCSmallGroup": code}},
    ]
    return {"bool": {"should": should, "minimum_should_match": 1}}


def _node_value(node: QueryNode) -> str:
    if isinstance(node, (WordNode, PhraseNode)):
        return node.value.strip()
    return ""


def _multi_match(query: str, fields: list[str]) -> dict:
    return {"multi_match": {"query": query, "fields": fields}}


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise QuerySyntaxError("q 查询语法错误：日期格式非法") from exc


def _parse_year(value: str) -> int:
    if len(value) != 4 or not value.isdigit():
        raise QuerySyntaxError("q 查询语法错误：日期格式非法")
    return int(value)


def _build_sort(sort: str) -> list:
    if sort == "!applicationDate":
        return [{"ApplicationDate": {"order": "desc"}}]
    return ["_score"]
