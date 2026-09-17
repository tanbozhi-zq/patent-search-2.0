"""验证 MCP 详情兼容字段中的 IPC 列表与主 IPC 别名保持一致。"""

from app.mappings.ipc_mapper import normalize_compat_ipc_record


def test_mcp_detail_keeps_empty_ipc_list_aliases_consistent():
    mapped = normalize_compat_ipc_record(
        {
            "id": "cn-empty-ipc",
            "ipcMainList": [],
            "ipc_main_list": ["invalid"],
        },
        preserve_empty_ipc_list=True,
    )

    assert mapped["ipcMainList"] == []
    assert mapped["ipc_main_list"] == []
