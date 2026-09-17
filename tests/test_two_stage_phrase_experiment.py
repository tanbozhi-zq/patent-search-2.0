from __future__ import annotations

import copy

import pytest

from benchmarks.two_stage_phrase_experiment import (
    build_candidate_body,
    build_verification_body,
    phrase_fields,
    validate_experiment_query,
    validate_mapping_analyzers,
)


def exact_body() -> dict:
    return {
        "from": 0,
        "size": 50,
        "_source": ["patent_id", "Title"],
        "track_total_hits": True,
        "query": {
            "bool": {
                "must": [
                    {"term": {"IPCListBase": "H02M"}},
                    {
                        "bool": {
                            "should": [
                                {
                                    "multi_match": {
                                        "query": "级联H桥变换",
                                        "fields": [
                                            "Title",
                                            "TitleCN",
                                            "TitleEN",
                                            "Abstract",
                                            "AbstractCN",
                                            "AbstractEN",
                                            "MainClaim",
                                            "MainClaimCN",
                                            "MainClaimEN",
                                            "Requirement",
                                            "RequirementCN",
                                            "RequirementEN",
                                            "Instructions",
                                        ],
                                        "type": "phrase",
                                        "slop": 1,
                                    }
                                },
                                {
                                    "multi_match": {
                                        "query": "多电平功率变换",
                                        "fields": [
                                            "Title",
                                            "TitleCN",
                                            "TitleEN",
                                            "Abstract",
                                            "AbstractCN",
                                            "AbstractEN",
                                            "MainClaim",
                                            "MainClaimCN",
                                            "MainClaimEN",
                                            "Requirement",
                                            "RequirementCN",
                                            "RequirementEN",
                                            "Instructions",
                                        ],
                                        "type": "phrase",
                                    }
                                },
                            ],
                            "minimum_should_match": 1,
                        }
                    },
                ]
            }
        },
        "sort": ["_score"],
    }


def test_candidate_rewrite_only_relaxes_positive_phrase_constraints() -> None:
    original = exact_body()
    candidate, rewritten = build_candidate_body(original, window=300)

    assert rewritten == 2
    assert original == exact_body()
    assert candidate["size"] == 300
    assert candidate["_source"] is False
    assert candidate["track_total_hits"] is False
    assert "request_cache" not in candidate
    clauses = candidate["query"]["bool"]["must"][1]["bool"]["should"]
    for clause in clauses:
        multi_match = clause["multi_match"]
        assert multi_match["type"] == "best_fields"
        assert multi_match["operator"] == "and"
        assert multi_match["fuzziness"] == 0
        assert "slop" not in multi_match


def test_candidate_rewrite_rejects_phrase_under_must_not() -> None:
    body = exact_body()
    phrase = copy.deepcopy(body["query"]["bool"]["must"].pop())
    body["query"]["bool"]["must_not"] = [phrase]

    with pytest.raises(ValueError, match="must_not"):
        build_candidate_body(body, window=100)


def test_candidate_rewrite_rejects_non_tscd_phrase_fields() -> None:
    body = exact_body()
    phrase = body["query"]["bool"]["must"][1]["bool"]["should"][0]["multi_match"]
    phrase["fields"] = ["Title", "Abstract"]

    with pytest.raises(ValueError, match="tscd phrase field set"):
        build_candidate_body(body, window=100)


def test_experiment_query_scope_rejects_not_and_non_tscd_phrases() -> None:
    assert validate_experiment_query('ipc:H02M AND tscd:("级联H桥" OR "多电平")') == 2

    with pytest.raises(ValueError, match="NOT"):
        validate_experiment_query('ipc:H02M AND NOT tscd:("级联H桥")')
    with pytest.raises(ValueError, match="tscd"):
        validate_experiment_query('ipc:H02M AND title:("级联H桥")')


def test_verification_reuses_exact_query_and_limits_ids() -> None:
    body = exact_body()
    verification = build_verification_body(
        body,
        candidate_ids=["p-1", "p-2"],
        page_size=50,
    )

    assert verification["query"]["bool"]["must"] == [body["query"]]
    assert verification["query"]["bool"]["filter"] == [
        {"ids": {"values": ["p-1", "p-2"]}}
    ]
    assert verification["track_total_hits"] is True
    assert verification["_source"] is False


def test_mapping_preflight_requires_same_search_and_quote_analyzer() -> None:
    body = exact_body()
    expected_fields = {
        "Title",
        "TitleCN",
        "TitleEN",
        "Abstract",
        "AbstractCN",
        "AbstractEN",
        "MainClaim",
        "MainClaimCN",
        "MainClaimEN",
        "Requirement",
        "RequirementCN",
        "RequirementEN",
        "Instructions",
    }
    assert phrase_fields(body) == expected_fields
    mapping = {
        "patent-v3": {
            "mappings": {
                "properties": {
                    "Title": {"type": "text", "analyzer": "ik_max_word"},
                    "Abstract": {
                        "type": "text",
                        "analyzer": "ik_max_word",
                        "search_analyzer": "ik_smart",
                        "search_quote_analyzer": "ik_smart",
                    },
                }
            }
        }
    }
    # Keep this unit focused on analyzer equality rather than all tscd fields.
    summary = validate_mapping_analyzers(mapping, fields={"Title", "Abstract"})
    assert summary["patent-v3.Title"]["search_analyzer"] == "ik_max_word"

    mapping["patent-v3"]["mappings"]["properties"]["Abstract"][
        "search_quote_analyzer"
    ] = "standard"
    with pytest.raises(ValueError, match="analyzer mismatch"):
        validate_mapping_analyzers(mapping, fields={"Title", "Abstract"})
