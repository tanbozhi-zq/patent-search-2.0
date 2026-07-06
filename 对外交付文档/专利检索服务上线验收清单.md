# 专利检索服务上线验收清单

## 1. 验收准备

验收前由服务提供方提供：

```bash
export BASE_URL="http://服务地址"
export API_TOKEN="由服务提供方发放的 API Key"
export PATENT_ID="从检索结果 records[0].patent_id 取得的专利 ID"
```

所有业务接口请求均需携带：

```http
X-API-Key: $API_TOKEN
```

## 2. 验收项

| 编号 | 验收项 | 请求示例 | 预期结果 | 结论 | 备注 |
|---|---|---|---|---|---|
| 1 | 健康检查 | `GET /health` | HTTP 200，`data.status=healthy` | 待验收 |  |
| 2 | 基础检索 | `q=阀门` | HTTP 200，返回 `total/page/page_size/records` | 待验收 |  |
| 3 | 标题检索 | `q=title:(阀门)` | HTTP 200，`records` 为数组 | 待验收 |  |
| 4 | 摘要检索 | `q=ab:(缓冲)` | HTTP 200，`records` 为数组 | 待验收 |  |
| 5 | 综合全文检索 | `q=tscd:(均衡)` | HTTP 200，`records` 为数组 | 待验收 |  |
| 6 | 首权检索 | `q=mainClaim:(均衡)` | HTTP 200，`records` 为数组 | 待验收 |  |
| 7 | 权利要求检索 | `q=claims:(均衡)` | HTTP 200，`records` 为数组 | 待验收 |  |
| 8 | 说明书检索 | `q=description:(均衡)` | HTTP 200，`records` 为数组 | 待验收 |  |
| 9 | 组合查询 | `q=ipc:H02M AND claims:(均衡)` | HTTP 200，`records` 为数组 | 待验收 |  |
| 10 | 详情接口 | `GET /api/patent/detail/{patent_id}` | HTTP 200，返回核心详情字段 | 待验收 |  |
| 11 | 详情说明书 | `GET /api/patent/detail/{patent_id}?include_description=true` | HTTP 200，包含 `description` 字段 | 待验收 |  |
| 12 | 引证接口 | `GET /api/patent/citations/{patent_id}` | HTTP 200，返回引证结构 | 待验收 |  |
| 13 | 查询语法错误 | `q=claims:()` | HTTP 400，`code=40001`，无外层 `detail` | 待验收 |  |
| 14 | 分页参数错误 | `page_size=101` | HTTP 400，`code=40003`，无外层 `detail` | 待验收 |  |
| 15 | 鉴权失败 | 不传 `X-API-Key` | HTTP 401，`code=40101`，无外层 `detail` | 待验收 |  |
| 16 | 高亮兼容 | `highlight=1` | HTTP 200，不要求返回高亮片段 | 待验收 |  |

## 3. 验收命令参考

健康检查：

```bash
curl -s "$BASE_URL/health" | python3 -m json.tool
```

基础检索：

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"阀门","page":1,"page_size":1}' | python3 -m json.tool
```

细粒度文本检索：

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"mainClaim:(均衡)","page":1,"page_size":1}' | python3 -m json.tool

curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"claims:(均衡)","page":1,"page_size":1}' | python3 -m json.tool

curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"description:(均衡)","page":1,"page_size":1}' | python3 -m json.tool
```

详情和引证：

```bash
curl -s "$BASE_URL/api/patent/detail/$PATENT_ID" \
  -H "X-API-Key: $API_TOKEN" | python3 -m json.tool

curl -s "$BASE_URL/api/patent/detail/$PATENT_ID?include_description=true" \
  -H "X-API-Key: $API_TOKEN" | python3 -m json.tool

curl -s "$BASE_URL/api/patent/citations/$PATENT_ID" \
  -H "X-API-Key: $API_TOKEN" | python3 -m json.tool
```

错误码：

```bash
curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"claims:()"}' | python3 -m json.tool

curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"阀门","page":1,"page_size":101}' | python3 -m json.tool

curl -s -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -d '{"q":"阀门"}' | python3 -m json.tool
```

## 4. 验收结论

| 项 | 结论 |
|---|---|
| 是否通过上线验收 | 待确认 |
| 需求方确认人 | 待填写 |
| 接入方确认人 | 待填写 |
| 服务提供方确认人 | 待填写 |
| 验收日期 | 待填写 |
| 遗留问题 | 待填写 |

