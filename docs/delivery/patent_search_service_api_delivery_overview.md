# 专利检索服务接口交付说明

## 1. 这份文档说明什么

本文面向需求方和接入方，用来说明专利检索服务最终交付的几个接口是什么、分别解决什么问题、应该按什么顺序调用，以及每个接口大致会返回哪些信息。

本服务的交付形式是：

```text
服务提供方部署好接口服务，接入方拿到服务地址 BASE_URL 和接口调用凭证 API Key 后，通过 HTTP 接口调用。
```

接入方不需要自己部署代码。

## 2. 接入方会拿到什么

| 项 | 说明 |
|---|---|
| `BASE_URL` | 服务访问地址，例如 `http://host:8000` 或公司网关地址 |
| `API Key` | 接口调用凭证 |
| 接口文档 | 本文和其他对外文档 |

业务接口调用时需要在请求头里带上：

```http
X-API-Key: <接口调用凭证>
```

## 3. 总共有几个接口

正式接入时，核心是 3 个业务接口和 1 个健康检查接口：

| 接口 | 方法 | 路径 | 作用 |
|---|---|---|---|
| 专利检索 | `POST` | `/api/patent/search` | 搜索专利列表 |
| 专利详情 | `GET` | `/api/patent/detail/{patent_id}` | 查看某一件专利的详细信息 |
| 引证信息 | `GET` | `/api/patent/citations/{patent_id}` | 查看某一件专利的引用关系 |
| 健康检查 | `GET` | `/health` | 判断服务是否正常运行 |

一句话理解：

```text
search 负责“找专利”
detail 负责“看某一件专利的详细内容”
citations 负责“看某一件专利的引用关系”
health 负责“检查服务是否可用”
```

## 4. 推荐调用流程

正常业务流程是：

```text
第一步：调用 search 搜索专利列表
第二步：从搜索结果里拿到 patent_id
第三步：用 patent_id 调 detail 查看详情
第四步：用 patent_id 调 citations 查看引用关系
```

示例流程：

```text
用户搜索：ipc:H02M AND claims:(均衡)
系统调用：POST /api/patent/search
返回结果：records[0].patent_id = cn-xxx
用户点开详情：GET /api/patent/detail/cn-xxx
用户查看引用：GET /api/patent/citations/cn-xxx
```

### 4.1 接口返回字段总览

下面这张表用于先整体理解每个接口会返回什么。更详细的调用方式和字段解释见后续各接口章节。

| 接口 | 返回重点 | 适合什么场景 |
|---|---|---|
| `search` | 返回分页信息和专利列表摘要 | 搜索结果页、列表展示、拿 `patent_id` |
| `detail` | 返回单件专利详情 | 用户点开某一件专利后查看详细内容 |
| `citations` | 返回引用、被引和原始引证字段 | 查看专利关系、技术脉络、引用分析 |
| `health` | 返回服务健康状态 | 判断接口服务是否可用 |

重要说明：

```text
接口会按约定返回字段结构，但字段是否有值取决于 OpenSearch 原始数据是否存在对应内容。
如果某件专利原始数据缺少说明书、完整权利要求、法律状态、附图或引证信息，对应字段可能是空字符串 "" 或空数组 []。
```

也就是说：

```text
detail 接口的字段结构比 search 更完整；
但如果某件专利本身缺少详情数据，detail 返回结果看起来可能和 search 差不多。
```

### 4.2 `search` 返回字段

`search` 顶层返回：

| 字段 | 说明 |
|---|---|
| `total` | 总命中数量 |
| `page` | 当前页码 |
| `page_size` | 每页数量 |
| `records` | 当前页专利摘要列表 |

`records` 中每条专利摘要主要返回：

| 字段 | 说明 |
|---|---|
| `id` / `patent_id` | 专利内部 ID，后续查详情和引证要用它 |
| `title` / `ti` | 标题 |
| `abstract` / `ab` / `summary` | 摘要 |
| `applicationNumber` / `application_number` | 申请号 |
| `documentNumber` / `document_number` | 公开号 |
| `applicationDate` / `application_date` / `ad` | 申请日 |
| `documentDate` / `document_date` | 公开日 |
| `applicant` / `pa` | 申请人 |
| `currentAssignee` | 当前权利人 |
| `inventor` | 发明人 |
| `mainIpc` / `main_ipc` | 主 IPC |
| `ipcMainList` | IPC 列表 |
| `legalStatus` / `legal_status` | 法律状态 |
| `currentStatus` | 当前状态 |
| `type` | 专利类型 |
| `score` | 搜索相关性分数 |

