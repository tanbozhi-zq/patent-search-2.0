"""验证公开搜索 DSL 的字段白名单、国家范围、IPC、短语和排序兼容规则。"""

import pytest

from app.core.exceptions import QuerySyntaxError
from app.mappings.query_field_mapping import VECTOR_FIELD_REGISTRY
from app.mappings.source_fields import SEARCH_SOURCE_FIELDS
from app.query.dsl_builder import TextQueryStrategy, build_search_dsl
from app.schemas.search import SearchRequest


def test_vector_field_registry_matches_current_serving_contract():
    assert {
        name: (
            definition.public_name,
            definition.opensearch_field,
            definition.dimensions,
            definition.space_type,
            definition.embedding_model,
        )
        for name, definition in VECTOR_FIELD_REGISTRY.items()
    } == {
        "abstract": (
            "abstract",
            "AbstractVector1024",
            1024,
            "cosinesimil",
            "doubao-embedding-vision-250615",
        ),
        "main_claim": (
            "main_claim",
            "MainClaimVector1024",
            1024,
            "cosinesimil",
            "doubao-embedding-vision-250615",
        ),
        "independent_claims": (
            "independent_claims",
            "IndependentClaimsVector1024",
            1024,
            "cosinesimil",
            "doubao-embedding-vision-250615",
        ),
    }


def test_plain_keyword_searches_title_and_abstract():
    dsl = build_search_dsl(SearchRequest(q="阀门"))

    assert dsl["query"]["bool"]["must"][0]["multi_match"]["query"] == "阀门"
    assert dsl["query"]["bool"]["must"][0]["multi_match"]["fields"] == [
        "TitleCN",
        "AbstractCN",
    ]


def test_search_dsl_uses_public_search_source_whitelist():
    dsl = build_search_dsl(SearchRequest(q="阀门"))

    assert dsl["_source"] == list(SEARCH_SOURCE_FIELDS)


@pytest.mark.parametrize("ds,expected", [("cn", "CN"), ("US", "US"), ("jp", "JP")])
def test_country_scope_filters_by_publication_country(ds, expected):
    dsl = build_search_dsl(SearchRequest(q="阀门", ds=ds))

    assert dsl["query"]["bool"]["filter"] == [
        {"term": {"PublicationCountry": expected}}
    ]


def test_all_scope_does_not_add_country_filter():
    dsl = build_search_dsl(SearchRequest(q="阀门", ds="all"))

    assert dsl["query"]["bool"]["filter"] == []


def test_bare_ipc_searches_normalized_hierarchy_field():
    dsl = build_search_dsl(SearchRequest(q="H02M"))

    assert dsl["query"]["bool"]["must"][0] == {"term": {"IPCListBase": "H02M"}}


def test_bare_ipc_with_group_searches_normalized_hierarchy_field():
    dsl = build_search_dsl(SearchRequest(q="H02M7/483"))

    assert dsl["query"]["bool"]["must"][0] == {"term": {"IPCListBase": "H02M7/483"}}


def test_plain_chinese_keyword_is_not_bare_ipc():
    dsl = build_search_dsl(SearchRequest(q="阀门"))

    assert dsl["query"]["bool"]["must"][0] == {
        "multi_match": {"query": "阀门", "fields": ["TitleCN", "AbstractCN"]}
    }


def test_quoted_plain_keyword_uses_phrase_matching():
    dsl = build_search_dsl(SearchRequest(q='"阀门"'))

    assert dsl["query"]["bool"]["must"][0] == {
        "multi_match": {
            "query": "阀门",
            "fields": ["TitleCN", "AbstractCN"],
            "type": "phrase",
        }
    }


def test_title_query_uses_title_fields():
    dsl = build_search_dsl(SearchRequest(q="title:(阀门)"))

    fields = dsl["query"]["bool"]["must"][0]["multi_match"]["fields"]
    assert fields == ["TitleCN"]


def test_english_title_and_abstract_queries_use_english_fields():
    title = build_search_dsl(SearchRequest(q="title:battery"))
    abstract = build_search_dsl(SearchRequest(q="ab:storage"))

    assert title["query"]["bool"]["must"][0]["multi_match"]["fields"] == ["TitleEN"]
    assert abstract["query"]["bool"]["must"][0]["multi_match"]["fields"] == ["AbstractEN"]


