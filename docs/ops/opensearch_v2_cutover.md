# OpenSearch 读别名与索引切换

本文件沿用历史文件名以保持链接稳定，步骤适用于后续读目标切换。执行前独立核对本环境读目标与配置，见[部署核对清单](deployment_checklist.md)。

## 查询与返回契约

`app/repositories/opensearch_repo.py` 将 `OPENSEARCH_INDEX` 原样传给 OpenSearch。服务默认使用稳定读别名 `patent_search_read`，不会自动选择最新索引、复制数据或建立回滚副本。

新目标必须满足当前代码实际使用的契约：

- `ipc` 以 `term` 查询 `IPCListBase`；显式无斜杠组族与完整带斜杠分类按不同粒度处理。`mainIpc` 使用对应标准化主 IPC 字段。
- `Type` 按 keyword 精确查询；机构等实体字段遵循字段映射。
- 标题、摘要、首权、完整权要、说明书、独权、从权及 `tscd` 按文本叶子路由到 CN/EN 字段。普通裸词查询同语言标题/摘要；完整 IPC 裸词保留分类查询语义。
- **详情**中的独权展示按 `IndependentClaimsCN`、`IndependentClaimsOriginal`、`IndependentClaimsEN` 回退；这不是搜索列表返回字段，也不表示查询会回退至 Original。
- 语义检索使用三个 `*Vector1024` 字段；维度、距离、模型和 RRF pipeline 见[向量说明](../vector_search.md)。

兼容字段可由数据工程单独添加；既有字段类型或 analyzer 改变需要重建物理索引。服务仓库不负责批量入库或回填。

## 切换前验收

1. 确认新目标包含要求的历史数据，以及切换窗口内的新增、更新和删除；读 alias 不会自动双写。
2. 核对 mapping、字段覆盖和 ETL 对 `dynamic: strict` 的兼容；用固定样本验证真实查询，不能只比较字段名。
3. 比较 Boolean、向量、混合检索及详情、引证、法律历史、MCP 的响应；明确有意的语义变化与不可接受的召回下降。
4. 记录副本、刷新、分片健康和资源预算。1 副本/10 秒刷新是建议服务配置，**不是现状描述**；当前例外见状态页。设置变更及显式 refresh 须独立授权、执行和回读。
5. 固定已存在且经验证的回滚索引或快照、服务提交及配套配置；回滚目标不得只写一个历史名称。
6. 先对新物理索引只读验收，再安排 alias 切换和观察窗口。

## Alias 原子切换

以下是写操作模板，只在发布窗口且前置验收完成后执行。先读取 `_alias/patent_search_read`，替换为真实目标：

```text
POST /_aliases
{
  "actions": [
    { "remove": { "index": "<CURRENT_VERIFIED_INDEX>", "alias": "patent_search_read" } },
    { "add": { "index": "<NEXT_VERIFIED_INDEX>", "alias": "patent_search_read" } }
  ]
}
```

首次创建时省略不存在的 remove。保持唯一读目标，避免多索引 alias 导致重复结果或语义差异。

若服务配置已经使用同一 alias，仅改变其物理目标不必为了“加载新索引名”重启服务；修改 `OPENSEARCH_INDEX` 配置时才需要按正常发布流程重启 FastAPI。MCP 经 FastAPI 查询，不持有独立 OpenSearch 读目标。两种情况都必须复验 HTTP/MCP 及依赖指标。

## 回滚与记录

将 alias 原子切回发布前确认的真实目标，复验探针和固定查询；若同时回滚服务代码，还须验证其字段、向量模型及 pipeline 与索引数据兼容。没有可用旧索引时只能按已验证快照恢复，不能假设 `patent_index` 仍然存在。

记录时间、执行人、前后 alias、文档/覆盖计数、增量对账、mapping 与设置、查询结果、服务提交和回滚入口。切换成功后更新本环境的私有运行记录，不把旧计数沿用为实时事实。
