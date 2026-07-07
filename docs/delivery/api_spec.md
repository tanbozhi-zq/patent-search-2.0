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

通用失败响应直接返回扁平结构，不再使用 FastAPI 默认 `422 detail` 数组，也不再包裹外层 `detail` 字段：

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
| `sort` | string | 否 | `relation` | 排序，支持 `relation`、`rank`、`relevance`、`score`、`applicationDate`、`!applicationDate`、`documentDate`、`!documentDate` |
| `page` | integer | 否 | `1` | 页码，从 1 开始 |
| `page_size` | integer | 否 | `50` | 每页数量 |
| `highlight` | integer | 否 | `0` | 高亮兼容参数，支持 `0`、`1`；阶段 8 仅兼容接收，不返回高亮片段 |
| `index_analyzer_mode` | string | 否 | `compat` | 索引 analyzer 兼容模式，支持 `compat`、`normal`；当前默认 `compat` |

### 3.2 参数限制

| 参数 | 限制 |
|---|---|
| `q` | 非空，最大长度第一版建议 1000 字符 |
| `page` | 大于等于 1 |
| `page_size` | 大于等于 1，最大值固定为 100 |
| `ds` | 只能是 `cn` 或 `all` |
| `sort` | 只能是 `relation`、`rank`、`relevance`、`score`、`applicationDate`、`!applicationDate`、`documentDate`、`!documentDate` |
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

`q` 当前支持字段查询：`title`、`ab`、`tscd`、`mainClaim`、`claims`、`description`、`ipc`、`applicant`、`currentAssignee`、`agency`、`agent`、`legalStatus`、`type`，以及 `ad`、`documentYear` 范围查询；同时支持 `AND`、`OR`、`NOT` 和常规多级括号分组，不承诺支持极端深度嵌套或明显不可读的超长表达式。阶段 10.5 起，`mainClaim` 映射 `MainClaim`，`claims` 映射 `Requirement`，`description` 映射 `Instructions`；Stage 12.1 起，`agency` 映射 `Agency` / `AgencyRaw`，`agent` 映射 `Agent`，并支持 `H02M`、`H02M7/483`、`F16K` 等裸 IPC 自动识别。`index_analyzer_mode=compat` 下 `mainClaim`、`claims`、`description` 使用 phrase 查询，`normal` 下使用普通 `multi_match`。

### 3.4 响应示例

```json
{
  "total": 128,
  "page": 1,
  "page_size": 50,
  "total_pages": 3,
  "next_page": 2,
  "took_ms": 35,
  "records": [
    {
      "id": "cn-xxx",
      "patent_id": "cn-xxx",
      "applicationNumber": "CN202411108082.1",
      "application_number": "CN202411108082.1",
      "documentNumber": "CN119188170B",
      "document_number": "CN119188170B",
      "title": "一种轴承座壳体的加工工艺",
      "ti": "一种轴承座壳体的加工工艺",
      "abstract": "本发明公开了一种...",
      "ab": "本发明公开了一种...",
      "summary": "本发明公开了一种...",
      "applicant": "某某公司",
      "pa": "某某公司",
      "currentAssignee": "某某公司",
      "inventor": "张三;李四",
      "mainIpc": "B23P15/00",
      "main_ipc": "B23P15/00",
      "ipcMainList": ["B23P15/00", "B23Q3/00"],
      "applicationDate": "2024-08-13",
      "application_date": "2024-08-13",
      "ad": "2024-08-13",
      "documentDate": "2026-06-12",
      "document_date": "2026-06-12",
      "legalStatus": "授权",
      "legal_status": "授权",
      "currentStatus": "授权",
      "type": "发明专利",
      "score": 12.45
    }
  ]
}
```

阶段 9 起，搜索记录在保留既有 camelCase 字段的同时补充 SaaS 工具层常用字段：`summary`、`application_number`、`document_number`、`application_date`、`document_date`、`legal_status`、`main_ipc`。`summary` 来源为 `Abstract`，与 `abstract` 同值。当前 HTTP API 顶层继续返回 `records`，不改为 PatentHub 工具层的 `patents`。

Stage 12.1 起，搜索响应顶层补充分页和耗时元数据：

| 字段 | 说明 |
|---|---|
| `total_pages` | `ceil(total / page_size)`；`total=0` 时为 `0` |
| `next_page` | 有下一页时为 `page + 1`，否则为 `null` |
| `took_ms` | OpenSearch raw response 的 `took`；缺失时为 `null` |

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
7. 说明书（受 `include_description` 控制）。
8. 附图信息。
9. 同族信息。
10. 引证/相关文献信息。