def test_bare_english_query_searches_english_title_and_abstract():
    clause = build_search_dsl(SearchRequest(q="battery"))["query"]["bool"]["must"][0]

    assert clause == {
        "multi_match": {"query": "battery", "fields": ["TitleEN", "AbstractEN"]}
    }


def test_numeric_and_mixed_language_values_follow_leaf_routing_contract():
    numeric = build_search_dsl(SearchRequest(q="title:2024"))["query"]["bool"]["must"][0]
    model = build_search_dsl(SearchRequest(q="title:5G"))["query"]["bool"]["must"][0]
    mixed = build_search_dsl(SearchRequest(q='title:"battery 电池"'))["query"]["bool"]["must"][0]

    assert numeric["multi_match"]["fields"] == ["TitleCN"]
    assert model["multi_match"]["fields"] == ["TitleEN"]
    assert mixed["multi_match"]["fields"] == ["TitleCN"]
    assert mixed["multi_match"]["type"] == "phrase"


@pytest.mark.parametrize(
    ("query", "expected_fields"),
    [
        ("mainClaim:电池", ["MainClaimCN"]),
        ("claims:battery", ["RequirementEN"]),
        ("description:电池", ["InstructionsCN"]),
        ("independentClaims:battery", ["IndependentClaimsEN"]),
        ("dependentClaims:电池", ["DependentClaimsCN"]),
    ],
)
def test_full_text_fields_route_each_leaf_to_its_language_fields(query, expected_fields):
    clause = build_search_dsl(SearchRequest(q=query))["query"]["bool"]["must"][0]

    assert clause["multi_match"]["fields"] == expected_fields


def test_cross_language_title_or_uses_dis_max_without_score_accumulation():
    clause = build_search_dsl(SearchRequest(q="title:(电池 OR battery)"))["query"]["bool"][
        "must"
    ][0]

    assert clause == {
        "dis_max": {
            "queries": [
                {"multi_match": {"query": "电池", "fields": ["TitleCN"]}},
                {"multi_match": {"query": "battery", "fields": ["TitleEN"]}},
            ],
            "tie_breaker": 0.0,
        }
    }


def test_cross_language_tscd_or_uses_dis_max_with_same_language_field_groups():
    clause = build_search_dsl(SearchRequest(q="tscd:(电池 OR battery)"))["query"]["bool"][
        "must"
    ][0]

    assert clause == {
        "dis_max": {
            "queries": [
                {
                    "multi_match": {
                        "query": "电池",
                        "fields": [
                            "TitleCN",
                            "AbstractCN",
                            "MainClaimCN",
                            "RequirementCN",
                            "InstructionsCN",
                            "IndependentClaimsCN",
                            "DependentClaimsCN",
                        ],
                    }
                },
                {
                    "multi_match": {
                        "query": "battery",
                        "fields": [
                            "TitleEN",
                            "AbstractEN",
                            "MainClaimEN",
                            "RequirementEN",
                            "InstructionsEN",
                            "IndependentClaimsEN",
                            "DependentClaimsEN",
                        ],
                    }
                },
            ],
            "tie_breaker": 0.0,
        }
    }


@pytest.mark.parametrize(
    ("query_field", "cn_field", "en_field"),
    [
        ("mainClaim", "MainClaimCN", "MainClaimEN"),
        ("claims", "RequirementCN", "RequirementEN"),
        ("description", "InstructionsCN", "InstructionsEN"),
        ("independentClaims", "IndependentClaimsCN", "IndependentClaimsEN"),
        ("dependentClaims", "DependentClaimsCN", "DependentClaimsEN"),
    ],
)
def test_full_text_direct_cross_language_or_uses_dis_max(query_field, cn_field, en_field):
    clause = build_search_dsl(SearchRequest(q=f"{query_field}:(电池 OR battery)"))["query"]["bool"][
        "must"
    ][0]

    assert clause == {
        "dis_max": {
            "queries": [
                {"multi_match": {"query": "电池", "fields": [cn_field]}},
                {"multi_match": {"query": "battery", "fields": [en_field]}},
            ],
            "tie_breaker": 0.0,
        }
    }


def test_equivalent_cross_language_or_syntax_uses_the_same_dis_max_shape():
    grouped = build_search_dsl(SearchRequest(q="title:(电池 OR battery)"))["query"]["bool"][
        "must"
    ][0]
    repeated = build_search_dsl(SearchRequest(q="title:电池 OR title:battery"))["query"]["bool"][
        "must"
    ][0]

    assert repeated == grouped


