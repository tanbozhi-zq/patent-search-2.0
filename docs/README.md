# 文档索引

本仓库只保留会影响代码、发布或运行决策的文档。README 是项目入口；其余文档按开发与运维职责划分。

| 文档 | 用途 |
|---|---|
| [PROJECT_OVERVIEW.md](../PROJECT_OVERVIEW.md) | 项目功能、技术组成与架构边界 |
| [README.md](../README.md) | 架构边界、能力与接入入口；部署要求与能力入口 |
| [ops/deployment_checklist.md](ops/deployment_checklist.md) | 部署前的版本、索引、配置和数据覆盖检查 |
| [api.md](api.md) | HTTP API、查询语法、返回结构、错误码和 MCP 对接契约 |
| [vector_search.md](vector_search.md) | 向量/混合模式、字段注册与数据覆盖、`top_k`、RRF pipeline、指标和维护事实 |
| [development.md](development.md) | 本地开发、检查、版本号与文档维护规则 |
| [ops/deployment_runbook.md](ops/deployment_runbook.md) | 服务发布、日志和代码回滚 |
| [ops/observability.md](ops/observability.md) | 指标、告警、基础面板和多实例聚合口径 |
| [ops/admin_dashboard.md](ops/admin_dashboard.md) | 管理员看板、配置草案、单实例运行时应用/回滚、审计和部署边界 |
| [ops/internal_prometheus.md](ops/internal_prometheus.md) | 内测 Prometheus 安装、保留、验收、备份和回滚 |
| [ops/opensearch_v2_cutover.md](ops/opensearch_v2_cutover.md) | 数据对齐、读 alias、切换与回滚（沿用历史文件名） |

`local/` 是 Git 忽略的临时材料目录，不承担接口、部署或工程决策说明；相关结论必须归并到上述正式文档，不能重建平行文档体系。

部署记录和测量结果由部署方单独维护。

## 验证工具

- [容量与过载](../benchmarks/capacity/README.md)：有负载的性能验证。
- [语义检索矩阵](../benchmarks/semantic_search/README.md)：隔离环境中的受控写入实验。
