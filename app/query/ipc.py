"""显式 ipc/mainIpc 查询需要在“用户写法”和“索引层级字段”之间做一次语义选择。
这个模块只负责规范化和选择目标字段，实际 term/bool DSL 仍由 dsl_builder 生成。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.exceptions import QuerySyntaxError
from app.ipc import canonicalize_ipc, canonicalize_ipc_group_family, ipc_level


@dataclass(frozen=True)
class NormalizedIPC:
    """一个用户 IPC 输入的规范形式及其对应主 IPC 索引定位信息。

    ``canonical`` 用于一般 IPC 匹配；``main_field`` 与 ``main_value`` 消除了
    调用方对 IPC 层级字段的二次判断，确保 ``ipc`` 与 ``mainIpc`` 语义一致。
    """

    # canonical 是用户值的标准形式；main_field/main_value 是主 IPC 查询需要的
    # serving 字段。两者同时保留，避免调用方再次推断层级。
    canonical: str
    level: str
    main_field: str
    main_value: str


def normalize_ipc(
    value: str,
    *,
    query_field: str,
    allow_group_family: bool = False,
) -> NormalizedIPC:
    """规范化一个 IPC，并确定它应命中的主 IPC 层级字段。"""

    # 先拒绝空白，再尝试完整 IPC；只有显式字段查询才允许无斜杠组族。
    if not value.strip():
        raise QuerySyntaxError(f"q 查询语法错误：字段 {query_field} 的值不能为空")

    code = canonicalize_ipc(value)
    if not code and allow_group_family:
        # A01B1 这类组族不是完整 IPC，ipc 查询用 IPCListBase，mainIpc 查询
        # 用对应的大组 /00；裸词不会走到这里，保持全文检索兼容性。
        group_family = canonicalize_ipc_group_family(value)
        if group_family:
            return NormalizedIPC(
                canonical=group_family,
                level="group_family",
                main_field="IPCLargeGroup",
                main_value=f"{group_family}/00",
            )
    if not code:
        raise QuerySyntaxError(f"q 查询语法错误：字段 {query_field} 的 IPC 格式非法")

    level = ipc_level(code)
    if level in {"large_group", "small_group"}:
        # 带斜杠的值（包括 /00）都按真实小组精确查询；只有无斜杠组族才走上面的
        # group_family 分支，避免把 /00 错当成整个大组。
        return NormalizedIPC(
            canonical=code,
            level=level,
            main_field="IPCSmallGroup",
            main_value=code,
        )

    if level == "small_category":
        return NormalizedIPC(code, "small_category", "IPCSmallCategory", code)
    if level == "large_category":
        return NormalizedIPC(code, "large_category", "IPCLargeCategory", code)
    if level == "section":
        return NormalizedIPC(code, "section", "IPCSection", code)

    raise QuerySyntaxError(f"q 查询语法错误：字段 {query_field} 的 IPC 格式非法")


def looks_like_ipc(value: str) -> bool:
    """只识别完整且有效的裸 IPC；不把显式字段组族规则泄露给全文词。"""

    return bool(canonicalize_ipc(value))
