# 阶段 8 手动测试用例

## 测试目标

验证阶段 8 的接口兼容与异常处理完善是否符合联调前要求。

## 前置条件

1. 后端服务已启动：

```bash
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

2. `.env` 中已配置真实 OpenSearch 凭据和 `API_TOKEN`。
3. `ENABLE_AUTH=true` 时，以下命令需携带 `X-API-Key`。

建议设置：

```bash
export BASE_URL=http://127.0.0.1:8000
export API_TOKEN="$(grep -E '^API_TOKEN=' .env | cut -d= -f2-)"
```

## 用例 1：健康检查

```bash
curl -s "$BASE_URL/health" | python3 -m json.tool
```

期望：

- HTTP 200
- `success=true`
- `data.status=healthy`

## 用例 2：正常搜索

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"阀门","page":1,"page_size":1}' | python3 -m json.tool
```

期望：

- HTTP 200
- 包含 `total`、`page`、`page_size`、`records`
- `records[0].patent_id` 非空

## 用例 3：缺少 q

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{}' | python3 -m json.tool
```

期望：

- HTTP 400
- code `40002`
- 响应为 `{success, code, message, data}` 结构
- 不返回 FastAPI 默认 `detail` 数组，也不额外包裹外层 `detail`

## 用例 4：非法 page_size

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"阀门","page_size":101}' | python3 -m json.tool
```

期望：

- HTTP 400
- code 与 `docs/api_spec.md` 一致
- 错误体不暴露内部堆栈

## 用例 5：非法查询语法

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"ipc:H02M AND AND tscd:(均衡)"}' | python3 -m json.tool
```

期望：

- HTTP 400
- code `40001`
- 错误结构为 `{success, code, message, data}`
- 不需要从 `detail.code` 中读取业务码

## 用例 6：未授权

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -d '{"q":"阀门"}' | python3 -m json.tool
```

期望：

- HTTP 401
- code `40101`

## 用例 7：详情默认不含 description

先从用例 2 取一个真实 `PATENT_ID`，再执行：

```bash
curl -s "$BASE_URL/api/patent/detail/$PATENT_ID" \
  -H "X-API-Key: $API_TOKEN" | python3 -m json.tool
```

期望：

- HTTP 200
- 包含 camelCase 与 snake_case 关键字段
- 不包含 `description`

## 用例 8：详情包含 description

```bash
curl -s "$BASE_URL/api/patent/detail/$PATENT_ID?include_description=true" \
  -H "X-API-Key: $API_TOKEN" | python3 -m json.tool
```

期望：

- HTTP 200
- 包含 `description`

## 用例 9：引证接口

```bash
curl -s "$BASE_URL/api/patent/citations/$PATENT_ID" \
  -H "X-API-Key: $API_TOKEN" | python3 -m json.tool
```

期望：

- HTTP 200
- 包含 `patent_id`
- 包含 `cited_by`
- 包含 `patent_references`
- 包含 `non_patent_references`
- 包含 `referencesCited`
- 包含 `referencesCitedRaw`
- 包含 `referencesCitedText`
- 包含 `relatedDocuments`

## 用例 10：专利不存在

```bash
curl -s "$BASE_URL/api/patent/detail/not-found-id" \
  -H "X-API-Key: $API_TOKEN" | python3 -m json.tool
```

期望：

- HTTP 404
- code `40401`
- 错误结构为 `{success, code, message, data}`
- 不需要从 `detail.code` 中读取业务码

## 用例 11：官方 smoke 回归

```bash
.venv/bin/python scripts/smoke_health.py "$BASE_URL"
.venv/bin/python scripts/smoke_search.py "$BASE_URL" "$API_TOKEN"
.venv/bin/python scripts/smoke_detail_citations.py "$BASE_URL" "$PATENT_ID" "$API_TOKEN"
```

期望：

- 三组 smoke 均退出码为 `0`
- search/detail/citations 成功响应结构不回退

## 边界检查

阶段 8 手动测试结束后执行：

```bash
git status --short patent_harness_base_副本/
git diff --stat HEAD -- patent_harness_base_副本/
```

期望：

- 两条命令无输出
- SaaS 副本源码未修改
