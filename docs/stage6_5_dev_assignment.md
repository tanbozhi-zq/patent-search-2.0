# 阶段 6.5 开发派工单

## 角色

- 项目总控：维护阶段 6.5 边界、验收标准、Git 状态和交付文档。
- 开发人员：按实施计划实现 `index_analyzer_mode` 和 analyzer 兼容 DSL。
- 测试人员：按验收单验证自动化测试、DSL 形态、真实 OpenSearch 对比和阶段六回归。

## 开发目标

在不重建 OpenSearch 索引、不修改 `patent_index` mapping 的前提下，为当前索引 analyzer 缺陷增加检索服务侧兼容模式。

## 开发内容

1. `SearchRequest` 新增 `index_analyzer_mode`。
2. `index_analyzer_mode` 只允许 `compat` 和 `normal`。
3. 默认值为 `compat`。
4. 增加字段 analyzer 风险分组。
5. `normal` 模式保留阶段六普通 `multi_match` 逻辑。
6. `compat` 模式对问题字段生成 phrase 查询。
7. 更新 API 文档、查询语法文档、测试验收文档。

## 问题字段

以下字段在 `compat` 模式下需要 phrase 查询：

```text
TitleCN
AbstractCN
MainClaim
Requirement
Instructions
Type
```

## 不变字段

以下字段保持阶段六逻辑：

```text
Title
Abstract
Applicant
Assignee
ApplicantNormalized
FirstApplicant
AssigneeNormalized
TitleEN
AbstractEN
PatentTypeCode
Kind
IPC 相关字段
ApplicationDate
PublicationDate
LatestLegalStatus
LegalStatus
```

## 参考计划

开发人员必须按以下实施计划逐任务执行，并在每个任务结束后提交：

`docs/superpowers/plans/2026-07-03-stage-6-5-index-analyzer-compat.md`

## 验证命令

每次提交前至少运行对应任务测试；最终提交前运行完整测试：

```bash
.venv/bin/python -m pytest -q
```

真实 OpenSearch 对比：

```bash
.venv/bin/python scripts/smoke_analyzer_compat.py http://127.0.0.1:8000 "$API_TOKEN"
```

## 阶段边界

阶段 6.5 不做：

1. 不重建 OpenSearch 索引。
2. 不修改 `patent_index` mapping。
3. 不改数据处理入库流程。
4. 不做阶段七详情接口。
5. 不做 SaaS 联调。
