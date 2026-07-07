# 专利检索 2.0 文档索引

## 当前阶段

当前项目处于 `Stage 12：DeerFlow Tool 联调与 MCP 交付准备`。

Stage 12 的主线是：在不频繁改动核心 HTTP API 的前提下，先把自研专利检索服务封装为 Flow / DeerFlow 可调用的 tool，再在 tool 联调稳定后封装 MCP Server。

## 优先阅读

1. `docs/delivery/stage12_deerflow_tool_mcp_work_plan.md`
2. `docs/internal/stage12_1_api_compat_dev_assignment.md`
3. `docs/internal/stage12_1_api_compat_test_acceptance.md`
4. `docs/internal/stage12_deerflow_tool_dev_assignment.md`
5. `docs/internal/stage12_3_deerflow_integration_acceptance.md`
6. `docs/internal/stage12_test_acceptance.md`
7. `docs/internal/stage12_code_review_checklist.md`
8. `docs/internal/stage12_manual_test_cases.md`
9. `docs/delivery/patent_search_service_api_delivery_overview.md`
10. `docs/delivery/api_spec.md`
11. `docs/delivery/query_syntax.md`
12. `docs/delivery/field_mapping.md`
13. `docs/ops/deployment_runbook.md`

## 正式交付文档

正式交付文档放在 `docs/delivery/`，用于接口确认、字段确认、Stage 12 工作计划和后续 DeerFlow / MCP 接入说明。

当前文件：

| 文件 | 用途 |
|---|---|
| `stage12_deerflow_tool_mcp_work_plan.md` | Stage 12 工作计划和交付边界 |
| `patent_search_service_api_delivery_overview.md` | 专利检索 HTTP API 接口交付说明 |
| `api_spec.md` | HTTP API 契约、参数、响应和错误码说明 |
| `query_syntax.md` | `q` 查询语法、字段语法和限制说明 |
| `field_mapping.md` | 外部字段、OpenSearch 字段和返回字段映射 |
| `patent_index_field_table.md` | `patent_index` 原始字段表 |
| `mcp_parameter_field_dependency.md` | MCP / Agent 参数和字段消费依赖参考 |

后续开发完成后补充：

| 文件 | 触发条件 |
|---|---|
| `deerflow_tool_integration_guide.md` | DeerFlow Tool 代码和本地 smoke 通过后 |
| `mcp_integration_guide.md` | MCP Server 代码和 MCP smoke 通过后 |
| `final_delivery_checklist.md` | Tool 与 MCP 联调完成并准备最终交付时 |

## 运维部署文档

运维部署文档放在 `docs/ops/`，用于服务器部署、环境变量、smoke 验证和回滚。

当前文件：

| 文件 | 用途 |
|---|---|
| `deployment_runbook.md` | systemd 部署、日志、部署后验证和回滚步骤 |
| `deploy_env_check.md` | 服务器、OpenSearch、端口和生产约束确认记录 |

## 内部过程文档

内部过程文档放在 `docs/internal/`，用于项目总控、开发人员、测试人员之间协作。

当前文件类型：

| 类型 | 示例 |
|---|---|
| Stage 10 / 10.5 过程文档 | `stage10_dev_assignment.md`、`stage10_5_test_report.md` |
| Stage 11 过程文档 | `stage11_delivery_plan.md`、`stage11_deploy_assignment.md` |
| Stage 12.1 API 兼容 | `stage12_1_api_compat_dev_assignment.md`、`stage12_1_api_compat_test_acceptance.md` |
| Stage 12.2 DeerFlow Tool | `stage12_deerflow_tool_dev_assignment.md` |
| Stage 12.3 DeerFlow / Flow 联调 | `stage12_3_deerflow_integration_acceptance.md` |
| Stage 12.4 MCP Server | `stage12_mcp_dev_assignment.md` |
| Stage 12 测试治理 | `stage12_test_acceptance.md`、`stage12_manual_test_cases.md`、`stage12_code_review_checklist.md` |
| SaaS / PatentHub 合约审计 | `saas_patent_contract_audit.md` |

## 历史归档

历史归档文档放在 `docs/archive/`，不删除，仅降低默认阅读优先级。

当前归档：

| 目录 | 内容 |
|---|---|
| `docs/archive/stage4-9/` | Stage 4 到 Stage 9 的开发、测试、验收、报告和 review 文档 |
| `docs/archive/initial/` | 早期需求说明、实施路径和初始需求边界文档 |

归档文档可能保留当时写作时的旧路径引用；需要查当前交付口径时，以 `docs/README.md` 和 `docs/delivery/` 中的文档为准。

## 对外交付文档

`对外交付文档/` 保留为给客户或外部接入方阅读的最终版本，不与内部派工、Git 管理、阶段过程文档混放。

## 只读参考仓库

`patent_harness_base_副本/` 是 DeerFlow / SaaS / PatentHub 合约参考仓库，只读参考，不允许在本项目治理或开发过程中修改。
