# 阶段 6.5 开发派工单

## 目标

在不重建 OpenSearch 索引、不修改 `patent_index` mapping 的前提下，为当前索引 analyzer 缺陷增加检索服务侧兼容模式。

## 开发内容

1. `SearchRequest` 新增 `index_analyzer_mode`，默认 `compat`。
2. 增加字段 analyzer 风险分组。
3. `compat` 模式下对问题字段生成 phrase 查询。
4. `normal` 模式保留阶段六普通 DSL。
5. 更新 API 文档和查询语法文档。

## 问题字段

- `TitleCN`
- `AbstractCN`
- `MainClaim`
- `Requirement`
- `Instructions`
- `Type`

## 验证要求

- `.venv/bin/python -m pytest -q`
- 真实 OpenSearch 对比 `normal` 与 `compat` 命中量。