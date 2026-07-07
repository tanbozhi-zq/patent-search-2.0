# 阶段 9 测试派工单

## 角色

- 项目总控：维护阶段九边界、外采对比口径、问题分级、Git 状态和交付文档；确认是否放行阶段十 SaaS 联调。
- 测试人员：按照本派工单执行自研服务回归、外采契约对比、真实 OpenSearch smoke 和差异归类。
- 开发人员：仅在测试发现阻塞性缺陷并经总控确认后介入修复。

## 测试目标

阶段 9 目标是验证自研服务是否具备进入 SaaS 联调的条件。

测试范围：

1. search/detail/citations 成功响应字段。
2. 查询语法和分页参数。
3. 错误响应结构和错误码。
4. SaaS PatentHub 工具层字段契约。
5. 外采服务 live 对比，若凭据和网络条件允许。
6. 差异分级与问题清单。

## 参考文档

```text
docs/superpowers/specs/2026-07-06-stage-9-vendor-comparison-design.md
docs/saas_patent_contract_audit.md
docs/api_spec.md
docs/field_mapping.md
docs/query_syntax.md
docs/stage8_test_report.md
```

## 测试任务

1. 运行完整自动化测试。
2. 启动本地服务并执行 health/search/detail/citations smoke。
3. 执行阶段 9 查询样本集，记录 total、首条记录关键字段和是否报错。
4. 对 search record 检查 SaaS 工具层字段：`application_number`、`document_number`、`application_date`、`document_date`、`legal_status`、`main_ipc`、`summary`。
5. 对 detail 检查 SaaS 工具层字段：`application_number`、`document_number`、`legal_status`、`current_status`、`current_assignee`、`main_ipc`、`summary`、`claims`。
6. 对 citations 检查 SaaS 工具层字段：`cited_by`、`patent_references`、`non_patent_references`。
7. 若具备外采 PatentHub token，执行同样样本的外采 live 对比。
8. 若不具备外采 token，记录外采 live 对比阻塞原因。
9. 输出 `docs/stage9_vendor_comparison_report.md`。

## 阶段边界

阶段 9 不做：

1. 不修改 SaaS 副本源码。
2. 不进入 SaaS 正式联调。
3. 不修改 OpenSearch mapping。
4. 不重建索引。
5. 不要求召回和排序与外采完全一致。
6. 不新增高亮片段。
7. 不实现企业专利画像。

