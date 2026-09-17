"""按单个文本叶子的内容选择公开文本字段的语言物理字段。"""

from typing import Final, Literal


TextLanguage = Literal["cn", "en"]

# 公开查询字段到 serving CN/EN 字段的明确合同。不要从旧字段名或后缀推断，
# 因为 tscd 的一个语言叶子对应多个物理字段。
ROUTED_TEXT_FIELD_MAPPING: Final = {
    "title": {"cn": ["TitleCN"], "en": ["TitleEN"]},
    "ab": {"cn": ["AbstractCN"], "en": ["AbstractEN"]},
    "mainClaim": {"cn": ["MainClaimCN"], "en": ["MainClaimEN"]},
    "claims": {"cn": ["RequirementCN"], "en": ["RequirementEN"]},
    "description": {"cn": ["InstructionsCN"], "en": ["InstructionsEN"]},
    "independentClaims": {
        "cn": ["IndependentClaimsCN"],
        "en": ["IndependentClaimsEN"],
    },
    "dependentClaims": {
        "cn": ["DependentClaimsCN"],
        "en": ["DependentClaimsEN"],
    },
    "tscd": {
        "cn": [
            "TitleCN",
            "AbstractCN",
            "MainClaimCN",
            "RequirementCN",
            "InstructionsCN",
            "IndependentClaimsCN",
            "DependentClaimsCN",
        ],
        "en": [
            "TitleEN",
            "AbstractEN",
            "MainClaimEN",
            "RequirementEN",
            "InstructionsEN",
            "IndependentClaimsEN",
            "DependentClaimsEN",
        ],
    },
}
ROUTED_TEXT_QUERY_FIELDS = frozenset(ROUTED_TEXT_FIELD_MAPPING)

# Unicode Script=Han 的常用标点/数码、部首、笔画、统一表意文字和兼容表意文字范围。
# 补充平面使用宽区间覆盖未来扩展，避免只依赖字符名称而漏掉 々、〆、〇 等 Han 字符。
_HAN_CODEPOINT_RANGES = (
    (0x2E80, 0x2FFF),
    (0x3005, 0x3007),
    (0x3021, 0x3029),
    (0x3038, 0x303B),
    (0x31C0, 0x31EF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x16FE2, 0x16FE3),
    (0x16FF0, 0x16FF1),
    (0x20000, 0x2FA1F),
    (0x30000, 0x3347F),
)


def contains_han_character(value: str) -> bool:
    """判断文本是否包含 Unicode Han 字符。"""
    return any(
        start <= ord(character) <= end
        for character in value
        for start, end in _HAN_CODEPOINT_RANGES
    )


def route_text_leaf(value: str) -> TextLanguage:
    """中文字符优先；没有中文字符的纯数字归中文，其余归英文。"""
    if contains_han_character(value):
        return "cn"
    if value.strip().isdigit():
        return "cn"
    return "en"


def routed_text_fields(value: str, *, query_field: str | None = None) -> list[str]:
    """返回一个叶子应检索的语言字段；裸词只覆盖同语言标题和摘要。"""
    language = route_text_leaf(value)
    query_fields = (query_field,) if query_field is not None else ("title", "ab")
    return [
        field
        for field_name in query_fields
        for field in ROUTED_TEXT_FIELD_MAPPING[field_name][language]
    ]
