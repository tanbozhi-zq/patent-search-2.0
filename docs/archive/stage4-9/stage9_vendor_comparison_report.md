# 阶段 9 外采服务对比报告

## 测试角色

本次按 `agents.md` 中“测试人员”职责执行：依据项目文档验证接口、查询语法、字段映射、异常场景、外采服务对比和 SaaS 联调风险，并输出测试结论或问题清单。

## 执行范围

本报告为阶段 9 测试验收记录，依据：

1. `docs/stage9_test_assignment.md`
2. `docs/stage9_test_acceptance.md`
3. `docs/saas_patent_contract_audit.md`
4. `patent_harness_base_副本/backend/packages/harness/deerflow/community/patenthub/tools.py`
5. `docs/api_spec.md`

阶段边界遵守情况：

1. 未修改 `patent_harness_base_副本/`。
2. 未修改 OpenSearch mapping，未重建索引。
3. 未进入 SaaS 正式联调。
4. 未新增高亮片段。
5. 未要求召回和排序与外采完全一致。

## 自研服务回归

自动化测试：

```bash
.venv/bin/python -m pytest -q
```

结果：

```text
115 passed in 0.07s
```

健康检查 smoke：

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
.venv/bin/python scripts/smoke_health.py http://127.0.0.1:8000
```

结果：

```text
health ok
```

真实 OpenSearch smoke：

```bash
.venv/bin/python scripts/smoke_search.py http://127.0.0.1:8000 "$API_TOKEN"
.venv/bin/python scripts/smoke_detail_citations.py http://127.0.0.1:8000 cn-95474fd9e61ba99b "$API_TOKEN"
```

结果：

1. search smoke 通过，8 条查询均返回 HTTP 200。
2. `阀门` 查询返回 `total=510569`，首条 `patent_id=cn-95474fd9e61ba99b`。
3. 首条 search record 阶段 9 必填字段缺失项：无。
4. detail 默认、detail 带 `include_description=true`、citations 均返回 HTTP 200。

阶段 9 查询样本：

| q | HTTP | total | records | 首条 patent_id | 字段缺失 |
|---|---:|---:|---:|---|---|
| `阀门` | 200 | 510569 | 1 | `cn-95474fd9e61ba99b` | 无 |
| `ipc:H02M AND tscd:(均衡)` | 200 | 5122 | 1 | `cn-0a393dff872faa6c` | 无 |
| `applicant:(华为技术有限公司)` | 200 | 45186249 | 1 | `cn-2ed0a10ad7d92604` | 无 |
| `currentAssignee:(华为技术有限公司)` | 200 | 45187007 | 1 | `cn-efc61fee2183e693` | 无 |
| `legalStatus:(有效专利)` | 200 | 27284721 | 1 | `cn-5e136065a3a41850` | 无 |
| `documentYear:[2020 TO 2024]` | 200 | 28854473 | 1 | `cn-4996812d68075bba` | 无 |
| `type:(发明专利)` | 200 | 30769770 | 1 | `cn-e537de093e2caf3b` | 无 |
| `tscd:("电液比例阀")` | 200 | 7046855 | 1 | `cn-281b7bea00f783cc` | 无 |

阶段 9 查询样本的首条 search record 均满足字段验收；`summary` 均存在且与 `abstract` 同值。

## 契约对比结论

### Search

| 字段 | 自研服务 | PatentHub 工具层 | 结论 |
|---|---|---|---|
| 顶层列表字段 | `records` | `patents` | `design_boundary`，阶段 8 已明确 HTTP API 保留 `records` |
| `id` / `patent_id` | 支持 | 工具使用 `id` | 通过 |
| `title` | 支持 | 支持 | 通过 |
| `abstract` | 支持 | 无直接同名字段 | 通过 |
| `summary` | 支持，来源 `Abstract` | 支持 | 通过 |
| `applicationNumber` / `application_number` | 支持 | 工具输出 `application_number` | 通过 |
| `documentNumber` / `document_number` | 支持 | 工具输出 `document_number` | 通过 |
| `applicationDate` / `application_date` | 支持 | 工具输出 `application_date` | 通过 |
| `documentDate` / `document_date` | 支持 | 工具输出 `document_date` | 通过 |
| `legalStatus` / `legal_status` | 支持 | 工具输出 `legal_status` | 通过 |
| `mainIpc` / `main_ipc` | 支持 | 工具输出 `main_ipc` | 通过 |
| `page_size` | 最大 100 | 最大 50 | `design_boundary`，HTTP API 保持 100，工具层适配时可限制为 50 |
| `highlight=1` | 兼容接收，不返回片段 | 外采支持高亮参数 | `design_boundary`，阶段 9 不新增高亮片段 |

### Detail

详情接口已覆盖阶段 9 验收关键字段：

```text
id, patent_id, title, summary, application_number, document_number,
legal_status, current_status, current_assignee, main_ipc, claims
```

`description` 仅在 `include_description=true` 时返回，符合阶段 7/8 文档约定。

### Citations

引证接口已覆盖阶段 9 验收关键字段：

```text
patent_id, cited_by, patent_references, non_patent_references,
referencesCited, relatedDocuments
```

`cited_by` 与 `patent_references` 对结构化条目尽力归一化；无法结构化时保留原始字段并返回空摘要数组，归类为 `data_difference` 或 `design_boundary`，不阻塞 SaaS 联调准备。

## 错误响应对比

自研服务错误响应为扁平结构：

```json
{
  "success": false,
  "code": 40001,
  "message": "...",
  "data": null
}
```

阶段 9 验收错误码覆盖：

| 场景 | HTTP | code | 结论 |
|---|---:|---:|---|
| 查询语法错误 | 400 | `40001` | 通过 |
| 普通参数非法 | 400 | `40002` | 通过 |
| `page` / `page_size` 非法 | 400 | `40003` | 通过 |
| 未授权 | 401 | `40101` | 通过 |
| 专利不存在 | 404 | `40401` | 通过 |
| 默认路由不存在 | 404 | `40400` | 通过 |
| OpenSearch 查询异常 | 502 | `50001` | 通过 |

真实服务错误响应抽样结果：

| Case | HTTP | code | 外层 `detail` |
|---|---:|---:|---|
| 查询语法错误 | 400 | `40001` | 无 |
| 普通参数非法 | 400 | `40002` | 无 |
| `page_size=101` | 400 | `40003` | 无 |
| 未授权 | 401 | `40101` | 无 |
| 专利不存在 | 404 | `40401` | 无 |
| 默认路由不存在 | 404 | `40400` | 无 |

## Live 外采对比

本次未执行外采 PatentHub live 对比。

阻塞原因：

```text
外采 live 对比未执行：当前 `.env` 未提供 PATENTHUB_API_TOKEN。
```

补测条件：

1. 提供可用 `PATENTHUB_API_TOKEN`。
2. 确认开发/测试环境可访问 PatentHub 外采接口。
3. 使用 `docs/stage9_manual_test_cases.md` 的查询样本执行同口径 search/detail/citations 对比。

## 差异清单

| 差异 | 类型 | 影响 | 处理建议 |
|---|---|---|---|
| 自研 search 顶层为 `records`，PatentHub 工具层为 `patents` | `design_boundary` | 不影响 HTTP API；直接替换工具层时需适配 | 阶段 10 SaaS 工具适配层处理 |
| 自研 `page_size` 最大 100，PatentHub 工具层最大 50 | `design_boundary` | 不影响自研 API；工具层可限制为 50 | 阶段 10 适配层按工具契约限制 |
| `highlight=1` 不返回高亮片段 | `design_boundary` | 不影响核心检索、详情、引证链路 | 后续如业务需要再实现 |
| 外采 live 对比未执行 | `unknown` | 无法验证召回、排序、字段真实数据差异 | 取得 token 和网络后补测 |

## 阻塞项

当前代码契约层未发现 `blocking` 差异。

外采 live 对比缺少 token/网络条件，归类为测试条件阻塞，不作为代码层进入 SaaS 联调准备的阻塞项。

## 测试建议

测试侧建议进入阶段 10 SaaS 联调准备：是。

最终是否放行阶段 10，由项目总控确认。

---

## 项目总控验收意见

验收时间：2026-07-06

总控复核结论：

1. 阶段 9 执行范围符合 `docs/superpowers/specs/2026-07-06-stage-9-vendor-comparison-design.md`。
2. 测试人员已完成自研服务回归、真实 OpenSearch smoke、契约字段对比和差异分级。
3. search 记录补充 `summary` 后，已覆盖 PatentHub 工具层 search 关键字段。
4. detail/citations 已覆盖 SaaS 工具层关键字段。
5. 当前未发现代码契约层 `blocking` 差异。
6. 外采 live 对比因缺少 `PATENTHUB_API_TOKEN` 未执行，归类为测试条件阻塞，不阻止进入阶段 10 联调准备。
7. `patent_harness_base_副本/` 保持只读边界，未修改 SaaS 副本源码；未修改 OpenSearch mapping。

阶段 9 判定：**通过并收口**。

是否建议进入阶段 10 SaaS 联调：**是**。

阶段 10 前置要求：

1. 先补阶段 10 设计文档，再派发开发与测试任务。
2. 阶段 10 可以进入 SaaS 工具适配和联调准备，但正式修改 SaaS 主工程前需明确接入方式、回滚策略和灰度范围。
3. 若后续提供外采 `PATENTHUB_API_TOKEN`，阶段 9 live 对比应作为补测项追加记录。
