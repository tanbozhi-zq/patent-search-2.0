# 文档索引

本仓库只保留会影响代码、发布或运行决策的文档。README 是项目入口；其余文档按开发与运维职责划分。

| 文档 | 用途 |
|---|---|
| [PROJECT_OVERVIEW.md](../PROJECT_OVERVIEW.md) | 项目功能、技术组成、架构边界与版本发布规则 |
| [README.md](../README.md) | 架构边界、当前运行状态、接口入口与 OpenSearch v2 改造范围 |
| [api.md](api.md) | HTTP API、查询语法、返回结构、错误码和 MCP 对接契约 |
| [development.md](development.md) | 本地开发、检查、分支与发布流程 |
| [ops/deployment_runbook.md](ops/deployment_runbook.md) | 服务发布、日志和代码回滚 |
| [ops/opensearch_v2_cutover.md](ops/opensearch_v2_cutover.md) | v2 数据对齐、读 alias、切换与回滚 |

`local/` 是 Git 忽略的临时材料目录，不承担接口、部署或工程决策说明；相关结论必须归并到上述正式文档，不能重建平行文档体系。
