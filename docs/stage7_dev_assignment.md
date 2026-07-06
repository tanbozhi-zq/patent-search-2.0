# 阶段 7 开发派工单

## 角色

- 项目总控：维护阶段七边界、接口契约、Git 状态、代码 review 和验收放行。
- 开发人员：按实施计划实现详情与引证接口，不修改 SaaS 副本和 OpenSearch mapping。
- 测试人员：按验收单验证自动化测试、真实 OpenSearch smoke、字段兼容和错误响应。

## 开发目标

补齐专利详情与引证/相关文献接口，并同时兼容当前 HTTP API 字段风格与 SaaS PatentHub 工具层字段契约。

## 开发范围

1. `GET /api/patent/detail/{patent_id}`
2. `GET /api/patent/detail/{patent_id}?include_description=true`
3. `GET /api/patent/citations/{patent_id}`
4. OpenSearch 按稳定标识查询单篇专利。
5. 详情响应同时返回 camelCase 和 snake_case 关键字段。
6. 引证响应同时返回 SaaS 工具字段和原始兼容字段。
7. 专利不存在返回 `40401`。
8. OpenSearch 查询异常返回 `50001`。

## 关键契约

详情接口必须覆盖：

```text
applicationNumber
application_number
documentNumber
document_number
legalStatus
legal_status
currentAssignee
current_assignee
mainIpc
main_ipc
claims
description
```

引证接口必须覆盖：

```text
cited_by
patent_references
non_patent_references
referencesCited
referencesCitedRaw
referencesCitedText
relatedDocuments
```

## 参考文档

- `docs/superpowers/specs/2026-07-06-stage-7-detail-citations-design.md`
- `docs/superpowers/plans/2026-07-06-stage-7-detail-citations.md`
- `docs/saas_patent_contract_audit.md`
- `docs/api_spec.md`
- `docs/field_mapping.md`

## 阶段边界

阶段 7 不做：

1. 不修改 SaaS 副本源码。
2. 不做 SaaS 联调。
3. 不实现企业专利画像。
4. 不实现独立法律历史接口。
5. 不复制 PatentHub 60 分钟 session-bound ID 机制。
6. 不修改 OpenSearch mapping。
7. 不重建索引。

## 提交流程

开发人员必须按实施计划逐任务提交。每次提交前运行该任务相关测试；最终提交前运行完整测试：

```bash
.venv/bin/python -m pytest -q
```

最终阶段必须补充：

```text
docs/stage7_test_report.md
scripts/smoke_detail_citations.py
```
