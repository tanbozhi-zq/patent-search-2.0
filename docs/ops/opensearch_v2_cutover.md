# OpenSearch v2 索引切换手册

## 1. 目的与边界

本手册用于将检索服务从 patent_index 安全切换至 patent_index_v2_20260716。它只定义索引、
服务配置和验收顺序；不包含任何真实凭据。

当前实现中，app/repositories/opensearch_repo.py 将 OPENSEARCH_INDEX 原样传给 OpenSearch。
因此服务不需要为 read alias 重构，但必须先修正与 v2 mapping 不兼容的查询 DSL。

## 2. 已知 v2 差异

| 项目 | 旧索引 | v2 |
|---|---|---|
| IPCList | text，带 .keyword 子字段 | keyword |
| IPCListBase | 无 | keyword |
| 动态字段 | dynamic: true | dynamic: strict |
| 中文分词字段 | 部分 standard | 多个中文字段调整为 ik_max_word |
| 批量加载设置 | serving 设置 | 允许临时 0 副本、关闭自动 refresh |

当前 compat DSL 仍含 IPCList.keyword。切换读路径前，应将 v2 的 IPC 精确分支改为 term/terms on
IPCList，并以真实样本比较命中差异。

## 3. 切换顺序

1. **数据对齐**：确认 v2 包含历史数据以及切换窗口内的新增、更新和删除。写到旧索引不会自动复制到 v2。
2. **写入验证**：用真实入库样本验证 dynamic: strict；出现未知字段时修正 ETL 或 mapping，不在生产中关闭 strict。
3. **代码兼容**：完成 IPC DSL 调整，并跑固定搜索、详情、引证、法律历史和 MCP 测试。
4. **Serving 设置**：将 v2 副本数恢复为 1、refresh_interval 恢复为 10s，显式 refresh，并确认分片健康。
5. **读 alias**：建立例如 patent_search_read 的 alias 指向 v2；先用 alias 做只读验证。
6. **服务切换**：把服务器 .env 的 OPENSEARCH_INDEX 改为读 alias，重启 FastAPI；MCP 经 FastAPI 查询，会同时生效。
7. **验收与观察**：验证固定查询及四条 HTTP/MCP 主链路，观察日志与写入状态。

## 4. Alias 原子切换

以下命令只在前置验收完成后执行，并应替换为实际索引名：

    POST /_aliases
    {
      "actions": [
        { "remove": { "index": "patent_index", "alias": "patent_search_read" } },
        { "add": { "index": "patent_index_v2_20260716", "alias": "patent_search_read" } }
      ]
    }

首次创建 alias 时省略不存在的 remove 动作。不要用 alias 代替增量同步，也不要假设一个 alias 会自动双写。

## 5. 回滚

若验收失败，将 patent_search_read 原子切回 patent_index，重启 FastAPI，并重新执行健康检查与固定检索。
旧索引必须保留到业务确认 v2 稳定为止。

## 6. 验收记录

至少记录：

- 两个索引的文档数、写入/更新/删除对账方式；
- v2 分片健康、刷新和副本设置；
- 固定查询的命中数与前几页结果；
- 详情、引证、法律历史与 MCP tools/list/tool call 结果；
- 切换时间、执行人和回滚入口。