def test_same_language_or_keeps_bool_should_scoring():
    clause = build_search_dsl(SearchRequest(q="title:(电池 OR 储能)"))["query"]["bool"]["must"][0]

    assert clause["bool"]["minimum_should_match"] == 1
    assert [item["multi_match"]["fields"] for item in clause["bool"]["should"]] == [
        ["TitleCN"],
        ["TitleCN"],
    ]


@pytest.mark.parametrize(
    "query",
    [
        "title:(电池 OR battery OR 储能)",
        "title:(储能 OR 电池 OR battery)",
        "title:(电池 OR 储能 OR battery)",
    ],
)
def test_multi_term_or_never_embeds_dis_max_based_on_ast_order(query):
    clause = build_search_dsl(SearchRequest(q=query))["query"]["bool"]["must"][0]

    assert "dis_max" not in str(clause)
    assert clause["bool"]["minimum_should_match"] == 1


def test_cross_language_or_under_not_keeps_bool_scoring():
    clause = build_search_dsl(SearchRequest(q="NOT title:(电池 OR battery)"))["query"]["bool"][
        "must"
    ][0]

    negative = clause["bool"]["must_not"][0]
    assert "dis_max" not in str(negative)
    assert negative["bool"]["minimum_should_match"] == 1


def test_positive_direct_cross_language_or_inside_and_still_uses_dis_max():
    clause = build_search_dsl(
        SearchRequest(q="title:(电池 OR battery) AND ipc:H02J")
    )["query"]["bool"]["must"][0]

    assert "dis_max" in clause["bool"]["must"][0]
    assert clause["bool"]["must"][1] == {"term": {"IPCListBase": "H02J"}}


def test_different_text_scopes_keep_bool_should_scoring():
    clause = build_search_dsl(SearchRequest(q="title:电池 OR ab:battery"))["query"]["bool"]["must"][
        0
    ]

    assert "bool" in clause
    assert "dis_max" not in clause


def test_bare_ipc_or_text_keeps_bool_should_scoring():
    clause = build_search_dsl(SearchRequest(q="H02M OR 电池"))["query"]["bool"]["must"][0]

    assert clause == {
        "bool": {
            "should": [
                {"term": {"IPCListBase": "H02M"}},
                {
                    "multi_match": {
                        "query": "电池",
                        "fields": ["TitleCN", "AbstractCN"],
                    }
                },
            ],
            "minimum_should_match": 1,
        }
    }


def test_ab_experiment_strategies_reproduce_baseline_bool_and_dis_max_variants():
    request = SearchRequest(q="ab:(储能 OR storage)")

    baseline = build_search_dsl(
        request,
        text_query_strategy=TextQueryStrategy.BASELINE,
    )["query"]["bool"]["must"][0]
    routed_bool = build_search_dsl(
        request,
        text_query_strategy=TextQueryStrategy.ROUTED_BOOL,
    )["query"]["bool"]["must"][0]
    routed_dis_max = build_search_dsl(request)["query"]["bool"]["must"][0]

    assert [item["multi_match"]["fields"] for item in baseline["bool"]["should"]] == [
        ["Abstract", "AbstractCN", "AbstractEN"],
        ["Abstract", "AbstractCN", "AbstractEN"],
    ]
    assert [item["multi_match"]["fields"] for item in routed_bool["bool"]["should"]] == [
        ["AbstractCN"],
        ["AbstractEN"],
    ]
    assert [item["multi_match"]["fields"] for item in routed_dis_max["dis_max"]["queries"]] == [
        ["AbstractCN"],
        ["AbstractEN"],
    ]


def test_ipc_query_uses_only_normalized_hierarchy_field():
    dsl = build_search_dsl(SearchRequest(q="ipc:H02M"))

    assert dsl["query"]["bool"]["must"][0] == {"term": {"IPCListBase": "H02M"}}


def test_group_family_query_keeps_the_cn_scope():
    dsl = build_search_dsl(SearchRequest(q="ipc:F16K31", ds="cn"))

    assert dsl["query"]["bool"]["must"][0] == {"term": {"IPCListBase": "F16K31"}}
    assert dsl["query"]["bool"]["filter"] == [
        {"term": {"PublicationCountry": "CN"}}
    ]


def test_application_date_range_query():
    dsl = build_search_dsl(SearchRequest(q="ad:[2020-01-01 TO 2020-12-31]"))

    assert dsl["query"]["bool"]["must"][0]["range"]["ApplicationDate"] == {
        "gte": "2020-01-01",
        "lte": "2020-12-31",
    }


