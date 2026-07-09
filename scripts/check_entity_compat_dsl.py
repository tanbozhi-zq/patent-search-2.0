from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.query.dsl_builder import build_search_dsl
from app.schemas.search import SearchRequest


def query_clause(q: str, mode: str = "compat") -> dict:
    return build_search_dsl(SearchRequest(q=q, index_analyzer_mode=mode))["query"]["bool"]["must"][0]


def search_dsl(q: str, ds: str = "cn", mode: str = "compat") -> dict:
    return build_search_dsl(SearchRequest(q=q, ds=ds, index_analyzer_mode=mode))


def assert_equal(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}\nexpected={expected!r}\nactual={actual!r}")


def assert_not_in(item: object, collection: list[object], label: str) -> None:
    if item in collection:
        raise AssertionError(f"{label}\nunexpected={item!r}")


def main() -> int:
    assert_equal(
        query_clause("applicant:(华为技术有限公司)"),
        {
            "multi_match": {
                "query": "华为技术有限公司",
                "fields": ["Applicant", "ApplicantNormalized", "FirstApplicant"],
                "type": "phrase",
            }
        },
        "applicant compat must use phrase matching",
    )
    assert_equal(
        query_clause("currentAssignee:(华为技术有限公司)"),
        {
            "multi_match": {
                "query": "华为技术有限公司",
                "fields": ["Assignee", "AssigneeNormalized"],
                "type": "phrase",
            }
        },
        "currentAssignee compat must use phrase matching",
    )
    assert_equal(
        query_clause("agency:(北京风雅颂专利代理有限公司)"),
        {
            "multi_match": {
                "query": "北京风雅颂专利代理有限公司",
                "fields": ["Agency", "Agency.keyword", "AgencyRaw"],
                "type": "phrase",
            }
        },
        "agency compat must prefer strict agency fields",
    )

    assert_equal(
        query_clause("applicant:(华为技术有限公司)", mode="normal"),
        {
            "multi_match": {
                "query": "华为技术有限公司",
                "fields": ["Applicant", "ApplicantNormalized", "FirstApplicant"],
            }
        },
        "normal mode applicant behavior must remain unchanged",
    )
    assert_equal(
        query_clause("currentAssignee:(华为技术有限公司)", mode="normal"),
        {
            "multi_match": {
                "query": "华为技术有限公司",
                "fields": ["Assignee", "AssigneeNormalized"],
            }
        },
        "normal mode currentAssignee behavior must remain unchanged",
    )
    assert_equal(
        query_clause("agency:(北京风雅颂专利代理有限公司)", mode="normal"),
        {
            "multi_match": {
                "query": "北京风雅颂专利代理有限公司",
                "fields": ["Agency", "AgencyRaw"],
            }
        },
        "normal mode agency behavior must remain unchanged",
    )

    ipc_should = query_clause("ipc:H02M")["bool"]["should"]
    assert_not_in({"match": {"IPCList": "H02M"}}, ipc_should, "ipc compat must not use ordinary IPCList match")
    assert {"term": {"IPCList.keyword": "H02M"}} in ipc_should
    assert {"match_phrase": {"IPCList": "H02M"}} in ipc_should
    bare_ipc_should = query_clause("H02M")["bool"]["should"]
    assert_not_in({"match": {"IPCList": "H02M"}}, bare_ipc_should, "bare ipc compat must not use ordinary IPCList match")
    assert {"term": {"IPCList.keyword": "H02M"}} in bare_ipc_should

    normal_ipc_should = query_clause("ipc:H02M", mode="normal")["bool"]["should"]
    assert {"match": {"IPCList": "H02M"}} in normal_ipc_should
    assert_not_in(
        {"term": {"IPCList.keyword": "H02M"}},
        normal_ipc_should,
        "normal mode ipc behavior must remain unchanged",
    )
    normal_bare_ipc_should = query_clause("H02M", mode="normal")["bool"]["should"]
    assert {"match": {"IPCList": "H02M"}} in normal_bare_ipc_should

    assert_equal(
        search_dsl("H02M", ds="us")["query"]["bool"]["filter"],
        [{"term": {"Country": "US"}}],
        "specific ds code must filter by upper-case Country",
    )
    assert_equal(
        search_dsl("H02M", ds="all")["query"]["bool"]["filter"],
        [],
        "ds=all must not add a Country filter",
    )
    assert_equal(
        search_dsl("H02M", ds="ALL")["query"]["bool"]["filter"],
        [],
        "ds=ALL must be accepted as all data scope",
    )

    assert_equal(
        query_clause("mainIpc:H02M"),
        {"term": {"IPC": "H02M"}},
        "mainIpc must search only the main IPC field",
    )
    assert_equal(
        query_clause("mainIpc:(A47J OR B01F)"),
        {
            "bool": {
                "should": [
                    {"term": {"IPC": "A47J"}},
                    {"term": {"IPC": "B01F"}},
                ],
                "minimum_should_match": 1,
            }
        },
        "mainIpc boolean composition must stay limited to IPC",
    )

    assert_equal(
        query_clause("applicationNumber:CN202411108082.1"),
        {
            "bool": {
                "should": [
                    {"term": {"ApplicationNumber": "CN202411108082.1"}},
                    {"term": {"ApplicationNumber.keyword": "CN202411108082.1"}},
                    {"match_phrase": {"ApplicationNumber": "CN202411108082.1"}},
                    {"term": {"ApplicationNumberAliases": "CN202411108082.1"}},
                    {"term": {"ApplicationNumberAliases.keyword": "CN202411108082.1"}},
                    {"match_phrase": {"ApplicationNumberAliases": "CN202411108082.1"}},
                ],
                "minimum_should_match": 1,
            }
        },
        "applicationNumber must use exact-style identifier fields",
    )
    assert_equal(
        query_clause("documentNumber:CN119188170B"),
        query_clause("publicationNumber:CN119188170B"),
        "publicationNumber must be an alias of documentNumber",
    )
    assert_equal(
        query_clause("patentId:cn-xxx"),
        {
            "bool": {
                "should": [
                    {"term": {"patent_id": "cn-xxx"}},
                    {"term": {"patent_id.keyword": "cn-xxx"}},
                    {"match_phrase": {"patent_id": "cn-xxx"}},
                ],
                "minimum_should_match": 1,
            }
        },
        "patentId must search patent_id with exact-style clauses",
    )

    print("entity compat DSL checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
