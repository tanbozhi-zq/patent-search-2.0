"""IPC 规范化被查询和响应映射共同使用。这里不关心“查询还是输出”，只负责把
NFKC、空白、大小写和版本尾标处理成一个可比较的 canonical code。
"""

from __future__ import annotations

import re
import unicodedata


_VERSION_TAIL = re.compile(
    r"""
    \(\s*
    (?:
        \d{4}\s*[./-]\s*\d{1,2}(?:\s*[./-]\s*\d{1,2})?
        |
        \d{8}
    )
    \s*\)\s*[A-Z]?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)
_FULL_GROUP = re.compile(
    r"^(?P<section>[A-HY])(?P<class_number>\d{2})"
    r"(?P<subclass>[A-Z])(?P<group>\d{1,4})/(?P<subgroup>\d{2,8})$"
)
_GROUP_FAMILY = re.compile(r"^[A-HY]\d{2}[A-Z]\d{1,4}$")
_SUBCLASS = re.compile(r"^(?P<section>[A-HY])(?P<class_number>\d{2})(?P<subclass>[A-Z])$")
_CLASS = re.compile(r"^(?P<section>[A-HY])(?P<class_number>\d{2})$")
_SECTION = re.compile(r"^(?P<section>[A-HY])$")


def canonicalize_ipc(value: object) -> str:
    """返回一个标准 IPC，非法值返回空字符串。"""

    code = _normalized_code(value)
    return code if ipc_level(code) else ""


def canonicalize_ipc_group_family(value: object) -> str:
    """返回仅供显式字段查询使用的无斜杠 IPC 组族。"""

    code = _normalized_code(value)
    return code if _GROUP_FAMILY.fullmatch(code) else ""


def _normalized_code(value: object) -> str:
    # NFKC 先统一全角/兼容字符，再去掉显示空格和末尾版本标记；不在这里放宽
    # 层级格式，最终合法性仍由各个正则和 ipc_level 共同决定。
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value).strip().upper()
    without_version_tail = _VERSION_TAIL.sub("", normalized)
    return re.sub(r"\s+", "", without_version_tail)


def ipc_level(code: str) -> str:
    """返回已规范化 IPC 的层级，未知格式返回空字符串。"""

    match = _FULL_GROUP.fullmatch(code)
    if match:
        return "large_group" if match.group("subgroup") == "00" else "small_group"
    if _SUBCLASS.fullmatch(code):
        return "small_category"
    if _CLASS.fullmatch(code):
        return "large_category"
    if _SECTION.fullmatch(code):
        return "section"
    return ""