@pytest.mark.parametrize(
    ("value", "expected_fields"),
    [
        (
            "均衡",
            [
                "TitleCN",
                "AbstractCN",
                "MainClaimCN",
                "RequirementCN",
                "InstructionsCN",
                "IndependentClaimsCN",
                "DependentClaimsCN",
            ],
        ),
        (
            "balance",
            [
                "TitleEN",
                "AbstractEN",
                "MainClaimEN",
                "RequirementEN",
                "InstructionsEN",
                "IndependentClaimsEN",
                "DependentClaimsEN",
            ],
        ),
    ],
)
def test_tscd_query_searches_only_same_language_body_fields(value, expected_fields):
    multi_match = build_search_dsl(SearchRequest(q=f"tscd:({value})"))["query"]["bool"]["must"][0][
        "multi_match"
    ]

    assert multi_match["query"] == value
    assert multi_match["fields"] == expected_fields


def test_issue_26_quoted_tscd_han_phrase_uses_one_position_slop():
    dsl = build_search_dsl(SearchRequest(q='tscd:("级联H桥变换")'))

    multi_match = dsl["query"]["bool"]["must"][0]["multi_match"]
    assert multi_match["query"] == "级联H桥变换"
    assert multi_match["type"] == "phrase"
    assert multi_match["slop"] == 1


def test_issue_26_query_preserves_ipc_hierarchy_and_country_scope():
    dsl = build_search_dsl(
        SearchRequest(q='tscd:("级联H桥变换") AND ipc:H02J3')
    )

    combined = dsl["query"]["bool"]["must"][0]["bool"]["must"]
    assert combined[0]["multi_match"]["slop"] == 1
    assert combined[1] == {"term": {"IPCListBase": "H02J3"}}
    assert dsl["query"]["bool"]["filter"] == [
        {"term": {"PublicationCountry": "CN"}}
    ]


def test_quoted_tscd_english_phrase_remains_strict():
    dsl = build_search_dsl(SearchRequest(q='tscd:("power conversion")'))

    multi_match = dsl["query"]["bool"]["must"][0]["multi_match"]
    assert multi_match["type"] == "phrase"
    assert "slop" not in multi_match


def test_quoted_title_han_phrase_remains_strict():
    dsl = build_search_dsl(SearchRequest(q='title:("级联H桥变换")'))

    multi_match = dsl["query"]["bool"]["must"][0]["multi_match"]
    assert multi_match["type"] == "phrase"
    assert "slop" not in multi_match


def test_sort_compatibility_values_map_to_opensearch_sort():
    assert build_search_dsl(SearchRequest(q="阀门", sort="relation"))["sort"] == ["_score"]
    assert build_search_dsl(SearchRequest(q="阀门", sort="rank"))["sort"] == ["_score"]
    assert build_search_dsl(SearchRequest(q="阀门", sort="relevance"))["sort"] == ["_score"]
    assert build_search_dsl(SearchRequest(q="阀门", sort="score"))["sort"] == ["_score"]
    assert build_search_dsl(SearchRequest(q="阀门", sort="applicationDate"))["sort"] == [
        {"ApplicationDate": {"order": "asc"}}
    ]
    assert build_search_dsl(SearchRequest(q="阀门", sort="!applicationDate"))["sort"] == [
        {"ApplicationDate": {"order": "desc"}}
    ]
    assert build_search_dsl(SearchRequest(q="阀门", sort="documentDate"))["sort"] == [
        {"PublicationDate": {"order": "asc"}}
    ]
    assert build_search_dsl(SearchRequest(q="阀门", sort="!documentDate"))["sort"] == [
        {"PublicationDate": {"order": "desc"}}
    ]


def test_inventor_query_is_scoped_and_composes_with_patenthub_default_fields():
    request = SearchRequest(q='documentNumber:CN103532200B AND (title:解国林 OR ab:解国林 OR applicant:解国林 OR inventor:解国林)')
    dsl = build_search_dsl(request)
    branch = dsl['query']['bool']['must'][0]['bool']['must'][1]
    assert branch['bool']['should'][1] == {'multi_match': {'query': '解国林', 'fields': ['Inventor']}}
    negative = build_search_dsl(SearchRequest(q='NOT inventor:"解国林"'))
    assert negative['query']['bool']['must'][0] == {
        'bool': {'must_not': [{'multi_match': {'query': '解国林', 'fields': ['Inventor'], 'type': 'phrase'}}]}}