`search` 默认不返回完整长文本字段，例如 `mainClaim`、`claims`、`description`，这些需要通过 `detail` 获取。

### 4.3 `detail` 返回字段

`detail` 返回的是单件专利详情。它会在 `search` 摘要字段基础上，增加更多详情字段。

| 字段 | 说明 |
|---|---|
| `id` / `patent_id` | 专利内部 ID |
| `title` / `ti` | 标题 |
| `abstract` / `ab` / `summary` | 摘要 |
| `applicationNumber` / `application_number` | 申请号 |
| `documentNumber` / `document_number` | 公开号 |
| `applicationDate` / `application_date` | 申请日 |
| `documentDate` / `document_date` | 公开日 |
| `type` | 专利类型 |
| `legalStatus` / `legal_status` | 法律状态 |
| `currentStatus` / `current_status` | 当前状态 |
| `applicant` | 申请人 |
| `firstApplicant` / `first_applicant` | 第一申请人 |
| `currentAssignee` / `current_assignee` | 当前权利人 |
| `assignee` | 权利人 |
| `inventor` | 发明人 |
| `firstInventor` / `first_inventor` | 第一发明人 |
| `applicantAddress` / `applicant_address` | 申请人地址 |
| `agency` | 代理机构 |
| `agent` | 代理人 |
| `ipc` / `mainIpc` / `main_ipc` | 主 IPC |
| `ipcMainList` / `ipc_main_list` | IPC 列表 |
| `loc` | 外观设计分类 |
| `priorityNumber` / `priority_number` | 优先权号 |
| `fullPriorityNumber` / `full_priority_number` | 完整优先权号 |
| `pctDate` / `pct_date` | PCT 日期 |
| `pctApplicationData` / `pct_application_data` | PCT 申请信息 |
| `pctPublicationData` / `pct_publication_data` | PCT 公布信息 |
| `imagePath` / `image_path` | 摘要图或图片地址 |
| `pdfList` / `pdf_list` | PDF 文件列表 |
| `family` | 同族信息 |
| `drawings` | 附图信息 |
| `legalStatusHistory` / `legal_status_history` | 法律状态历史 |
| `mainClaim` / `main_claim` | 首权或主权利要求 |
| `claims` | 完整权利要求书 |
| `description` | 说明书正文，仅 `include_description=true` 时返回 |

注意：

```text
include_description=true 只是表示“如果有说明书正文就返回”；
如果原始数据里没有 Instructions，description 仍然可能是空字符串。
```

### 4.4 `citations` 返回字段

`citations` 返回当前专利的引用关系和原始引证字段。

| 字段 | 说明 |
|---|---|
| `patent_id` | 当前专利 ID |
| `cited_by` | 被引或相关专利摘要列表 |
| `patent_references` | 当前专利引用的专利摘要列表 |
| `non_patent_references` | 非专利文献或原始引用文本 |
| `referencesCited` | 原始结构化引用字段 |
| `referencesCitedRaw` | 原始引用文本 |
| `referencesCitedText` | 文本化引用信息 |
| `relatedDocuments` | 原始相关文献字段 |

`cited_by` 和 `patent_references` 中的专利摘要通常包含：

| 字段 | 说明 |
|---|---|
| `id` | 引用专利 ID |
| `title` | 标题 |
| `applicant` | 申请人 |
| `application_date` | 申请日 |
| `application_number` | 申请号 |
| `type` | 专利类型 |
| `legal_status` | 法律状态 |
| `main_ipc` | 主 IPC |

如果原始数据里没有结构化引证信息，`cited_by` 或 `patent_references` 可能返回空数组 `[]`。

### 4.5 `health` 返回字段

`health` 用于检查服务是否可用。

| 字段 | 说明 |
|---|---|
| `success` | 是否成功 |
| `code` | 状态码，健康时为 `0` |
| `message` | 状态消息 |
| `data.status` | 服务状态，健康时为 `healthy` |
| `data.service` | 服务名称 |

## 5. 专利检索接口

### 5.1 接口作用

专利检索接口用于根据关键词、IPC、申请人、标题、摘要、首权、权利要求、说明书等条件，搜索出一批专利结果。

它返回的是“列表摘要”，类似搜索引擎的结果页。

它不是用来返回每件专利的全部内容。

### 5.2 请求方式

