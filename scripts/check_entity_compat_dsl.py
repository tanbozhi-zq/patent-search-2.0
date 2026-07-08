from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.query.dsl_builder import build_search_dsl
from app.schemas.search import SearchRequest


def query_clause(q: str, mode: str = "compat") -> dict:
    return build_search_dsl(SearchRequest(q=q, index_analyzer_mode=mode))["query"]["bool"]["must"][0]


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

    print("entity compat DSL checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
