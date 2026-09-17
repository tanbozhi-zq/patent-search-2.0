"""专利类型来源有三层优先级：明确业务 Type → PatentTypeCode → country+Kind 推断。
只有映射表中的已知类型才对外返回，未知值保持空字符串，避免把内部 Kind 当业务类型。
"""

from __future__ import annotations

from collections.abc import Mapping


_TYPE_ALIASES = {
    "发明专利": "发明专利",
    "发明申请": "发明专利",
    "实用新型": "实用新型",
    "实用新型专利": "实用新型",
    "外观设计": "外观设计",
    "外观设计专利": "外观设计",
}

_PATENT_TYPE_CODE_TYPES = {
    "1": "发明专利",
    "2": "实用新型",
    "3": "外观设计",
}

_KIND_TYPE_RULES = {
    "CN": {
        "A": "发明专利",
        "B": "发明专利",
        "C": "发明专利",
        "U": "实用新型",
        "Y": "实用新型",
        "D": "外观设计",
        "S": "外观设计",
    },
    "US": {"S": "外观设计", "S1": "外观设计"},
    "DE": {
        "A": "发明专利",
        "A1": "发明专利",
        "A8": "发明专利",
        "A9": "发明专利",
        "B": "发明专利",
        "B1": "发明专利",
        "B2": "发明专利",
        "B3": "发明专利",
        "B4": "发明专利",
        "B9": "发明专利",
        "C": "发明专利",
        "C1": "发明专利",
        "C2": "发明专利",
        "C3": "发明专利",
        "C5": "发明专利",
        "C9": "发明专利",
        "D1": "发明专利",
        "D2": "发明专利",
        "T1": "发明专利",
        "T2": "发明专利",
        "T3": "发明专利",
        "T5": "发明专利",
        "U": "实用新型",
        "U1": "实用新型",
    },
    "KR": {
        "A": "发明专利",
        "A9": "发明专利",
        "B1": "发明专利",
        "B9": "发明专利",
        "U": "实用新型",
        "Y1": "实用新型",
        "Y9": "实用新型",
    },
}

_CN_CORRECTION_KINDS = {
    "A8": "A",
    "A9": "A",
    "B8": "B",
    "B9": "B",
    "U8": "U",
    "U9": "U",
    "S8": "S",
    "S9": "S",
}


def normalized_patent_type(source: Mapping[str, object]) -> str:
    """返回已知业务类型；未知类型保持空字符串。"""

    # 明确的 Type 最可靠，但仍通过 alias 表归一化“发明申请”等历史写法。
    direct_type = _text(source.get("Type"))
    if direct_type in _TYPE_ALIASES:
        return _TYPE_ALIASES[direct_type]

    # 数字代码次于直接文本，随后才尝试按国家和公告 Kind 推断。
    patent_type_code = _text(source.get("PatentTypeCode"))
    if patent_type_code in _TYPE_ALIASES:
        return _TYPE_ALIASES[patent_type_code]
    if patent_type_code in _PATENT_TYPE_CODE_TYPES:
        return _PATENT_TYPE_CODE_TYPES[patent_type_code]

    country = _text(source.get("PublicationCountry")).upper()
    for value in (patent_type_code, _text(source.get("Kind"))):
        patent_type = _type_from_kind(country, value)
        if patent_type:
            return patent_type
    return ""


def _type_from_kind(country: str, value: str) -> str:
    # CN 的 A8/A9 等修正公告先折叠到基础 Kind；其他国家直接走有限规则表。
    if country == "CN":
        value = _CN_CORRECTION_KINDS.get(value, value)
    return _KIND_TYPE_RULES.get(country, {}).get(value, "")


def _text(value: object) -> str:
    # 类型元数据不做数组展开；这里的输入应是单值，异常数组按字符串处理并留空/不命中。
    return str(value).strip() if value is not None else ""
