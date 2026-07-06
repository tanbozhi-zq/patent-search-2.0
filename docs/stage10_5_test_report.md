# 阶段 10.5 测试报告

## 测试范围

- 新增细粒度文本字段查询：`mainClaim`、`claims`、`description`
- `compat` 与 `normal` 两种 `index_analyzer_mode`
- OR 短语查询、IPC 组合查询、NOT 组合查询
- 非法语法错误响应
- 既有 `title`、`ab`、`tscd` 回归
- 真实 OpenSearch smoke

## 测试环境

- 服务地址：`http://127.0.0.1:8001`
- 鉴权：`X-API-Key: test-token`
- 测试日期：2026-07-06

## 自动化测试

命令：

```bash
source .venv/bin/activate
python3 -m pytest -q
```

结果：

```text
132 passed in 0.09s
```

## DSL 验证

### normal 模式

| q | 实际 DSL 字段 | 结果 |
|---|---|---|
| `mainClaim:(均衡)` | `MainClaim` | 通过 |
| `claims:(均衡)` | `Requirement` | 通过 |
| `description:(均衡)` | `Instructions` | 通过 |

### compat 模式

| q | 实际 DSL | 结果 |
|---|---|---|
| `mainClaim:(均衡)` | `multi_match type=phrase fields=[MainClaim]` | 通过 |
| `claims:(均衡)` | `multi_match type=phrase fields=[Requirement]` | 通过 |
| `description:(均衡)` | `multi_match type=phrase fields=[Instructions]` | 通过 |

### 回归确认

`tscd:(均衡)` 在 `compat` 下仍覆盖：

```text
Title
Abstract
MainClaim
Requirement
Instructions
```

其中 `Title`、`Abstract` 使用普通 `multi_match`，`MainClaim`、`Requirement`、`Instructions` 使用 phrase 查询。

## 真实 OpenSearch Smoke

所有 smoke 请求均返回 HTTP 200，响应结构均包含：

```text
total
page
page_size
records
```

| 用例 | HTTP | total | records | 结果 |
|---|---:|---:|---:|---|
| `mainClaim:(均衡)` compat | 200 | 98928 | 1 | 通过 |
| `claims:(均衡)` compat | 200 | 45589 | 1 | 通过 |
| `description:(均衡)` compat | 200 | 248006 | 1 | 通过 |
| `mainClaim:(均衡)` normal | 200 | 12303056 | 1 | 通过 |
| `claims:(均衡)` normal | 200 | 4038174 | 1 | 通过 |
| `description:(均衡)` normal | 200 | 7683049 | 1 | 通过 |
| `mainClaim:("均衡" OR "平衡")` | 200 | 520190 | 1 | 通过 |
| `claims:("均衡" OR "平衡")` | 200 | 192272 | 1 | 通过 |
| `description:("均衡" OR "平衡")` | 200 | 1098158 | 1 | 通过 |
| `ipc:H02M AND claims:(均衡)` | 200 | 777 | 1 | 通过 |
| `mainClaim:(电路) AND NOT description:(外观)` | 200 | 2178275 | 1 | 通过 |
| `title:(阀门)` | 200 | 72219 | 1 | 通过 |
| `ab:(缓冲)` | 200 | 1034541 | 1 | 通过 |
| `tscd:(均衡)` | 200 | 413318 | 1 | 通过 |

## 非法语法验证

| q | HTTP | code | 是否含外层 detail | 结果 |
|---|---:|---:|---|---|
| `mainClaim:` | 400 | 40001 | 否 | 通过 |
| `claims:()` | 400 | 40001 | 否 | 通过 |
| `description:(均衡) AND AND ipc:H02M` | 400 | 40001 | 否 | 通过 |

示例错误结构：

```json
{
  "success": false,
  "code": 40001,
  "message": "q 查询语法错误：字段 claims 的值不能为空",
  "data": null
}
```

## 文档与边界

- `docs/query_syntax.md` 已包含 `mainClaim`、`claims`、`description` 查询语法。
- `docs/field_mapping.md` 已包含新增字段映射。
- `docs/api_spec.md` 已说明阶段 10.5 字段映射与 analyzer 行为。
- 未发现 OpenSearch mapping 修改。
- 未发现阶段 11 部署上线相关变更。

## 问题清单

未发现阻塞性问题。

## 总控验收复核

- 复核日期：2026-07-06
- 复核角色：项目总控
- 复核方式：以测试人员报告为主证据，补充检查代码差异、字段映射、查询语法文档、API 文档和目标自动化测试。
- 轻量复核命令：`.venv/bin/python -m pytest -q tests/test_legal_status_mapping.py tests/test_query_dsl_builder_stage6.py tests/test_query_dsl_builder_stage6_5.py tests/test_search_api.py`
- 轻量复核结果：57 passed in 0.04s
- 差异检查：`git diff --check` 通过。

总控结论：阶段 10.5 实现符合设计文档、开发派工单和测试验收单约定；新增 `mainClaim`、`claims`、`description` 查询字段边界清晰，未修改 OpenSearch mapping，未混入阶段 11 部署上线变更。阶段 10.5 可以关闭，后续进入阶段 11 前应先完成部署/接口交付文档对齐。

## 测试结论

阶段 10.5 自动化测试、DSL 验证、真实 OpenSearch smoke、非法语法和既有字段回归均通过，建议进入验收。