```http
POST /api/patent/search
```

### 5.3 常用请求参数

| 参数 | 类型 | 是否必填 | 说明 |
|---|---|---|---|
| `q` | string | 是 | 查询式 |
| `page` | integer | 否 | 页码，从 1 开始，默认 1 |
| `page_size` | integer | 否 | 每页数量，默认 50，最大 100 |
| `ds` | string | 否 | 数据范围，默认 `cn` |
| `sort` | string | 否 | 排序方式，默认按相关性 |
| `highlight` | integer | 否 | 可传 `0` 或 `1`，当前仅兼容接收 |

### 5.4 查询式示例

```text
阀门
title:(阀门)
ab:(缓冲)
tscd:("均衡" OR "平衡")
mainClaim:(均衡)
claims:(均衡)
description:(均衡)
ipc:H02M AND claims:(均衡)
(title:(均衡) OR (title:(平衡) AND ipc:H02M))
```

其中：

| 查询字段 | 含义 |
|---|---|
| `title` | 标题 |
| `ab` | 摘要 |
| `tscd` | 标题、摘要、首权、权利要求、说明书综合检索 |
| `mainClaim` | 首权或主权利要求 |
| `claims` | 完整权利要求书 |
| `description` | 说明书 |
| `ipc` | IPC 分类 |

### 5.5 请求示例

```bash
curl -X POST "$BASE_URL/api/patent/search" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{
    "q": "ipc:H02M AND claims:(均衡)",
    "page": 1,
    "page_size": 10
  }'
```

### 5.6 返回内容

返回结构：

```json
{
  "total": 128,
  "page": 1,
  "page_size": 10,
  "records": []
}
```

字段含义：

| 字段 | 说明 |
|---|---|
| `total` | 总命中数量 |
| `page` | 当前页码 |
| `page_size` | 每页数量 |
| `records` | 当前页的专利摘要列表 |

每条 `record` 主要包含：

| 字段 | 说明 |
|---|---|
| `patent_id` | 专利内部 ID，后续查详情和引证要用它 |
| `title` | 标题 |
| `abstract` | 摘要 |
| `summary` | 摘要兼容字段 |
| `applicationNumber` / `application_number` | 申请号 |
| `documentNumber` / `document_number` | 公开号 |
| `applicationDate` / `application_date` | 申请日 |
| `documentDate` / `document_date` | 公开日 |
| `applicant` | 申请人 |
| `currentAssignee` | 当前权利人 |
| `mainIpc` / `main_ipc` | 主 IPC |
| `legalStatus` / `legal_status` | 法律状态 |
| `type` | 专利类型 |

### 5.7 检索接口默认不返回什么

检索接口默认不返回以下长文本或扩展信息：

| 不默认返回的信息 | 获取方式 |
|---|---|
| 首权正文 `mainClaim` | 调用详情接口 |
| 完整权利要求 `claims` | 调用详情接口 |
| 说明书正文 `description` | 调用详情接口，并传 `include_description=true` |
| 同族信息 | 调用详情接口 |
| 附图/PDF 信息 | 调用详情接口 |
| 引用/被引用信息 | 调用引证接口 |

这样设计是为了避免搜索列表一次返回过大的数据，影响速度和接入体验。

## 6. 专利详情接口

### 6.1 接口作用

专利详情接口用于查看某一件专利的详细信息。

通常是在搜索结果里拿到 `patent_id` 后，再调用详情接口。

### 6.2 请求方式

```http
GET /api/patent/detail/{patent_id}
```

其中 `{patent_id}` 来自检索接口返回的 `records[].patent_id`。

### 6.3 查询参数

| 参数 | 类型 | 是否必填 | 说明 |
|---|---|---|---|
| `include_description` | boolean | 否 | 是否返回说明书正文，默认 `false` |

说明书正文通常很长，所以默认不返回。需要说明书时，调用：

```http
GET /api/patent/detail/{patent_id}?include_description=true
```

### 6.4 请求示例

```bash
curl "$BASE_URL/api/patent/detail/$PATENT_ID" \
  -H "X-API-Key: $API_TOKEN"
```

带说明书正文：

```bash
curl "$BASE_URL/api/patent/detail/$PATENT_ID?include_description=true" \
  -H "X-API-Key: $API_TOKEN"
```

### 6.5 返回内容

详情接口返回某一件专利的详细对象，主要包含：

