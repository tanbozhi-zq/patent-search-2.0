"""IPC 输出映射同时服务 HTTP、MCP 和兼容引证对象。它优先使用 serving 索引已经
计算好的层级字段，再回退到原始 IPC；内部 IPCListBase/层级列不应穿透到公共响应。
"""

from __future__ import annotations

from collections.abc import Mapping

from app.ipc import canonicalize_ipc, ipc_level


_MAIN_IPC_HIERARCHY_FIELDS = (
    ("IPCSmallGroup", "small_group"),
    ("IPCLargeGroup", "large_group"),
    ("IPCSmallCategory", "small_category"),
    ("IPCLargeCategory", "large_category"),
    ("IPCSection", "section"),
)
_RAW_MAIN_IPC_FIELDS = ("IPC", "mainIpc", "main_ipc", "ipc")
_PUBLIC_MAIN_IPC_FIELDS = ("IPC", "mainIpc", "main_ipc", "ipc")
_PUBLIC_IPC_LIST_FIELDS = ("IPCList", "ipcMainList", "ipc_main_list")
_IPC_LIST_SOURCE_FIELDS = ("ipcMainList", "ipc_main_list", "IPCList")


def normalized_main_ipc(source: Mapping[str, object]) -> str:
    """优先读取规范化主 IPC 层级字段，再回退到原始 IPC。"""

    # 逐级字段必须与实际 code 层级一致，避免把 small group 值误当 category 输出。
    for field, expected_level in _MAIN_IPC_HIERARCHY_FIELDS:
        value = canonicalize_ipc(source.get(field))
        if value and ipc_level(value) == expected_level:
            return value

    for field in _RAW_MAIN_IPC_FIELDS:
        value = canonicalize_ipc(source.get(field))
        if value:
            return value
    return ""


def normalized_ipc_list(value: object) -> list[str]:
    """返回合法、去重且保持来源顺序的 IPC 列表。"""

    values = value if isinstance(value, (list, tuple)) else (value,)
    normalized = []
    seen = set()
    for item in values:
        ipc = canonicalize_ipc(item)
        if ipc and ipc not in seen:
            seen.add(ipc)
            normalized.append(ipc)
    return normalized


def normalized_record_ipc_list(source: Mapping[str, object]) -> list[str]:
    """从兼容记录中选择第一个可用 IPC 列表来源并清洗。"""

    for field in _IPC_LIST_SOURCE_FIELDS:
        if field not in source:
            continue
        values = normalized_ipc_list(source.get(field))
        if values:
            return values
    return []


def normalize_compat_ipc_record(
    record: dict,
    *,
    preserve_empty_main_ipc: bool = False,
    preserve_empty_ipc_list: bool = False,
) -> dict:
    """复制兼容记录，移除原始/内部 IPC 值并统一公共字段。"""

    # 先浅拷贝，不能原地修改 OpenSearch hit 或调用方持有的供应商对象。
    normalized = dict(record)
    main_ipc = normalized_main_ipc(record)

    for field in _PUBLIC_MAIN_IPC_FIELDS:
        if field not in normalized:
            continue
        if main_ipc:
            normalized[field] = main_ipc
        elif preserve_empty_main_ipc:
            normalized[field] = ""
        else:
            normalized.pop(field)

    ipc_list = normalized_record_ipc_list(record)
    for field in _PUBLIC_IPC_LIST_FIELDS:
        if field not in normalized:
            continue
        if ipc_list or preserve_empty_ipc_list:
            normalized[field] = ipc_list
        else:
            normalized.pop(field)

    normalized.pop("IPCListBase", None)
    for field, _ in _MAIN_IPC_HIERARCHY_FIELDS:
        normalized.pop(field, None)
    return normalized


def normalize_compat_ipc_records(value: object) -> object:
    """递归处理兼容列表中的字典 IPC 字段，非列表输入原样返回。"""

    if not isinstance(value, list):
        return value
    return [normalize_compat_ipc_record(item) if isinstance(item, dict) else item for item in value]
