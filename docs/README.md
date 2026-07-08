# 专利检索 2.0 文档索引

## 当前阶段

当前项目处于 `Stage 12：远程 HTTP MCP 服务化交付`。

Stage 12 当前主线是：保持核心 FastAPI HTTP API 稳定，把已验证的 stdio MCP Server 收敛为公司真实 DeerFlow / 工作台可通过公网调用的远程 HTTP MCP Server。

本阶段不再维护 DeerFlow Tool 本地插件路径；相关历史可通过 Git 历史查询。

## 优先阅读

1. `docs/delivery/stage12_deerflow_tool_mcp_work_plan.md`
2. `docs/delivery/mcp_integration_guide.md`
3. `docs/delivery/patent_search_service_api_delivery_overview.md`
4. `docs/delivery/api_spec.md`
5. `docs/delivery/query_syntax.md`
6. `docs/delivery/field_mapping.md`
7. `docs/ops/deployment_runbook.md`

## 正式交付文档

正式交付文档放在 `docs/delivery/`，用于接口确认、字段确认、Stage 12 工作计划和后续远程 HTTP MCP 接入说明。

当前文件：

| 文件 | 用途 |
|---|---|
| `stage12_deerflow_tool_mcp_work_plan.md` | Stage 12 工作计划和交付边界 |
| `mcp_integration_guide.md` | 远程 HTTP MCP 接入、部署、smoke 和 tools 清单说明 |
| `patent_search_service_api_delivery_overview.md` | 专利检索 HTTP API 接口交付说明 |
| `api_spec.md` | HTTP API 契约、参数、响应和错误码说明 |
| `query_syntax.md` | `q` 查询语法、字段语法和限制说明 |
| `field_mapping.md` | 外部字段、OpenSearch 字段和返回字段映射 |
| `patent_index_field_table.md` | `patent_index` 原始字段表 |
| `mcp_parameter_field_dependency.md` | MCP / Agent 参数和字段消费依赖参考 |

后续开发完成后补充：

| 文件 | 触发条件 |
|---|---|
| `final_delivery_checklist.md` | HTTP MCP 联调完成并准备最终交付时 |

## 运维部署文档

运维部署文档放在 `docs/ops/`，用于服务器部署、环境变量、smoke 验证和回滚。

当前文件：

| 文件 | 用途 |
|---|---|
| `deployment_runbook.md` | systemd 部署、日志、部署后验证和回滚步骤 |
| `deploy_env_check.md` | 服务器、OpenSearch、端口和生产约束确认记录 |

## 内部过程文档

内部过程文档、阶段派工、阶段测试报告、历史归档和 agent 过程设计文件不进入远端可部署主线。开发人员可在本地 `.local-dev/` 中保存这些过程资产。

Stage 12 起当前远端 `main` 只保留可部署代码、正式交付文档和运维文档。质量门统一收敛为开发自查、项目总控 review、真实联调记录和交付文档复核。

## 历史归档

历史归档文档不进入远端可部署主线。如需保留历史过程材料，请放在本地 `.local-dev/` 并由 `.gitignore` 忽略。

## 外部交付口径

对外交付内容以 `docs/delivery/` 中的正式交付文档为准；重复整理目录和过程材料不进入远端可部署主线。

## 只读参考仓库

`patent_harness_base_副本/` 是 DeerFlow / SaaS / PatentHub 合约参考仓库，只读参考，不允许在本项目治理或开发过程中修改。
