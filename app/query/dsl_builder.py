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
)
from app.query.ast import AndNode, FieldQuery, NotNode, OrNode, PhraseNode, QueryNode, RangeQuery, WordNode
from app.query.parser import parse_query
from app.schemas.search import SearchRequest


def build_search_dsl(request: SearchRequest) -> dict:
    must = [_build_node_clause(parse_query(request.q))]
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


def build_target_rank_dsl(request, identifier_field: str, target: dict) -> dict:
    base = build_search_dsl(
        SearchRequest(q=request.q, ds=request.ds, sort=request.sort, page=1, page_size=1)
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
        sort_value = target.get("_score")
        better_query = None
        tied_query = None
    else:
        field, descending = _date_sort_details(request.sort)
        sort_value = target_source.get(field)
        if sort_value:
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
    if sort in {"applicationDate", "!applicationDate"}:
        return "ApplicationDate", sort.startswith("!")
    return "PublicationDate", sort.startswith("!")


def _build_node_clause(node: QueryNode) -> dict:
    if isinstance(node, WordNode):
        if _is_bare_ipc(node.value):
            return _build_ipc_clause(node.value.upper())
        return _multi_match(node.value, ["Title", "Abstract"])
    if isinstance(node, PhraseNode):
        return _phrase_multi_match(node.value, ["Title", "Abstract"])
    if isinstance(node, FieldQuery):
        return _build_field_clause(node)
    if isinstance(node, RangeQuery):
        return _build_range_clause(node)
    if isinstance(node, AndNode):
        return {
            "bool": {
                "must": [
                    _build_node_clause(node.left),
                    _build_node_clause(node.right),
                ]
            }
        }
    if isinstance(node, OrNode):
        return {
            "bool": {
                "should": [
                    _build_node_clause(node.left),
                    _build_node_clause(node.right),
                ],
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
        return _build_field_value_clause(node.value, field)
    if field == "ipc":
        return _build_ipc_clause(value)
    if field == MAIN_IPC_FIELD:
        return _build_main_ipc_value_clause(node.value, field)
    if field in IDENTIFIER_FIELD_MAPPING:
        return _build_identifier_value_clause(node.value, IDENTIFIER_FIELD_MAPPING[field], field)
    if field == LEGAL_STATUS_FIELD:
        return build_legal_status_clause(value)

    raise QuerySyntaxError(f"q 查询语法错误：不支持字段 {field}")


def _build_field_value_clause(value_node: QueryNode, query_field: str) -> dict:
    if isinstance(value_node, (WordNode, PhraseNode)):
        if query_field in {"applicant", "currentAssignee", "agency", "agent"}:
            return _phrase_multi_match(value_node.value, TEXT_FIELD_MAPPING[query_field])
        if query_field == "type":
            return _build_keyword_clause(value_node.value, TEXT_FIELD_MAPPING[query_field])
        if isinstance(value_node, PhraseNode):
            return _phrase_multi_match(value_node.value, TEXT_FIELD_MAPPING[query_field])
        return _multi_match(value_node.value, TEXT_FIELD_MAPPING[query_field])
    if isinstance(value_node, AndNode):
        return {
            "bool": {
                "must": [
                    _build_field_value_clause(value_node.left, query_field),
                    _build_field_value_clause(value_node.right, query_field),
                ]
            }
        }
    if isinstance(value_node, OrNode):
        return {
            "bool": {
                "should": [
                    _build_field_value_clause(value_node.left, query_field),
                    _build_field_value_clause(value_node.right, query_field),
                ],
                "minimum_should_match": 1,
            }
        }
    if isinstance(value_node, NotNode):
        return {"bool": {"must_not": [_build_field_value_clause(value_node.child, query_field)]}}
    raise QuerySyntaxError("q 查询语法错误：字段值不支持该表达式")


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


def _build_ipc_clause(code: str) -> dict:
    if not code:
        raise QuerySyntaxError("q 查询语法错误：字段 ipc 的值不能为空")
    should = [
        {"term": {"IPC": code}},
        {"term": {"IPCList": code}},
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
    return _build_keyword_clause(value, fields)


def _build_keyword_clause(value: str, fields: list[str]) -> dict:
    return {
        "bool": {
            "should": [{"term": {field: value}} for field in fields],
            "minimum_should_match": 1,
        }
    }


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
