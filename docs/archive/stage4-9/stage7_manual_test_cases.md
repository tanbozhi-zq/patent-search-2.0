# 阶段 7 手动测试用例

## 测试目标

验证阶段 7 新增的专利详情和专利引用接口：

- `GET /api/patent/detail/{patent_id}`
- `GET /api/patent/detail/{patent_id}?include_description=true`
- `GET /api/patent/citations/{patent_id}`

## 前置条件

1. 后端服务已启动：

```bash
source .venv/bin/activate
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

2. `.env` 中已配置真实 OpenSearch 凭据和 `API_TOKEN=test-token`。

3. 服务健康检查通过：

```bash
curl -s http://127.0.0.1:8000/health
```

## 测试入口

### 方式一：浏览器可视化测试页

打开：

```text
http://127.0.0.1:8000/test/
```

页面已包含：

1. **搜索区域**：先搜索一个专利，复制 `patent_id`。
2. **专利详情 / 引用测试区域**：
   - 填入 `patent_id`（或公开号、申请号）。
   - 选择是否包含说明书。
   - 点击 **获取详情** 或 **获取引用**。

### 方式二：curl 命令行

以下命令假设：

- `BASE_URL=http://127.0.0.1:8000`
- `API_KEY=test-token`
- 示例 `PATENT_ID=cn-4401457edd8060df`（实际测试时请替换为搜索返回的真实 patent_id）

---

## 用例 1：搜索一个真实专利以获取 patent_id

**目的**：获取用于 detail/citations 测试的专利标识。

**请求**：

```bash
curl -s -X POST http://127.0.0.1:8000/api/patent/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: test-token" \
  -d '{"q":"阀门","page":1,"page_size":1}' | python3 -m json.tool
```

**期望结果**：

- HTTP 200
- `records[0].patent_id` 存在且非空
- `records[0].applicationNumber`、`records[0].documentNumber` 非空

---

## 用例 2：获取专利详情（默认不含说明书）

**目的**：验证 detail 接口默认返回字段，且不含 `description`。

**请求**：

```bash
curl -s http://127.0.0.1:8000/api/patent/detail/cn-4401457edd8060df \
  -H "X-API-Key: test-token" | python3 -m json.tool
```

**期望结果**：

- HTTP 200
- 响应 JSON 直接为详情对象
- 包含字段：`id`、`patent_id`、`title`、`applicationNumber`、`application_number`、`documentNumber`、`document_number`、`applicationDate`、`documentDate`、`legalStatus`、`currentStatus`、`currentAssignee`、`mainIpc`、`claims`
- **不包含** `description` 字段

---

## 用例 3：获取专利详情（包含说明书）

**目的**：验证 `include_description=true` 时返回 `description`。

**请求**：

```bash
curl -s "http://127.0.0.1:8000/api/patent/detail/cn-4401457edd8060df?include_description=true" \
  -H "X-API-Key: test-token" | python3 -m json.tool
```

**期望结果**：

- HTTP 200
- 包含用例 2 中所有字段
- **包含** `description` 字段

---

## 用例 4：使用公开号（PublicationNumber）获取详情

**目的**：验证 identifier 回退逻辑支持公开号。

**请求**：

```bash
# 将 CNXXXXXX.X 替换为真实公开号
curl -s http://127.0.0.1:8000/api/patent/detail/CN115629104A \
  -H "X-API-Key: test-token" | python3 -m json.tool
```

**期望结果**：

- HTTP 200
- 返回对应专利详情

---

## 用例 5：使用申请号（ApplicationNumber）获取详情

**目的**：验证 identifier 回退逻辑支持申请号。

**请求**：

```bash
# 将 CNXXXXXXXX.X 替换为真实申请号
curl -s http://127.0.0.1:8000/api/patent/detail/CN202211234567.8 \
  -H "X-API-Key: test-token" | python3 -m json.tool
```

**期望结果**：

- HTTP 200
- 返回对应专利详情

---