#### 4.4.1 双兼容字段（阶段七）

阶段七起，详情接口对核心关键字段同时输出 HTTP API 风格的 camelCase 字段与 SaaS PatentHub 工具层实际消费的 snake_case 别名，保证两套消费方均无需自行改写。设计依据见 `docs/superpowers/specs/2026-07-06-stage-7-detail-citations-design.md` §7.2。

基础著录字段（camelCase + snake_case 别名）：

| camelCase 字段 | snake_case 别名 | OpenSearch 来源 |
|---|---|---|
| `applicationNumber` | `application_number` | `ApplicationNumber` |
| `documentNumber` | `document_number` | `PublicationNumber` |
| `applicationDate` | `application_date` | `ApplicationDate` |
| `documentDate` | `document_date` | `PublicationDate` |
| `legalStatus` | `legal_status` | `LatestLegalStatus` 命中后回退 `LegalStatus` |
| `currentStatus` | `current_status` | `LatestLegalStatus` |
| `currentAssignee` | `current_assignee` | `Assignee` 命中后回退 `Applicant` |
| `mainIpc` | `main_ipc` | `IPC` |
| `ipcMainList` | `ipc_main_list` | `IPCList` |
| `firstApplicant` | `first_applicant` | `FirstApplicant` |
| `firstInventor` | `first_inventor` | `FirstInventor` |
| `applicantAddress` | `applicant_address` | `ApplicantAddress` |
| `priorityNumber` | `priority_number` | `PriorityNumber` |
| `fullPriorityNumber` | `full_priority_number` | `FullPriorityNumber` |
| `pctDate` | `pct_date` | `PCTDate` |
| `pctApplicationData` | `pct_application_data` | `PCTApplicationData` |
| `pctPublicationData` | `pct_publication_data` | `PCTPublicationData` |
| `imagePath` | `image_path` | `AbstractFigureUrl` / `ImagePath` |
| `pdfList` | `pdf_list` | `PDFList` |
| `legalStatusHistory` | `legal_status_history` | `LegalStatusHistory`，回退 `LegalStatus` |

文本字段（保持现有别名，部分字段不设 snake_case 别名）：

| 字段 | 别名 | OpenSearch 来源 |
|---|---|---|
| `title` | `ti` | `Title` |
| `abstract` | `ab` / `summary` | `Abstract` |
| `mainClaim` | `main_claim` | `MainClaim` |
| `claims` | 无 | `Requirement` |
| `description` | 无 | `Instructions`，仅在 `include_description=true` 时返回 |

主体字段：

| camelCase 字段 | snake_case 别名 | OpenSearch 来源 |
|---|---|---|
| `applicant` | 无 | `Applicant` |
| `assignee` | 无 | `Assignee` |
| `inventor` | 无 | `Inventor` |
| `agency` | 无 | `Agency` |
| `agent` | 无 | `Agent` |

分类与扩展字段：

| 字段 | OpenSearch 来源 |
|---|---|
| `ipc` | `IPC` |
| `loc` | `LOC` |
| `family` | `Family`, `SimpleFamily`, `ExtendedFamily`, `DocDBFamily` |
| `drawings` | `Drawings`, `DescriptionImages` |

空值规则沿用 `docs/delivery/field_mapping.md` 第 6 节；`legalStatus` 优先 `LatestLegalStatus`，缺失回退 `LegalStatus`；`currentAssignee` 优先 `Assignee`，缺失回退 `Applicant`。

#### 4.4.2 响应示例（阶段七）

```json
{
  "id": "cn-xxx",
  "patent_id": "cn-xxx",
  "title": "一种轴承座壳体的加工工艺",
  "ti": "一种轴承座壳体的加工工艺",
  "abstract": "本发明公开了一种...",
  "ab": "本发明公开了一种...",
  "summary": "本发明公开了一种...",
  "applicationNumber": "CN202411108082.1",
  "application_number": "CN202411108082.1",
  "documentNumber": "CN119188170B",
  "document_number": "CN119188170B",
  "applicationDate": "2024-08-13",
  "application_date": "2024-08-13",
  "documentDate": "2026-06-12",
  "document_date": "2026-06-12",
  "applicant": "某某公司",
  "currentAssignee": "某某公司",
  "current_assignee": "某某公司",
  "inventor": "张三;李四",
  "mainIpc": "B23P15/00",
  "main_ipc": "B23P15/00",
  "ipcMainList": ["B23P15/00", "B23Q3/00"],
  "ipc_main_list": ["B23P15/00", "B23Q3/00"],
  "legalStatus": "授权",
  "legal_status": "授权",
  "currentStatus": "授权",
  "current_status": "授权",
  "type": "发明专利",
  "mainClaim": "一种轴承座壳体的加工工艺，其特征在于...",
  "main_claim": "一种轴承座壳体的加工工艺，其特征在于...",
  "claims": "1. 一种轴承座壳体的加工工艺..."
}
```

