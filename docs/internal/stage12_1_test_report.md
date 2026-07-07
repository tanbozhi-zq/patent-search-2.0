# Stage 12.1 核心 API 兼容补点测试报告

## 1. 测试范围

按照 Stage 12.1 开发派工边界和当时执行口径，记录以下 5 个补点的收口结果：

1. sort 兼容。
2. `agency` / `agent` 字段检索。
3. 裸 IPC 自动识别。
4. search 返回 `total_pages` / `next_page` / `took_ms`。
5. legal history 基础能力。

不验收 `deerflow_tool/`、`mcp_server/`、DeerFlow / Flow 端到端联调。

## 2. 代码 Review 结论

Review 结论：通过。

### 2.1 边界检查

| 检查项 | 结果 |
|---|---|
| 未新增 `deerflow_tool/` | 通过 |
| 未新增 `mcp_server/` | 通过 |
| 未修改 `patent_harness_base_副本/` | 通过 |
| 未修改 `对外交付文档/` | 通过 |
| 未提交 `.env` | 通过，`.env` 未被 git 跟踪 |
| 未发现 app 内 OpenSearch mapping / reindex 写操作 | 通过 |
| HTTP API 继续保留 `records` | 通过 |
| 接口路径稳定 | 通过，新增 `/api/patent/legal-history/{patent_id}` |

### 2.2 补点实现检查

| 补点 | 相关文件 | Review 结果 |
|---|---|---|
| A1 sort 兼容 | `app/schemas/search.py`, `app/query/dsl_builder.py` | 通过 |
| A2 agency / agent | `app/mappings/query_field_mapping.py`, `app/query/dsl_builder.py` | 通过 |
| A3 裸 IPC | `app/query/dsl_builder.py` | 通过 |
| A4 search metadata | `app/mappings/result_mapper.py` | 通过 |
| A5 legal history | `app/api/legal_history.py`, `app/services/legal_history_service.py`, `app/mappings/legal_history_mapper.py`, `app/main.py` | 通过 |

### 2.3 Review 问题清单

阻塞问题：无。

重要问题：无。

建议问题：

1. `agency:(知识产权代理)` 在真实 OpenSearch smoke 中命中量较大，可能与 analyzer / 字段内容有关，非 Stage 12.1 阻塞问题。
2. 真实 OpenSearch 上构造稳定 `total=0` 搜索较困难，`total=0` metadata 边界以单元测试覆盖。

是否允许进入功能验证：允许。

## 3. 自动化测试

命令：

```bash
source .venv/bin/activate
python3 -m pytest -q
```

结果：

```text
157 passed in 0.10s
```

说明：`rank` 兼容记录在测试结束后已按开发修复结果修正；项目负责人确认不触发重复测试，本报告作为 Stage 12.1 最终收口记录。

## 4. 真实 API Smoke

测试服务：`http://127.0.0.1:8002`

健康检查：HTTP 200。

### A1：sort 兼容

| sort | HTTP | 返回结构 | 结果 |
|---|---:|---|---|
| `relation` | 200 | 含 `records` / metadata | 通过 |
| `relevance` | 200 | 含 `records` / metadata | 通过 |
| `score` | 200 | 含 `records` / metadata | 通过 |
| `!applicationDate` | 200 | 含 `records` / metadata | 通过 |
| `applicationDate` | 200 | 含 `records` / metadata | 通过 |
| `!documentDate` | 200 | 含 `records` / metadata | 通过 |
| `documentDate` | 200 | 含 `records` / metadata | 通过 |
| `rank` | 200 | 含 `records` / metadata | 通过 |

### A2：agency / agent

| q | HTTP | 结果 |
|---|---:|---|
| `agency:(知识产权代理)` | 200 | 通过 |
| `agent:(张)` | 200 | 通过 |
| `agency:` | 400 / code `40001` | 通过 |
| `agent:()` | 400 / code `40001` | 通过 |

### A3：裸 IPC

| q | HTTP | total | 结果 |
|---|---:|---:|---|
| `H02M` | 200 | 207492 | 通过 |
| `H02M7/483` | 200 | 89672 | 通过 |
| `F16K` | 200 | 356052 | 通过 |
| `阀门` | 200 | 510569 | 通过，未误判 IPC |
| `ipc:H02M` | 200 | 207492 | 通过，显式 IPC 行为不变 |

### A4：search metadata

请求：

```json
{"q":"H02M","page":1,"page_size":2,"sort":"relevance"}
```

结果：

```json
{
  "total": 207492,
  "page": 1,
  "page_size": 2,
  "total_pages": 103746,
  "next_page": 2,
  "took_ms": 16,
  "records": ["..."]
}
```

结论：顶层 `records` 保留，`total_pages` / `next_page` / `took_ms` 存在且计算正确。

### A5：legal history

样例 `patent_id`：`cn-e1c4b9ab5180b8a1`

成功响应：

```json
{
  "patent_id": "cn-e1c4b9ab5180b8a1",
  "transaction_count": 0,
  "transactions": []
}
```

未找到响应：

```json
{
  "success": false,
  "code": 40401,
  "message": "patent not found",
  "data": null
}
```

结论：结构稳定，未找到错误结构稳定。

## 5. 测试结论

Stage 12.1 代码 review 通过，自动化回归通过，5 个 API 兼容补点均通过真实接口 smoke。建议项目总控放行进入 Stage 12.2 DeerFlow Tool 封装。