## 用例 6：获取专利引用

**目的**：验证 citations 接口返回全部引用字段。

**请求**：

```bash
curl -s http://127.0.0.1:8000/api/patent/citations/cn-4401457edd8060df \
  -H "X-API-Key: test-token" | python3 -m json.tool
```

**期望结果**：

- HTTP 200
- 包含字段：`patent_id`、`cited_by`、`patent_references`、`non_patent_references`、`referencesCited`、`referencesCitedRaw`、`referencesCitedText`、`relatedDocuments`

---

## 用例 7：专利不存在

**目的**：验证 404 错误响应。

**请求**：

```bash
curl -s http://127.0.0.1:8000/api/patent/detail/not-found-id \
  -H "X-API-Key: test-token" | python3 -m json.tool
```

**期望结果**：

- HTTP 404
- 错误体：

```json
{
    "success": false,
    "code": 40401,
    "message": "patent not found",
    "data": null
}
```

---

## 用例 8：未授权访问

**目的**：验证鉴权生效。

**请求**：

```bash
curl -s http://127.0.0.1:8000/api/patent/detail/cn-4401457edd8060df | python3 -m json.tool
```

**期望结果**：

- HTTP 401 或 403（取决于 `ENABLE_AUTH` 配置）
- 返回缺少 API Key 的提示

---

## 用例 9：专利 ID 为空或非法

**目的**：验证空字符串或特殊字符被拦截。

**请求**：

```bash
curl -s http://127.0.0.1:8000/api/patent/detail/%20 \
  -H "X-API-Key: test-token" | python3 -m json.tool
```

**期望结果**：

- HTTP 400
- 错误体：

```json
{
    "success": false,
    "code": 40002,
    "message": "invalid patent identifier",
    "data": null
}
```

---

## 用例 10：执行官方 smoke 脚本

**目的**：一次性验证 detail 和 citations 三个核心接口。

**请求**：

```bash
python3 scripts/smoke_detail_citations.py http://127.0.0.1:8000 cn-4401457edd8060df test-token
```

**期望结果**：

```json
{"name": "detail", "status": 200, "keys": [...]}
{"name": "detail_description", "status": 200, "keys": [...]}
{"name": "citations", "status": 200, "keys": [...]}
```

退出码：`0`。

---

## 字段检查清单

### detail 响应字段

| 字段 | 类型 | 必须 |
|---|---|---|
| id | string | 是 |
| patent_id | string | 是 |
| title | string | 是 |
| applicationNumber | string | 是 |
| application_number | string | 是 |
| documentNumber | string | 是 |
| document_number | string | 是 |
| applicationDate | string | 是 |
| application_date | string | 是 |
| documentDate | string | 是 |
| document_date | string | 是 |
| legalStatus | string | 是 |
| legal_status | string | 是 |
| currentStatus | string | 是 |
| current_status | string | 是 |
| currentAssignee | string | 是 |
| current_assignee | string | 是 |
| mainIpc | string | 是 |
| main_ipc | string | 是 |
| claims | string | 是 |
| description | string | include_description=true 时 |

### citations 响应字段

| 字段 | 类型 | 必须 |
|---|---|---|
| patent_id | string | 是 |
| cited_by | array | 是 |
| patent_references | array | 是 |
| non_patent_references | array | 是 |
| referencesCited | object/array | 是 |
| referencesCitedRaw | object/array | 是 |
| referencesCitedText | object/array | 是 |
| relatedDocuments | object/array | 是 |

---

## 常见问题

### 1. `description` 字段值为空字符串

属于 OpenSearch 数据源问题。只要 `include_description=true` 时响应中存在 `description` 键即视为 API 契约满足。

### 2. 公开号/申请号查不到

确认该号码在当前 OpenSearch 索引中存在。部分索引可能只包含 `patent_id`，此时公开号和申请号回退可能失败。

### 3. 引用数组为空

表示该专利在 OpenSearch 中没有引用记录，不是接口错误。
