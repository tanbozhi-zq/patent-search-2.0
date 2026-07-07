# Stage 12.1 核心 API 兼容补点测试验收单

## 1. 角色判断

本验收单供测试人员执行。测试人员在执行测试前，必须先对开发提交做代码 review；review 不通过，不进入功能测试。

## 2. 测试范围

Stage 12.1 只验收 5 个 API 兼容补点：

1. sort 兼容。
2. `agency` / `agent` 字段检索。
3. 裸 IPC 自动识别。
4. search 返回 `total_pages` / `next_page` / `took_ms`。
5. legal history 基础能力。

不验收：

1. `deerflow_tool/`。
2. `mcp_server/`。
3. DeerFlow / Flow 端到端联调。

## 3. Review 检查

测试前检查：

1. 未新增 `deerflow_tool/`。
2. 未新增 `mcp_server/`。
3. 未修改 `patent_harness_base_副本/`。
4. 未提交 `.env` 或真实密钥。
5. 未修改 OpenSearch mapping 或部署索引脚本。
6. HTTP API 仍保留 `records`。
7. 接口路径不变。

## 4. 自动化回归

必须执行：

```bash
.venv/bin/python -m pytest -q
```

通过标准：

1. 全量测试通过。
2. 新增测试覆盖 5 个补点。
3. 无密钥输出。

## 5. 补点验收

### A1：sort 兼容

至少验证：

```text
relation
rank
relevance
score
!applicationDate
applicationDate
!documentDate
documentDate
```

期望：

1. 合法值通过 Pydantic 校验。
2. DSL sort 映射正确。
3. 未知值返回稳定参数错误。

### A2：agency / agent

至少验证：

```text
agency:(知识产权代理)
agent:(张)
```

期望：

1. DSL 命中约定字段。
2. 非法空值仍返回 `40001`。
3. 不影响 `applicant`、`currentAssignee`。

### A3：裸 IPC

至少验证：

```text
H02M
H02M7/483
F16K
阀门
ipc:H02M
```

期望：

1. 裸 IPC 按 IPC 查询。
2. 显式 `ipc:` 行为不变。
3. 普通中文词不误判。

### A4：search metadata

至少验证：

| total | page | page_size | total_pages | next_page |
|---|---|---|---|---|
| 0 | 1 | 50 | 0 | null |
| 1 | 1 | 50 | 1 | null |
| 101 | 1 | 50 | 3 | 2 |
| 101 | 3 | 50 | 3 | null |

期望：

1. 顶层字段存在。
2. `records` 保留。
3. `took_ms` 来自 raw response 的 `took` 或为 `null`。

### A5：legal history 基础能力

至少验证：

```json
{
  "patent_id": "cn-xxx",
  "transaction_count": 0,
  "transactions": []
}
```

期望：

1. 返回结构稳定。
2. `transaction_count` 与 `transactions` 数量一致，或在无数据时为 `0`。
3. 未找到专利时错误结构稳定。

## 6. 手工 smoke

服务启动后执行：

```bash
export BASE_URL=http://127.0.0.1:8000
export API_TOKEN="$(grep -E '^API_TOKEN=' .env | cut -d= -f2-)"
```

检索 smoke：

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"H02M","page":1,"page_size":2,"sort":"relevance"}' | python3 -m json.tool
```

期望：

1. HTTP 200。
2. 返回 `records`。
3. 返回 `total_pages`、`next_page`、`took_ms`。

## 7. 通过标准

Stage 12.1 通过需要同时满足：

1. 代码 review 通过。
2. 自动化回归通过。
3. 5 个补点全部通过。
4. 文档同步更新。
5. 未引入 Tool / MCP 开发。
6. 未修改只读参考仓库。

通过后，项目总控再放行：

```text
Stage 12.2 DeerFlow Tool 封装
```
