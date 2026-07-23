from app.query.dsl_builder import build_search_dsl
from app.schemas.search import SearchRequest


def query_clause(q: str, mode: str = "compat") -> dict:
    return build_search_dsl(SearchRequest(q=q, index_analyzer_mode=mode))["query"]["bool"]["must"][0]


def test_default_mode_is_compat_for_tscd_risky_fields():
    clause = build_search_dsl(SearchRequest(q="tscd:(口腔数字印模仪图像采集方法)"))["query"]["bool"]["must"][0]

    should = clause["bool"]["should"]
    assert {
        "multi_match": {
            "query": "口腔数字印模仪图像采集方法",
            "fields": ["Title", "Abstract"],
        }
    } in should
    assert {
        "multi_match": {
            "query": "口腔数字印模仪图像采集方法",
            "fields": ["MainClaim", "Requirement", "Instructions"],
            "type": "phrase",
        }
    } in should
    assert clause["bool"]["minimum_should_match"] == 1


def test_normal_mode_preserves_stage_6_tscd_multi_match():
    clause = query_clause("tscd:(口腔数字印模仪图像采集方法)", mode="normal")

    assert clause == {
        "multi_match": {
            "query": "口腔数字印模仪图像采集方法",
            "fields": ["Title", "Abstract", "MainClaim", "Requirement", "Instructions"],
        }
    }


def test_stage_10_5_fine_grained_fields_use_phrase_in_compat_mode():
    assert query_clause("mainClaim:(均衡)") == {
        "multi_match": {"query": "均衡", "fields": ["MainClaim"], "type": "phrase"}
    }
    assert query_clause("claims:(均衡)") == {
        "multi_match": {"query": "均衡", "fields": ["Requirement"], "type": "phrase"}
    }
    assert query_clause("description:(均衡)") == {
        "multi_match": {"query": "均衡", "fields": ["Instructions"], "type": "phrase"}
    }


def test_stage_10_5_or_phrase_queries_use_phrase_in_compat_mode():
    clause = query_clause('description:("均衡" OR "平衡")')

    assert clause["bool"]["minimum_should_match"] == 1
    assert clause["bool"]["should"] == [
        {"multi_match": {"query": "均衡", "fields": ["Instructions"], "type": "phrase"}},
        {"multi_match": {"query": "平衡", "fields": ["Instructions"], "type": "phrase"}},
    ]


def test_title_and_abstract_compat_use_cn_phrase_fields():
    title_clause = query_clause("title:(口腔数字印模仪)")
    abstract_clause = query_clause("ab:(口腔数字印模仪)")

    assert {
        "multi_match": {
            "query": "口腔数字印模仪",
            "fields": ["Title", "TitleEN"],
        }
    } in title_clause["bool"]["should"]
    assert {
        "multi_match": {
            "query": "口腔数字印模仪",
            "fields": ["TitleCN"],
            "type": "phrase",
        }
    } in title_clause["bool"]["should"]
    assert {
        "multi_match": {
            "query": "口腔数字印模仪",
            "fields": ["AbstractCN"],
            "type": "phrase",
        }
    } in abstract_clause["bool"]["should"]


def test_type_compat_uses_type_phrase_and_code_matches():
    clause = query_clause("type:(发明专利)")

    assert {
        "multi_match": {
            "query": "发明专利",
            "fields": ["PatentTypeCode", "Kind"],
        }
    } in clause["bool"]["should"]
    assert {
        "multi_match": {
            "query": "发明专利",
            "fields": ["Type"],
            "type": "phrase",
        }
    } in clause["bool"]["should"]


def test_boolean_nodes_apply_compat_to_leaf_fields():
    clause = query_clause("ipc:H02M AND tscd:(口腔数字印模仪)")

    right = clause["bool"]["must"][1]
    assert right["bool"]["minimum_should_match"] == 1
    assert {
        "multi_match": {
            "query": "口腔数字印模仪",
            "fields": ["MainClaim", "Requirement", "Instructions"],
            "type": "phrase",
        }
    } in right["bool"]["should"]


def test_applicant_compat_uses_phrase_multi_match():
    clause = query_clause("applicant:(华为技术有限公司)")

    assert clause == {
        "multi_match": {
            "query": "华为技术有限公司",
            "fields": ["Applicant", "ApplicantNormalized", "FirstApplicant"],
            "type": "phrase",
        }
    }