`include_description=true` 时在上述结构上追加 `description` 字段；缺省时不返回该字段，避免长文本默认进入响应。

## 5. 引证/相关文献

```http
GET /api/patent/citations/{patent_id}
```

### 5.1 路径参数

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `patent_id` | string | 是 | 搜索结果返回的专利内部 ID |

### 5.2 响应内容

阶段七起，引证接口同时输出 SaaS PatentHub 工具层字段与本项目原始兼容字段。设计依据见 `docs/superpowers/specs/2026-07-06-stage-7-detail-citations-design.md` §8.2。

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

| 字段 | OpenSearch 来源 | 说明 |
|---|---|---|
| `patent_id` | request / `_source.patent_id` | 专利稳定 ID |
| `cited_by` | `RelatedDocuments` 尽力归一化 | 被引专利摘要列表 |
| `patent_references` | `ReferencesCited` 尽力归一化 | 引用专利摘要列表 |
| `non_patent_references` | `ReferencesCitedRaw` / `ReferencesCitedText` | 非专利文献或原始引用文本 |
| `referencesCited` | `ReferencesCited` | 原始结构化引证字段 |
| `referencesCitedRaw` | `ReferencesCitedRaw` | 原始引证文本 |
| `referencesCitedText` | `ReferencesCitedText` | 文本化引证列表 |
| `relatedDocuments` | `RelatedDocuments` | 原始相关文献 |

`cited_by` 与 `patent_references` 在能结构化时输出 SaaS 工具层格式摘要：

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

若 OpenSearch 中 `ReferencesCited` / `RelatedDocuments` 已为结构化数组，mapper 尽量归一化到上述摘要字段；如为字符串或未知结构，则保留原始值到兼容字段，`patent_references` / `cited_by` 返回空数组 `[]`。

兼容 OpenSearch 原始引文字段：

1. 当引用条目仅包含 `DocNumber`、`Country`、`Kind` 时，摘要 `id` 按 `Country + DocNumber + Kind` 合成，例如 `{"Country":"CN","DocNumber":"112501955","Kind":"A"}` 输出 `id="CN112501955A"`。
2. 当引用条目包含 `Date` 且缺少 `ApplicationDate` 时，摘要 `application_date` 使用 `Date`。
3. 归一化摘要会跳过完全无法识别的空摘要，并对完全相同的摘要条目去重。
4. 原始引用对象仍完整保留在 `referencesCited` 或 `relatedDocuments`，归一化摘要仅用于 SaaS 工具层兼容展示与后续追查。

## 6. 法律历史

```http
GET /api/patent/legal-history/{patent_id}
```

### 6.1 路径参数

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `patent_id` | string | 是 | 搜索结果返回的专利内部 ID |

### 6.2 响应内容

Stage 12.1 先提供稳定基础结构，供 DeerFlow Tool / MCP 的 `patent_get_legal_history` 封装使用。当前不承诺完整法律事务历史。

```json
{
  "patent_id": "cn-xxx",
  "transaction_count": 0,
  "transactions": []
}
```

若 OpenSearch 文档中存在结构化 `LegalStatusHistory` 数组，`transactions` 尽量原样返回，`transaction_count` 等于数组长度；无结构化数据时返回空数组。

## 7. 错误码

| 错误码 | 含义 | HTTP 状态码建议 |
|---|---|---|
| `0` | 成功 | 200 |
| `40001` | 查询语法错误 | 400 |
| `40002` | 参数非法 | 400 |
| `40003` | `page` 或 `page_size` 非法 | 400 |
| `40101` | 鉴权缺失或错误 | 401 |
| `40401` | 专利不存在 | 404 |
| `50001` | OpenSearch 查询异常 | 502 |
| `50002` | 服务内部异常 | 500 |

## 8. 错误响应策略

1. 查询语法错误返回 `40001`，且不会访问 OpenSearch。
2. Pydantic/FastAPI 参数校验错误统一转为 HTTP 400；普通参数为 `40002`，`page` / `page_size` 为 `40003`。
3. OpenSearch 查询异常统一返回 `50001`，响应不暴露连接串、账号密码或内部堆栈。
4. 服务内 `X-API-Key` 鉴权继续保留；若后续接入公司 API 网关，是否关闭二次鉴权由后续阶段确认。