| 字段 | 说明 |
|---|---|
| `patent_id` | 专利内部 ID |
| `title` | 标题 |
| `abstract` | 摘要 |
| `applicationNumber` / `application_number` | 申请号 |
| `documentNumber` / `document_number` | 公开号 |
| `applicationDate` / `application_date` | 申请日 |
| `documentDate` / `document_date` | 公开日 |
| `applicant` | 申请人 |
| `inventor` | 发明人 |
| `currentAssignee` / `current_assignee` | 当前权利人 |
| `mainIpc` / `main_ipc` | 主 IPC |
| `ipcMainList` / `ipc_main_list` | IPC 列表 |
| `legalStatus` / `legal_status` | 法律状态 |
| `mainClaim` / `main_claim` | 首权或主权利要求 |
| `claims` | 完整权利要求书 |
| `description` | 说明书正文，仅 `include_description=true` 时返回 |
| `family` | 同族信息 |
| `drawings` | 附图信息 |
| `pdfList` / `pdf_list` | PDF 信息 |

## 7. 引证信息接口

### 7.1 接口作用

引证信息接口用于查看某一件专利和其他文献之间的引用关系。

它适合用于：

- 查看这件专利引用了哪些专利。
- 查看相关专利或被引信息。
- 做技术脉络、竞品专利、专利关系分析。

### 7.2 请求方式

```http
GET /api/patent/citations/{patent_id}
```

其中 `{patent_id}` 来自检索接口返回的 `records[].patent_id`。

### 7.3 请求示例

```bash
curl "$BASE_URL/api/patent/citations/$PATENT_ID" \
  -H "X-API-Key: $API_TOKEN"
```

### 7.4 返回内容

返回结构示例：

```json
{
  "patent_id": "cn-xxx",
  "cited_by": [],
  "patent_references": [],
  "non_patent_references": [],
  "referencesCited": [],
  "referencesCitedRaw": "",
  "referencesCitedText": "",
  "relatedDocuments": []
}
```

字段含义：

| 字段 | 说明 |
|---|---|
| `patent_id` | 当前专利 ID |
| `cited_by` | 被引或相关专利摘要列表 |
| `patent_references` | 当前专利引用的专利摘要列表 |
| `non_patent_references` | 非专利文献或原始引用文本 |
| `referencesCited` | 原始结构化引用字段 |
| `referencesCitedRaw` | 原始引用文本 |
| `referencesCitedText` | 文本化引用信息 |
| `relatedDocuments` | 原始相关文献字段 |

`cited_by` 和 `patent_references` 中的专利摘要通常包含：

| 字段 | 说明 |
|---|---|
| `id` | 引用专利 ID |
| `title` | 标题 |
| `applicant` | 申请人 |
| `application_date` | 申请日 |
| `application_number` | 申请号 |
| `type` | 专利类型 |
| `legal_status` | 法律状态 |
| `main_ipc` | 主 IPC |

## 8. 健康检查接口

### 8.1 接口作用

健康检查接口用于判断服务是否正常运行。

它通常给接入方、运维或监控系统使用。

### 8.2 请求方式

```http
GET /health
```

### 8.3 请求示例

```bash
curl "$BASE_URL/health"
```

### 8.4 返回内容

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

如果 `data.status=healthy`，说明服务正在运行。

## 9. 常见问题

### 9.1 检索时会默认返回所有信息吗

不会。

检索接口默认只返回搜索结果列表摘要。完整详情需要用 `patent_id` 再调用详情接口。

### 9.2 为什么检索接口不直接返回说明书和完整权利要求

因为这些字段通常很长。搜索一次可能返回几十条结果，如果每条都带上说明书和完整权利要求，接口响应会很大，也会变慢。

更合理的方式是：

```text
先用 search 找到专利，再用 detail 查看某一件专利的完整内容。
```

### 9.3 说明书正文怎么获取

调用详情接口时加上：

```http
include_description=true
```

示例：

```http
GET /api/patent/detail/{patent_id}?include_description=true
```

### 9.4 引证信息是不是详情接口的一部分

不是。

引证信息单独通过 `citations` 接口获取：

```http
GET /api/patent/citations/{patent_id}
```

这样可以避免详情接口默认返回过多信息。

## 10. 最简接入总结

接入方只需要记住：

```text
1. 调 search 搜索专利列表。
2. 从 records 里取 patent_id。
3. 调 detail 查看这件专利的详细内容。
4. 调 citations 查看这件专利的引用关系。
5. 调 health 检查服务是否可用。
```
