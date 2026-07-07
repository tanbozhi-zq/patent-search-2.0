# Stage 12 手动测试用例

## 1. 前置条件

启动自研 API：

```bash
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

设置环境变量：

```bash
export BASE_URL=http://127.0.0.1:8000
export API_TOKEN="$(grep -E '^API_TOKEN=' .env | cut -d= -f2-)"
export PATENT_SEARCH_BASE_URL="$BASE_URL"
export PATENT_SEARCH_API_TOKEN="$API_TOKEN"
export PATENT_SEARCH_USE_SELF_HOSTED=true
export PATENT_SEARCH_PAGE_SIZE_LIMIT=50
export PATENT_SEARCH_TIMEOUT_SECONDS=30
```

如果本地未启用鉴权，`API_TOKEN` 可为空；联调和准生产环境必须启用鉴权。

## 2. API 用例

### 用例 1：健康检查

```bash
curl -s "$BASE_URL/health" | python3 -m json.tool
```

期望：

- HTTP 200
- `success=true`
- `data.status=healthy`

### 用例 2：普通关键词检索

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"阀门","page":1,"page_size":2}' | python3 -m json.tool
```

期望：

- HTTP 200
- 顶层包含 `total`、`page`、`page_size`、`records`
- Stage 12 兼容完成后包含 `total_pages`、`next_page`、`took_ms`

### 用例 3：IPC + 技术词检索

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"ipc:H02M AND tscd:(均衡 OR 平衡)","page":1,"page_size":2}' | python3 -m json.tool
```

期望：

- HTTP 200
- 返回 `records`
- `records[*].id` 可用于详情查询

### 用例 4：agency 检索

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"agency:(知识产权代理)","page":1,"page_size":2}' | python3 -m json.tool
```

期望：

- Stage 12 兼容完成后 HTTP 200
- 未完成前应记录为待开发缺口

### 用例 5：agent 检索

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"agent:(张)","page":1,"page_size":2}' | python3 -m json.tool
```

期望：

- Stage 12 兼容完成后 HTTP 200
- 未完成前应记录为待开发缺口

### 用例 6：裸 IPC 自动识别

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"H02M","page":1,"page_size":2}' | python3 -m json.tool
```

期望：

- Stage 12 兼容完成后按 IPC 查询
- 不应被当作普通标题摘要关键词造成明显偏离

### 用例 7：详情

先从 search 结果取一个 ID：

```bash
export PATENT_ID=从_search_records_中复制_id
```

调用详情：

```bash
curl -s "$BASE_URL/api/patent/detail/$PATENT_ID?include_description=true" \
  -H "X-API-Key: $API_TOKEN" | python3 -m json.tool
```

期望：

- HTTP 200
- 包含 `id`、`patent_id`、`claims`
- `include_description=true` 时包含 `description`

### 用例 8：引证

```bash
curl -s "$BASE_URL/api/patent/citations/$PATENT_ID" \
  -H "X-API-Key: $API_TOKEN" | python3 -m json.tool
```

期望：

- HTTP 200
- 包含 `cited_by`、`patent_references`、`non_patent_references`

### 用例 9：错误查询式

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"ipc:H02M AND AND tscd:(均衡)"}' | python3 -m json.tool
```

期望：

- HTTP 400
- `success=false`
- `code=40001`
- 不包含 FastAPI 默认 `detail` 数组

## 3. Tool 用例

开发人员完成 `deerflow_tool/` 后执行。

### 用例 10：Tool search

```bash
python3 - <<'PY'
import json
from deerflow_tool.tools import patent_search

data = json.loads(patent_search(q="阀门", page=1, page_size=2))
print(json.dumps(data, ensure_ascii=False, indent=2))
assert "patents" in data
assert "records" not in data
PY
```

期望：

- 返回 `patents`
- 不返回 `records`

### 用例 11：Tool detail

```bash
python3 - <<'PY'
import json
from deerflow_tool.tools import patent_search, patent_get_detail

search = json.loads(patent_search(q="阀门", page=1, page_size=1))
patent_id = search["patents"][0]["id"]
detail = json.loads(patent_get_detail(patent_id=patent_id, include_description=True))
print(json.dumps(detail, ensure_ascii=False, indent=2))
assert detail["id"] == patent_id or detail.get("patent_id") == patent_id
assert "claims" in detail
PY
```

期望：

- 能使用 search 返回的 `id` 查询详情
- 包含 `claims`

### 用例 12：Tool citations

```bash
python3 - <<'PY'
import json
from deerflow_tool.tools import patent_search, patent_get_citations

search = json.loads(patent_search(q="阀门", page=1, page_size=1))
patent_id = search["patents"][0]["id"]
citations = json.loads(patent_get_citations(patent_id=patent_id))
print(json.dumps(citations, ensure_ascii=False, indent=2))
assert "cited_by" in citations
assert "patent_references" in citations
assert "non_patent_references" in citations
PY
```

期望：

- 引证字段稳定存在

### 用例 13：Tool 错误转换

```bash
python3 - <<'PY'
import json
from deerflow_tool.tools import patent_search

data = json.loads(patent_search(q="ipc:H02M AND AND tscd:(均衡)"))
print(json.dumps(data, ensure_ascii=False, indent=2))
assert "error" in data
assert data.get("code") == 40001
PY
```

期望：

- 返回 `{error, code}`
- 不抛未捕获异常

## 4. DeerFlow / Flow 联调用例

### 用例 14：主链路

在 Flow / DeerFlow agent 中执行：

```text
请检索阀门相关专利，选择第一件专利读取详情，并查看它的引证信息。
```

期望：

- agent 调用 `patent_search`
- agent 使用 `patents[0].id` 调用 `patent_get_detail`
- agent 调用 `patent_get_citations`
- agent 输出可读分析结论

### 用例 15：错误查询

在 Flow / DeerFlow agent 中执行：

```text
请用查询式 ipc:H02M AND AND tscd:(均衡) 检索专利。
```

期望：

- tool 返回错误对象
- agent 能说明查询式非法
- 系统不崩溃

## 5. 记录要求

每个用例记录：

1. 执行时间。
2. 环境和版本提交号。
3. 输入参数。
4. HTTP 状态或工具返回。
5. 是否通过。
6. 失败时的错误信息和截图或日志片段。
