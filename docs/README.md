# 文档索引

本仓库只保留会影响代码、发布或运行决策的文档。README 是项目入口；其余文档按开发与运维职责划分。

| 文档 | 用途 |
|---|---|
| README.md | 架构边界、当前运行状态、接口入口与 OpenSearch v2 改造范围 |
| docs/development.md | 本地开发、检查、分支与发布流程 |
| docs/ops/deployment_runbook.md | 服务发布、日志和代码回滚 |
| docs/ops/opensearch_v2_cutover.md | v2 数据对齐、读 alias、切换与回滚 |
| mcp_server/README.md | MCP tools、传输和鉴权 |

## 本地档案

不随源代码提交的材料统一放在仓库根目录的 `local/`（已被 Git 忽略）：

| 目录 | 内容 |
|---|---|
| `local/delivery/` | 对外交付材料 |
| `local/meeting-notes/` | 会议记录 |
| `local/test-evidence/` | 手工测试报告与验证附件 |
| `local/test-data/` | 原始测试数据（需要时创建） |

这些材料可用于追溯，但不作为工程事实来源。若其中的结论会影响代码、发布或运行决策，应提炼并更新到本目录的正式工程文档。
历史的 archive、internal、delivery、superpowers 和重要文档副本已清除；不要重新创建平行文档体系。
