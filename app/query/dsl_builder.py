import re

from app.schemas.search import SearchRequest


class UnsupportedQuerySyntax(ValueError):
    pass


def build_search_dsl(request: SearchRequest) -> dict:
    must = [_build_query_clause(request.q)]
    filters = []

    if request.ds == "cn":
        filters.append({"term": {"Country": "CN"}})

    sort = _build_sort(request.sort)

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
        "sort": sort,
    }


def _build_query_clause(q: str) -> dict:
    q = q.strip()

    title_match = re.fullmatch(r"title:\((.+)\)", q)
    if title_match:
        return _multi_match(_strip_quotes(title_match.group(1)), ["Title", "TitleCN", "TitleEN"])

    abstract_match = re.fullmatch(r"ab:\((.+)\)", q)
    if abstract_match:
        return _multi_match(_strip_quotes(abstract_match.group(1)), ["Abstract", "AbstractCN", "AbstractEN"])

    ipc_match = re.fullmatch(r"ipc:([A-Za-z0-9/]+)", q)
    if ipc_match:
        code = ipc_match.group(1)
        return {
            "bool": {
                "should": [
                    {"term": {"IPC": code}},
                    {"match": {"IPCList": code}},
                    {"term": {"IPCSmallCategory": code}},
                ],
                "minimum_should_match": 1,
            }
        }

    date_match = re.fullmatch(r"ad:\[(\d{4}-\d{2}-\d{2}) TO (\d{4}-\d{2}-\d{2})\]", q)
    if date_match:
        return {
            "range": {
                "ApplicationDate": {
                    "gte": date_match.group(1),
                    "lte": date_match.group(2),
                }
            }
        }

    if any(token in q for token in ["tscd:", "applicant:", "currentAssignee:", "legalStatus:", "type:", "documentYear:", " AND ", " OR ", " NOT "]):
        raise UnsupportedQuerySyntax("q 查询语法错误：暂不支持该语法")

    return _multi_match(_strip_quotes(q), ["Title", "Abstract"])


def _multi_match(query: str, fields: list) -> dict:
    return {
        "multi_match": {
            "query": query,
            "fields": fields,
        }
    }


def _strip_quotes(value: str) -> str:
    return value.strip().strip('"')


def _build_sort(sort: str) -> list:
    if sort == "!applicationDate":
        return [{"ApplicationDate": {"order": "desc"}}]
    return ["_score"]
