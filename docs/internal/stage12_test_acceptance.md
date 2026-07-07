# Stage 12 总体验收单

## 1. 角色判断

本验收单由项目总控维护，供测试人员执行。测试人员在执行接口、Tool、联调验证之前，必须先对开发人员提交的代码进行 review，并输出 review 结论或问题清单。

本文件是 Stage 12 总体验收口径，不要求开发和测试一步到位。实际执行按子阶段推进：

1. `Stage 12.1`：核心 API 兼容补点。
2. `Stage 12.2`：DeerFlow Tool 封装。
3. `Stage 12.3`：DeerFlow / Flow 联调。
4. `Stage 12.4`：MCP Server。

测试人员不负责修改实现代码；发现问题后反馈给项目总控和开发人员。

## 2. 测试入口文档

测试前必须阅读：

1. `docs/delivery/stage12_deerflow_tool_mcp_work_plan.md`
2. `docs/internal/stage12_1_api_compat_dev_assignment.md`
3. `docs/internal/stage12_1_api_compat_test_acceptance.md`
4. `docs/internal/stage12_deerflow_tool_dev_assignment.md`
5. `docs/internal/stage12_3_deerflow_integration_acceptance.md`
6. `docs/internal/stage12_code_review_checklist.md`
7. `docs/internal/stage12_manual_test_cases.md`
8. `docs/delivery/api_spec.md`
9. `docs/delivery/query_syntax.md`
10. `docs/delivery/field_mapping.md`
11. `docs/internal/saas_patent_contract_audit.md`

## 3. 代码 Review 验收

测试人员必须先 review 开发提交，重点检查：

1. `deerflow_tool/` 是否只调用自研 HTTP API。
2. Tool 或 MCP 是否直接读取 OpenSearch 配置或创建 OpenSearch client。
3. `app/` 是否被反向依赖 `deerflow_tool/` 或 `mcp_server/`。
4. 是否修改了接口路径。
5. 是否移除了 HTTP API 的 `records`。
6. 是否把 Tool 层 `patents` 混入核心 HTTP API 顶层结构。
7. 是否修改 `patent_harness_base_副本/`。
8. 是否提交了 `.env`、token、OpenSearch 密码或真实密钥。
9. 是否有新增测试覆盖新增能力。

Review 不通过时，不进入功能测试。

## 4. 自动化回归

必须执行：

```bash
.venv/bin/python -m pytest -q
```

通过标准：

1. 全量测试通过。
2. 无未处理异常堆栈。
3. 无密钥、token、OpenSearch 密码输出。

## 5. Stage 12.1 核心 API 兼容验收

详细验收见：

```text
docs/internal/stage12_1_api_compat_test_acceptance.md
```

| Case | 验收点 | 期望 |
|---|---|---|
| search 原接口 | `POST /api/patent/search` | 路径不变，仍返回 `records` |
| sort 兼容 | PatentHub 风格 sort 值 | 合法兼容值成功，非法值返回稳定参数错误 |
| agency 检索 | `agency:(代理机构)` | DSL 命中 `Agency` |
| agent 检索 | `agent:(代理人)` | DSL 命中 `Agent` |
| 裸 IPC | `H02M` / `H02M7/483` | 自动按 IPC 检索 |
| 分页 metadata | `page`, `page_size`, `total` | 返回 `total_pages`、`next_page`、`took_ms` |
| legal history | `patent_get_legal_history` 或对应 API | 返回 `{patent_id, transaction_count, transactions}` |

## 6. Stage 12.2 DeerFlow Tool 验收

本阶段只验收本地 Tool 封装和 smoke，不验收真实 Flow / DeerFlow agent 加载。

| Tool | 验收点 | 期望 |
|---|---|---|
| `patent_search` | 返回结构 | 顶层包含 `patents`，不包含 `records` |
| `patent_search` | page_size 限制 | 不超过 `PATENT_SEARCH_PAGE_SIZE_LIMIT` |
| `patent_get_detail` | 详情字段 | 包含 `id`、`patent_id`、`claims` |
| `patent_get_detail` | `include_description=true` | 返回 `description` |
| `patent_get_citations` | 引证字段 | 包含 `cited_by`、`patent_references`、`non_patent_references` |
| `patent_get_legal_history` | 法律历史基础结构 | 包含 `transaction_count`、`transactions` |
| 错误转换 | 查询语法错误、未授权、404 | 返回 `{error, code}` |

## 7. Stage 12.3 DeerFlow / Flow 联调验收

详细验收见：

```text
docs/internal/stage12_3_deerflow_integration_acceptance.md
```

联调时至少跑通：

```text
search -> detail -> citations
```

验收标准：

1. Flow / DeerFlow 能加载 tool。
2. agent 能选择 `patent_search`。
3. search 返回的 `patents[0].id` 能继续查详情。
4. detail 返回字段足够支撑 agent 生成专利分析。
5. citations 无工具调用异常。
6. 错误查询式能被 agent 识别为工具错误，而不是系统崩溃。
7. 调用链路不依赖 PatentHub 临时 session id。

## 8. 外采服务对比

如 PatentHub 外采可用，测试人员需要抽样对比：

1. 首页结果是否存在可解释差异。
2. 排序差异是否可解释。
3. search 字段是否满足 agent 消费。
4. detail 的 `claims`、`description` 是否满足分析需要。
5. citations 字段命名是否与 DeerFlow 工具层消费一致。

对比不要求结果完全一致，但必须记录差异和可能原因。

## 9. 通过标准

Stage 12.2 DeerFlow Tool 本地封装通过需要同时满足：

1. 代码 review 通过。
2. 自动化回归通过。
3. 核心 API 兼容补点通过。
4. DeerFlow Tool 本地测试通过。
5. Tool 错误场景稳定。
6. 未修改 `patent_harness_base_副本/`。
7. 未提交密钥。
8. 输出测试报告或问题清单。

Stage 12.3 Flow / DeerFlow 联调通过标准以 `docs/internal/stage12_3_deerflow_integration_acceptance.md` 为准。

## 10. 测试报告要求

测试完成后，测试人员输出：

```text
docs/internal/stage12_test_report.md
```

报告必须包含：

1. 测试版本提交号。
2. 测试环境。
3. 自动化测试结果。
4. 代码 review 结论。
5. 核心 API 兼容验收结果。
6. Tool 验收结果。
7. DeerFlow / Flow 联调结果。
8. 外采对比结论。
9. 问题清单。
10. 是否建议进入 MCP Server 阶段。
