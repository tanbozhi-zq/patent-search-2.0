# 阶段 10.5 手动测试用例

## 前置条件

```bash
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
export BASE_URL=http://127.0.0.1:8000
export API_TOKEN="$(grep -E '^API_TOKEN=' .env | cut -d= -f2-)"
```

## 用例 1：首权检索

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"mainClaim:(均衡)","page":1,"page_size":1}' | python3 -m json.tool
```

期望：

- HTTP 200
- 响应包含 `total`、`records`

## 用例 2：权利要求检索

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"claims:(均衡)","page":1,"page_size":1}' | python3 -m json.tool
```

期望：

- HTTP 200
- 响应包含 `total`、`records`

## 用例 3：说明书检索

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"description:(均衡)","page":1,"page_size":1}' | python3 -m json.tool
```

期望：

- HTTP 200
- 响应包含 `total`、`records`

## 用例 4：组合查询

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"ipc:H02M AND claims:(均衡)","page":1,"page_size":1}' | python3 -m json.tool
```

期望：

- HTTP 200

## 用例 5：非法语法

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"claims:()"}' | python3 -m json.tool
```

期望：

- HTTP 400
- code `40001`
- 响应不含外层 `detail`

