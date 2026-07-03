# API 接口设计说明

## 1. 通用约定

服务基础路径第一版统一使用：

```text
/api/patent
```

第一版鉴权约定：

| 项 | 约定 |
|---|---|
| 鉴权方式 | Header Token |
| 请求头 | `X-API-Key` |
| 开关 | `ENABLE_AUTH=true/false` |
| Token 配置 | `API_TOKEN=实际内部 token` |
| 开发调试 | 可临时关闭鉴权 |
| 联调/生产 | 必须开启鉴权，除非接入公司统一网关鉴权 |

业务接口成功响应原则：

1. `search`、`detail`、`citations` 成功响应优先保持与原外采服务接近的返回结构。
2. 成功响应不强制包裹 `success/code/message/data`。
3. 返回字段以 SaaS 当前实际消费字段为优先，不追求外采服务字段 100% 全量保留。
4. 健康检查、错误响应、内部调试接口可以使用统一结构。

通用失败响应：

```json
{
  "success": false,
  "code": 40001,
  "message": "q 查询语法错误：缺少右括号",
  "data": null
}
```

## 2. 健康检查

```http
GET /health
```

响应：

```json
{
  "success": true,
  "code": 0,
  "message": "ok",
  "data": {
    "status": "healthy",
    "service": "patent-search-service"
  }
}
```

## 3. 专利检索

```http
POST /api/patent/search
```

### 3.1 请求参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `q` | string | 是 | 无 | 查询式 |
| `ds` | string | 否 | `cn` | 数据范围，支持 `cn`、`all` |
| `sort` | string | 否 | `relation` | 排序，支持 `relation`、`!applicationDate` |
| `page` | integer | 否 | `1` | 页码，从 1 开始 |
| `page_size` | integer | 否 | `50` | 每页数量 |
| `highlight` | integer | 否 | `0` | 是否高亮，支持 `0`、`1` |
| `index_analyzer_mode` | string | 否 | `compat` | `compat` / `normal` | 索引 analyzer 兼容模式；当前默认 `compat` |

### 3.2 参数限制

| 参数 | 限制 |
|---|---|
| `q` | 非空，最大长度第一版建议 1000 字符 |
| `page` | 大于等于 1 |
| `page_size` | 大于等于 1，最大值第一版建议 100 |
| `ds` | 只能是 `cn` 或 `all` |
| `sort` | 只能是 `relation` 或 `!applicationDate` |
| `highlight` | 只能是 `0` 或 `1` |

### 3.3 请求示例

```json
{
  "q": "ipc:H02M AND tscd:(\"均衡\" OR \"平衡\")",
  "ds": "cn",
  "sort": "relation",
  "page": 1,
  "page_size": 50,
  "highlight": 0
}
```

### 3.4 响应示例

```json
{
  "total": 128,
  "page": 1,
  "page_size": 50,
  "records": [
    {
      "id": "cn-xxx",
      "patent_id": "cn-xxx",
      "applicationNumber": "CN202411108082.1",
      "documentNumber": "CN119188170B",
      "title": "一种轴承座壳体的加工工艺",
      "ti": "一种轴承座壳体的加工工艺",
      "abstract": "本发明公开了一种...",
      "ab": "本发明公开了一种...",
      "applicant": "某某公司",
      "pa": "某某公司",
      "currentAssignee": "某某公司",
      "inventor": "张三;李四",
      "mainIpc": "B23P15/00",
      "ipcMainList": ["B23P15/00", "B23Q3/00"],
      "applicationDate": "2024-08-13",
      "ad": "2024-08-13",
      "documentDate": "2026-06-12",
      "legalStatus": "授权",
      "currentStatus": "授权",
      "type": "发明专利",
      "score": 12.45
    }
  ]
}
```

## 4. 专利详情

```http
GET /api/patent/detail/{patent_id}
```

### 4.1 路径参数

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `patent_id` | string | 是 | 搜索结果返回的专利内部 ID |

### 4.2 查询参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `include_description` | boolean | 否 | `false` | 是否返回说明书长文本 |

### 4.3 请求示例

```http
GET /api/patent/detail/cn-xxx?include_description=true
```

### 4.4 响应内容

详情成功响应直接返回专利详情对象，不强制包裹 `success/code/message/data`。详情响应应包含：

1. 基础著录信息。
2. 标题、摘要。
3. 申请人、当前权利人、发明人。
4. IPC 分类。
5. 法律状态。
6. 权利要求。
7. 说明书。
8. 附图信息。
9. 同族信息。
10. 引证/相关文献信息。

## 5. 引证/相关文献

```http
GET /api/patent/citations/{patent_id}
```

### 5.1 路径参数

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `patent_id` | string | 是 | 搜索结果返回的专利内部 ID |

### 5.2 响应内容

```json
{
  "patent_id": "cn-xxx",
  "referencesCited": [],
  "referencesCitedRaw": "",
  "referencesCitedText": "",
  "relatedDocuments": []
}
```

## 6. 错误码

| 错误码 | 含义 | HTTP 状态码建议 |
|---|---|---|
| `0` | 成功 | 200 |
| `40001` | 查询语法错误 | 400 |
| `40002` | 参数非法 | 400 |
| `40003` | `page` 或 `page_size` 非法 | 400 |
| `40401` | 专利不存在 | 404 |
| `50001` | OpenSearch 查询异常 | 502 |
| `50002` | 服务内部异常 | 500 |

## 7. 待确认项

1. `highlight=1` 的高亮字段名称和标签格式。
2. `page_size` 最大值是否固定为 100。
3. OpenSearch 异常是否对 SaaS 暴露细节或只返回统一错误。
4. 如果接入公司 API 网关，是否仍保留服务内 `X-API-Key` 二次鉴权。
