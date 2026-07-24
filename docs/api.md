# 专利检索 HTTP API 与 MCP 对接

本文是对外调用契约。运行中的 OpenAPI 规格见 `<BASE_URL>/docs` 或 `<BASE_URL>/redoc`；接口实现变更时须同步更新本文。不要把 Token、OpenSearch 凭据或服务器私钥写入此文档。

## 1. 两种接入方式

| 方式 | 地址 | 鉴权 | 适用场景 |
|---|---|---|---|
| HTTP API | `<BASE_URL>/api/patent/*` | `X-API-Key: <API_TOKEN>` | 应用、后端服务或批处理程序直接调用。 |
| Streamable HTTP MCP | `<MCP_URL>/mcp` | `Authorization: Bearer <MCP_ACCESS_TOKEN>` | 支持 MCP 的 Agent 或工作区。 |

`GET <BASE_URL>/health` 不鉴权。`/console` 和 `/console-api/*` 是内部控制台实现，不是对外交付接口。

## 2. HTTP API

所有请求和响应使用 UTF-8 JSON。除健康检查外，业务接口都要求 `X-API-Key`。`<BASE_URL>` 和 API Token 由服务方通过安全渠道提供。

### 2.1 接口清单

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/health` | 存活检查。 |
| `POST` | `/api/patent/search` | 按 `q` 检索。 |
| `GET` | `/api/patent/detail/{patent_id}` | 查询单件专利详情。 |
| `GET` | `/api/patent/citations/{patent_id}` | 查询引用、被引和原始引证数据。 |
| `GET` | `/api/patent/legal-history/{patent_id}` | 查询法律状态历史。 |

### 2.2 检索

`POST /api/patent/search`

| 字段 | 类型 | 必填 | 默认值 | 约束/说明 |
|---|---|---:|---|---|
| `q` | string | 是 | — | 检索式，1–1000 个字符。 |
| `ds` | string | 否 | `cn` | `all` 或任意两位国家/地区码；非 `all` 时按 `Country` 大写值过滤。 |
| `sort` | string | 否 | `relation` | `relation`、`rank`、`relevance`、`score`（均为相关性降序）；`applicationDate`、`!applicationDate`、`documentDate`、`!documentDate`（前者升序，`!` 为降序）。 |
| `page` | integer | 否 | `1` | 从 1 开始。 |
| `page_size` | integer | 否 | `50` | 1–100。 |
| `highlight` | integer | 否 | `0` | 仅接受 `0` 或 `1`；目前不返回高亮片段。 |

```bash
curl -sS -X POST "$BASE_URL/api/patent/search" \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"ipc:H02M AND ab:\"口腔数字印模仪器\"","ds":"cn","page":1,"page_size":10}'
```

成功时直接返回结果对象：

| 字段 | 说明 |
|---|---|
| `total`、`page`、`page_size`、`total_pages`、`next_page`、`took_ms` | 分页与耗时信息；无下一页时 `next_page` 为 `null`。 |
| `records` | 专利记录数组。每项至少含 `id`/`patent_id`、`title`、`abstract`、`applicant`、`applicationNumber`、`documentNumber`、`applicationDate`、`documentDate`、`mainIpc`、`legalStatus`、`type`、`score`。 |
| 兼容字段 | `ti`=`title`，`ab`/`summary`=`abstract`，`pa`=`applicant`；同时返回 `application_number`、`document_number`、`application_date`、`document_date`、`legal_status`、`main_ipc`、`main_claim`、`independent_claims`。 |

`mainClaim`/`main_claim` 是主权利要求；`independentClaims`/`independent_claims` 为独立权利要求。源数据缺失时，字符串字段返回空字符串、列表字段返回空数组；不要用空值推断不存在的业务事实。

### 2.3 查询语法

检索式只接受英文双引号 `"`；HTTP 调用中 `“”` 不是短语符号。短语会按字段分词结果进行连续匹配，通常比普通全文检索窄。

| 写法 | 含义 | 示例 |
|---|---|---|
| 裸词 | 在标题和摘要全文检索；形如 IPC 的裸词按 IPC 检索。 | `阀门`、`H02M` |
| 字段词 | `field:value` 或 `field:(表达式)`。 | `title:阀门`、`ab:(数字 OR 印模)` |
| 完整短语 | 字段后直接使用英文双引号。 | `ab:"口腔数字印模仪器"` |
| 布尔/分组 | 支持 `AND`、`OR`、前缀 `NOT` 与括号。 | `(title:阀门 OR ab:缓冲) AND NOT type:外观设计` |
| 范围 | 仅支持方括号闭区间。 | `ad:[2020-01-01 TO 2020-12-31]` |

文本、`type`、`ipc`、`mainIpc` 和标识符字段可在字段值内使用 `AND`、`OR`、`NOT` 和括号；`legalStatus` 使用单个状态值。

| 类别 | 字段 | 含义 |
|---|---|---|
| 文本 | `title`、`ab`、`tscd` | 标题；摘要；标题、摘要、主权利要求、权利要求书、说明书综合检索。 |
| 文本 | `mainClaim`、`claims`、`description` | 主权利要求；权利要求书；说明书。 |
| 文本 | `applicant`、`currentAssignee`、`agency`、`agent` | 申请人、当前权利人、代理机构、代理人。 |
| 枚举 | `type`、`legalStatus` | 专利类型；法律状态。`有效专利`、`在审`、`失效`分别按内置状态集合匹配，其余值精确匹配状态字段。 |
| 分类 | `ipc`、`mainIpc` | `ipc` 覆盖主 IPC 与 IPC 相关分类字段；`mainIpc` 只匹配主 IPC。 |
| 标识符 | `applicationNumber`、`documentNumber`、`publicationNumber`、`patentId` | 申请号；公开号/公告号（后两者是同一组字段）；内部专利 ID。 |
| 范围 | `ad`、`documentYear` | 申请日 `YYYY-MM-DD`；公开年 `YYYY`。 |

