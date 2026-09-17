"""验证标题、摘要文本叶子的语言识别和物理字段选择。"""

import pytest

from app.query.text_field_router import (
    contains_han_character,
    route_text_leaf,
    routed_text_fields,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("电池", "cn"),
        ("battery", "en"),
        ("2024", "cn"),
        (" 2024 ", "cn"),
        ("5G", "en"),
        ("5G电池", "cn"),
        ("battery 电池", "cn"),
        ("𠀀battery", "cn"),
        ("〇", "cn"),
        ("々", "cn"),
        ("〆", "cn"),
    ],
)
def test_route_text_leaf(value, expected):
    assert route_text_leaf(value) == expected


def test_contains_han_character_supports_basic_compatibility_and_extension_ranges():
    assert contains_han_character("电")
    assert contains_han_character("神")
    assert contains_han_character("𠀀")
    assert contains_han_character("〇")
    assert contains_han_character("々")
    assert contains_han_character("〆")
    assert not contains_han_character("battery-2024")


@pytest.mark.parametrize(
    ("value", "query_field", "expected"),
    [
        ("电池", "title", ["TitleCN"]),
        ("battery", "ab", ["AbstractEN"]),
        ("电池", "mainClaim", ["MainClaimCN"]),
        ("battery", "claims", ["RequirementEN"]),
        ("电池", "description", ["InstructionsCN"]),
        ("battery", "independentClaims", ["IndependentClaimsEN"]),
        ("电池", "dependentClaims", ["DependentClaimsCN"]),
        (
            "电池",
            "tscd",
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
            "battery",
            "tscd",
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
def test_routed_text_fields_selects_the_explicit_language_fields(value, query_field, expected):
    assert routed_text_fields(value, query_field=query_field) == expected


def test_routed_text_fields_keeps_bare_terms_to_title_and_abstract():
    assert routed_text_fields("电池") == ["TitleCN", "AbstractCN"]
    assert routed_text_fields("battery") == ["TitleEN", "AbstractEN"]
