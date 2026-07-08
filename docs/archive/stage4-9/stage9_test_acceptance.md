# 阶段 9 测试验收单

## 自动化测试

必须通过：

```bash
.venv/bin/python -m pytest -q
```

## 自研服务 Smoke

服务启动后必须通过：

```bash
.venv/bin/python scripts/smoke_health.py http://127.0.0.1:8000
.venv/bin/python scripts/smoke_search.py http://127.0.0.1:8000 "$API_TOKEN"
.venv/bin/python scripts/smoke_detail_citations.py http://127.0.0.1:8000 "$PATENT_ID" "$API_TOKEN"
```

## 查询样本验收

至少覆盖：

```text
阀门
ipc:H02M AND tscd:(均衡)
applicant:(华为技术有限公司)
currentAssignee:(华为技术有限公司)
legalStatus:(有效专利)
documentYear:[2020 TO 2024]
type:(发明专利)
tscd:("电液比例阀")
```

每条样本记录：

1. HTTP 状态。
2. total。
3. records 数量。
4. 首条记录 `patent_id`。
5. 字段完整性。
6. 与外采差异说明。

## 字段验收

search record 必须包含：

```text
id
patent_id
title
abstract
summary
applicationNumber
application_number
documentNumber
document_number
applicationDate
application_date
documentDate
document_date
legalStatus
legal_status
mainIpc
main_ipc
```

detail 必须包含：

```text
id
patent_id
title
summary
application_number
document_number
legal_status
current_status
current_assignee
main_ipc
claims
```

citations 必须包含：

```text
patent_id
cited_by
patent_references
non_patent_references
referencesCited
relatedDocuments
```

## 错误响应验收

必须确认：

| Case | Expected |
|---|---|
| 查询语法错误 | HTTP 400，code `40001` |
| 普通参数非法 | HTTP 400，code `40002` |
| `page` / `page_size` 非法 | HTTP 400，code `40003` |
| 未授权 | HTTP 401，code `40101` |
| 专利不存在 | HTTP 404，code `40401` |
| 默认路由不存在 | HTTP 404，code `40400` |

错误响应不得含外层 `detail`。

## 外采对比验收

若外采 token 可用：

1. 对相同样本执行外采 search。
2. 记录外采 total、首条 id、关键字段。
3. 用外采 search 返回 id 调用外采 detail/citations。
4. 与自研结果逐项对比字段覆盖、空值和差异类型。

若外采 token 不可用：

1. 执行离线契约对比。
2. 在报告中记录 live 对比阻塞原因。
3. 明确补测条件。

## 通过标准

1. 自动化测试通过。
2. 自研服务 smoke 通过。
3. 字段覆盖满足本验收单。
4. 所有差异已分级。
5. 无 `blocking` 差异，或阻塞项已有明确修复计划。
6. `docs/stage9_vendor_comparison_report.md` 已输出。