不支持通配符、模糊/邻近/boost 语法，也没有隐式 `AND`。不支持字段、空字段值、未闭合引号/括号、非法日期或逆序范围均返回 `40001`。

### 2.4 详情、引证与法律状态

所有 `patent_id` 均应来自检索结果的 `id` 或 `patent_id`，并按 URL 编码传入路径。

| 接口 | 参数 | 成功返回 |
|---|---|---|
| `GET /api/patent/detail/{patent_id}` | `include_description`：可选布尔值，默认 `false` | 基本信息、申请人与发明人、分类、权利要求、家族、附图、法律状态、PDF 等；传 `true` 时额外有 `description`。常用 camelCase 字段同时带 snake_case 兼容别名。 |
| `GET /api/patent/citations/{patent_id}` | 无 | `patent_id`、`cited_by`、`patent_references`、`non_patent_references`，以及原始兼容字段 `referencesCited`、`referencesCitedRaw`、`referencesCitedText`、`relatedDocuments`。归一化的引用专利项包含 `id`、`title`、`applicant`、`application_date`、`application_number`、`type`、`legal_status`、`main_ipc`。 |
| `GET /api/patent/legal-history/{patent_id}` | 无 | `patent_id`、`transaction_count`、`transactions`。`transactions` 保留上游法律状态历史条目结构。 |

详情中，除上述检索记录字段外，还可能包含 `firstApplicant`、`currentAssignee`、`assignee`、`inventor`、`firstInventor`、`applicantAddress`、`agency`、`agent`、`ipcMainList`、`loc`、优先权/PCT 字段、`imagePath`、`pdfList`、`family`、`drawings`、`legalStatusHistory`、`mainClaim` 与 `claims`；其 snake_case 兼容别名与对应 camelCase 值相同。

```bash
curl -sS "$BASE_URL/api/patent/detail/$PATENT_ID?include_description=true" \
  -H "X-API-Key: $API_TOKEN"
```

### 2.5 错误

错误统一是无外层 `detail` 的扁平 JSON：

```json
{"success":false,"code":40001,"message":"q 查询语法错误：...","data":null}
```

| code | HTTP | 含义 |
|---:|---:|---|
| `40001` | 400 | 检索语法错误。 |
| `40002` | 400 | 普通参数或专利 ID 非法。 |
| `40003` | 400 | `page` 或 `page_size` 非法。 |
| `40101` | 401 | 缺少或错误的 `X-API-Key`。 |
| `40401` | 404 | 专利不存在。 |
| `50001` | 502 | OpenSearch 查询失败。 |
| `50002` | 500 | 服务内部异常。 |

## 3. MCP

MCP 是 HTTP API 的工具层，不直接访问 OpenSearch。它使用另一份 Bearer Token；HTTP API 的 `X-API-Key` 仅由 MCP 服务在内部调用 FastAPI 时使用。两种 Token 可独立轮换。

### 3.1 远程 HTTP

端点：`<MCP_URL>/mcp`。使用支持 Streamable HTTP 的 MCP 客户端，并设置：

```json
{
  "mcpServers": {
    "patent-search": {
      "type": "http",
      "url": "https://<host>/mcp",
      "headers": {"Authorization": "Bearer <MCP_ACCESS_TOKEN>"}
    }
  }
}
```

### 3.2 stdio

部署在本机或同一受控环境时，可由 MCP 客户端启动 `python mcp_server/server.py`。该进程需能访问 `PATENT_SEARCH_BASE_URL`，并通过 `PATENT_SEARCH_API_TOKEN` 调用 HTTP API；不要将这些变量写入客户端配置仓库。

### 3.3 工具

| 工具 | 参数 | 返回 |
|---|---|---|
| `patent_search` | `q`；可选 `ds`、`page`、`page_size`、`sort`、`highlight`（boolean） | 与 HTTP 搜索相同的分页元数据，但列表字段为 `patents` 而非 `records`。每项是精简 snake_case 记录。实际每页上限由 MCP 配置控制，默认 50。 |
| `patent_get_detail` | `patent_id`；可选 `include_description` | 与 HTTP 详情相同。 |
| `patent_get_citations` | `patent_id` | 与 HTTP 引证相同。 |
| `patent_get_legal_history` | `patent_id` | 与 HTTP 法律状态历史相同。 |

MCP 工具失败时返回 `{"error":"...","code":<错误码>}`，而非 HTTP 的 `success/data` 错误信封。先调用 `tools/list` 获取最终参数 schema；客户端应把 `patent_search` 返回的 `patents[*].id` 传给其余三个工具。

## 4. 验证与变更

| 场景 | 验证方式 |
|---|---|
| HTTP 存活 | `GET /health` 返回 `data.status="healthy"`。 |
| HTTP 契约 | 运行 `python3 scripts/smoke_health.py <BASE_URL>`，并用有效 Token 发起检索、详情、引证、法律状态请求。 |
| MCP stdio | `python3 scripts/smoke_mcp_server.py <BASE_URL> "$API_TOKEN"`。 |
| MCP HTTP | `python3 scripts/smoke_mcp_http.py <MCP_URL>/mcp "$MCP_ACCESS_TOKEN"`。 |

新增或修改 HTTP/MCP 参数、返回字段、鉴权或查询语义时，必须同时更新本文、OpenAPI/工具 schema 测试与版本记录。索引切换不改变调用地址；若它改变可观察的查询语义或结果契约，应按服务发布规则升级版本并说明影响。
