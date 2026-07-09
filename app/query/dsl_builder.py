from datetime import date
import re

from app.core.exceptions import QuerySyntaxError
from app.mappings.legal_status_mapping import build_legal_status_clause
from app.mappings.query_field_mapping import (
    IDENTIFIER_FIELD_MAPPING,
    LEGAL_STATUS_FIELD,
    MAIN_IPC_FIELD,
    SUPPORTED_FIELDS,
    TEXT_FIELD_MAPPING,
    get_normal_analyzer_fields,
    get_risky_analyzer_fields,
)
from app.query.ast import AndNode, FieldQuery, NotNode, OrNode, PhraseNode, QueryNode, RangeQuery, WordNode
from app.query.parser import parse_query
from app.schemas.search import SearchRequest


def build_search_dsl(request: SearchRequest) -> dict:
    must = [_build_node_clause(parse_query(request.q), request.index_analyzer_mode)]
    filters = []

    ds = request.ds.lower()
    if ds != "all":
        filters.append({"term": {"Country": ds.upper()}})

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


def _build_node_clause(node: QueryNode, index_analyzer_mode: str) -> dict:
    if isinstance(node, WordNode):
        if _is_bare_ipc(node.value):
            return _build_ipc_clause(node.value.upper(), index_analyzer_mode)
        return _multi_match(node.value, ["Title", "Abstract"])
    if isinstance(node, PhraseNode):
        return _multi_match(node.value, ["Title", "Abstract"])
    if isinstance(node, FieldQuery):
        return _build_field_clause(node, index_analyzer_mode)
    if isinstance(node, RangeQuery):
        return _build_range_clause(node)
    if isinstance(node, AndNode):
        return {
            "bool": {
                "must": [
                    _build_node_clause(node.left, index_analyzer_mode),
                    _build_node_clause(node.right, index_analyzer_mode),
                ]
            }
        }
    if isinstance(node, OrNode):
        return {
            "bool": {
                "should": [
                    _build_node_clause(node.left, index_analyzer_mode),
                    _build_node_clause(node.right, index_analyzer_mode),
                ],
                "minimum_should_match": 1,
            }
        }
    if isinstance(node, NotNode):
        return {"bool": {"must_not": [_build_node_clause(node.child, index_analyzer_mode)]}}
    raise QuerySyntaxError("q 查询语法错误：无法解析查询式")


def _build_field_clause(node: FieldQuery, index_analyzer_mode: str) -> dict:
    field = node.field
    if field not in SUPPORTED_FIELDS:
        raise QuerySyntaxError(f"q 查询语法错误：不支持字段 {field}")

    value = _node_value(node.value)
    if not value and not isinstance(node.value, (AndNode, OrNode, NotNode)):
        raise QuerySyntaxError(f"q 查询语法错误：字段 {field} 的值不能为空")

    if field in TEXT_FIELD_MAPPING:
        return _build_field_value_clause(node.value, field, index_analyzer_mode)
    if field == "ipc":
        return _build_ipc_clause(value, index_analyzer_mode)
    if field == MAIN_IPC_FIELD:
        return _build_main_ipc_value_clause(node.value, field)
    if field in IDENTIFIER_FIELD_MAPPING:
        return _build_identifier_value_clause(node.value, IDENTIFIER_FIELD_MAPPING[field], field)
    if field == LEGAL_STATUS_FIELD:
        return build_legal_status_clause(value)

    raise QuerySyntaxError(f"q 查询语法错误：不支持字段 {field}")


def _build_field_value_clause(value_node: QueryNode, query_field: str, index_analyzer_mode: str) -> dict:
    if isinstance(value_node, (WordNode, PhraseNode)):
        if index_analyzer_mode == "compat":
            return _compat_multi_match(value_node.value, query_field)
        return _multi_match(value_node.value, TEXT_FIELD_MAPPING[query_field])
    if isinstance(value_node, AndNode):
        return {
            "bool": {
                "must": [
                    _build_field_value_clause(value_node.left, query_field, index_analyzer_mode),
                    _build_field_value_clause(value_node.right, query_field, index_analyzer_mode),
                ]
            }
        }
    if isinstance(value_node, OrNode):
        return {
            "bool": {
                "should": [
                    _build_field_value_clause(value_node.left, query_field, index_analyzer_mode),
                    _build_field_value_clause(value_node.right, query_field, index_analyzer_mode),
                ],
                "minimum_should_match": 1,
            }
        }
    if isinstance(value_node, NotNode):
        return {"bool": {"must_not": [_build_field_value_clause(value_node.child, query_field, index_analyzer_mode)]}}
    raise QuerySyntaxError("q 查询语法错误：字段值不支持该表达式")


def _compat_multi_match(query: str, query_field: str) -> dict:
    normal_fields = get_normal_analyzer_fields(query_field)
    risky_fields = get_risky_analyzer_fields(query_field)

    clauses = []
    if normal_fields:
        clauses.append(_multi_match(query, normal_fields))
    if risky_fields:
        clauses.append(_phrase_multi_match(query, risky_fields))

    if not clauses:
        return _multi_match(query, TEXT_FIELD_MAPPING[query_field])
    if len(clauses) == 1:
        return clauses[0]
    return {"bool": {"should": clauses, "minimum_should_match": 1}}


def _phrase_multi_match(query: str, fields: list[str]) -> dict:
    return {"multi_match": {"query": query, "fields": fields, "type": "phrase"}}


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


def _build_ipc_clause(code: str, index_analyzer_mode: str) -> dict:
    if not code:
        raise QuerySyntaxError("q 查询语法错误：字段 ipc 的值不能为空")
    ipc_list_clause = (
        [{"term": {"IPCList.keyword": code}}, {"match_phrase": {"IPCList": code}}]
        if index_analyzer_mode == "compat"
        else [{"match": {"IPCList": code}}]
    )
    should = [
        {"term": {"IPC": code}},
        *ipc_list_clause,
        {"term": {"IPCSmallCategory": code}},
        {"term": {"IPCLargeGroup": code}},
        {"term": {"IPCSmallGroup": code}},
    ]
    return {"bool": {"should": should, "minimum_should_match": 1}}


def _build_main_ipc_value_clause(value_node: QueryNode, query_field: str) -> dict:
    if isinstance(value_node, (WordNode, PhraseNode)):
        code = value_node.value.strip().upper()
        if not code:
            raise QuerySyntaxError(f"q 查询语法错误：字段 {query_field} 的值不能为空")
        return {"term": {"IPC": code}}
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
    should = []
    for field in fields:
        should.extend(
            [
                {"term": {field: value}},
                {"term": {f"{field}.keyword": value}},
                {"match_phrase": {field: value}},
            ]
        )
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


def _is_bare_ipc(value: str) -> bool:
    return re.fullmatch(r"[A-H]\d{2}[A-Z](?:\d{1,4}/\d{1,6})?", value.strip().upper()) is not None
