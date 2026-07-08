# 阶段六开发派工单

## 角色

- 项目总控：维护阶段六边界、审查实现、负责 Git 提交节奏和交付文档。
- 开发人员：按实施计划实现完整布尔查询解析器和 API 错误处理。
- 测试人员：按验收单验证自动化测试、非法输入、真实 OpenSearch 冒烟和阶段五回归。

## 开发目标

阶段六需要把 `q` 从阶段五的正则识别升级为完整解析链路：

```text
SearchRequest.q -> Tokenizer -> Parser -> AST -> DSL Builder -> OpenSearch
```

完成后支持：

1. `title`、`ab`、`tscd`、`ipc`、`applicant`、`currentAssignee`、`legalStatus`、`type`。
2. `ad:[YYYY-MM-DD TO YYYY-MM-DD]`。
3. `documentYear:[YYYY TO YYYY]`。
4. `AND`、`OR`、`NOT`。
5. 括号分组。
6. 短语。
7. 非法查询语法返回 `40001`，且不访问 OpenSearch。

## 开发任务

1. 新增 `QuerySyntaxError`、token 定义和 tokenizer。
2. 新增 AST 节点和递归下降 parser。
3. 新增查询字段映射和法律状态基础映射。
4. 重构 `app/query/dsl_builder.py`，由 AST 构造 OpenSearch DSL。
5. 修改 `app/api/search.py`，统一捕获 `QuerySyntaxError` 并返回 `40001`。
6. 扩展 `scripts/smoke_search.py` 阶段六样例。
7. 更新 `docs/query_syntax.md` 和 `docs/field_mapping.md`。

## 关键约束

- `NOT` 是必须实现的正式能力，映射为 `bool.must_not`。
- `NOT title:(外观)` 和 `ipc:H02M AND NOT tscd:(均衡)` 必须是合法查询。
- `NOT` 和 `tscd:(均衡) NOT` 必须是非法查询。
- `type` 必须映射到实际存在的 OpenSearch 字段：`Type`、`PatentTypeCode`、`Kind`。
- 日期必须真实解析，`2020-13-01` 不能通过。
- `ad:[2021-01-01 TO 2020-12-31]` 和 `documentYear:[2024 TO 2020]` 必须返回 `40001`。
- `SearchService` 保持编排层，不直接处理 token 和 AST 细节。
- 不做阶段七详情、引证、SaaS 联调和生产网关改造。

## 参考计划

开发人员必须按以下实施计划逐任务执行，并在每个任务结束后提交：

`docs/superpowers/plans/2026-07-02-stage-6-boolean-query-parser.md`

## 提交要求

建议提交顺序：

1. `feat: add query tokenizer`
2. `feat: add boolean query parser`
3. `feat: add stage 6 query mappings`
4. `feat: build dsl from boolean query ast`
5. `feat: return 40001 for query syntax errors`
6. `docs: add stage 6 validation artifacts`

每次提交前至少运行对应任务测试；最终提交前运行完整测试：

```bash
.venv/bin/python -m pytest -q
```
