# 阶段六代码审查报告

## 审查范围

- `app/query/tokenizer.py`
- `app/query/tokens.py`
- `app/query/ast.py`
- `app/query/parser.py`
- `app/query/dsl_builder.py`
- `app/mappings/query_field_mapping.py`
- `app/mappings/legal_status_mapping.py`
- `app/api/search.py`
- `app/core/exceptions.py`
- `scripts/smoke_search.py`
- `tests/test_query_tokenizer.py`
- `tests/test_query_parser.py`
- `tests/test_query_dsl_builder_stage6.py`
- `tests/test_legal_status_mapping.py`
- `tests/test_search_api.py`
- `tests/test_search_dsl_builder.py`
- `tests/test_search_service.py`

## 审查结论

阶段六布尔查询解析链路已经完成，当前阻塞问题已修复，自动化测试通过。

## 已发现并修复的问题

### 1. `type` 字段映射到不存在的 `PatentType` 字段

初次审查发现 `app/mappings/query_field_mapping.py` 中 `type` 被映射为：

```python
"type": ["PatentType"]
```

但 `patent_index` 中不存在 `PatentType` 字段。根据 `字段表.md` 和 `docs/field_mapping.md`，专利类型相关字段为：

- `Type`
- `PatentTypeCode`
- `Kind`

开发已在提交 `2331dc1 fix: map type query field to existing opensearch fields` 中修复为：

```python
"type": ["Type", "PatentTypeCode", "Kind"]
```

同步更新项：

- `app/mappings/query_field_mapping.py`
- `tests/test_legal_status_mapping.py`
- `tests/test_query_dsl_builder_stage6.py`

项目总控已同步修正文档规格：

- `docs/field_mapping.md`
- `docs/stage6_dev_assignment.md`
- `docs/superpowers/specs/2026-07-02-stage-6-boolean-query-parser-design.md`
- `docs/superpowers/plans/2026-07-02-stage-6-boolean-query-parser.md`

复查结论：已修复。

## 非阻塞事项

### 1. 参数校验错误仍返回 FastAPI 默认 422 结构

`page=0`、`page_size=101`、`ds=invalid` 等参数非法时，当前仍返回 FastAPI 默认 `detail` 数组结构，与 `docs/api_spec.md` 中统一的 `success/code/message/data` 错误结构不一致。

这不属于阶段六验收范围，建议阶段八“接口兼容与异常处理完善”统一处理。

### 2. `legalStatus` 使用 `match` 查询 keyword 字段

`LatestLegalStatus` 字段类型为 `keyword`，当前使用 `match` 查询可以工作，但语义上后续可考虑改为 `term`、`terms` 或更明确的状态归类映射。

阶段六先保持现状，待法律状态业务口径确认后再调整。

### 3. 保留字作为字段值时解析失败

例如 `title:AND` 或 `ipc:NOT` 会被 tokenizer 识别为保留字。该边界场景不影响阶段六主路径，后续如 SaaS 有真实需求再补充转义或字段值上下文解析。

## 验证结果

自动化测试：

```text
66 passed in 0.04s
```

测试报告：

- `docs/stage6_test_report.md`

真实 OpenSearch 冒烟结论：

- 阶段五回归查询通过。
- 阶段六合法查询通过。
- `type:(发明专利)` 修复后真实查询通过。
- 非法查询返回 HTTP 400，业务 code 为 `40001`。

## 是否建议进入下一阶段

建议阶段六验收通过后进入下一阶段。

阶段七建议聚焦：

1. 专利详情接口。
2. 搜索结果与详情字段一致性。
3. SaaS 调用方需要的详情字段补齐。
