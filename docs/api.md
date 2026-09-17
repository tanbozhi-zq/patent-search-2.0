# 专利检索 HTTP API 与 MCP 对接

本文是对外调用契约。运行中的 OpenAPI 规格见 `<BASE_URL>/docs` 或 `<BASE_URL>/redoc`；接口实现变更时须同步更新本文。不要把 Token、OpenSearch 凭据或服务器私钥写入此文档。

## 1. 接入方式

| 方式 | 地址 | 鉴权 | 适用场景 |
|---|---|---|---|
| HTTP API | `<BASE_URL>/api/patent/*` | `X-API-Key: <API_TOKEN>` | 应用、后端服务或批处理程序直接调用。 |
| 内部控制台 | `<INTERNAL_BASE_URL>/console/` | 独立 HTTP Basic 凭据 | 可信网络中的浏览器用户。 |
| Streamable HTTP MCP | `<MCP_URL>/mcp` | `Authorization: Bearer <MCP_ACCESS_TOKEN>` | 支持 MCP 的 Agent 或工作区。 |

`GET <BASE_URL>/live`、`/startup`、`/ready` 与兼容入口 `/health` 不鉴权，但必须只经可信入口/网络访问。`/console` 和 `/console-api/*` 是内部控制台实现，不是对外交付接口；浏览器使用独立的 HTTP Basic 凭据，程序调用仍可使用 `X-API-Key`。正式 `/api/patent/*` 不接受 Console Basic 凭据。

### 地址与部署状态

HTTP API、MCP 和内部浏览器入口由部署方分别提供，以下示例使用占位符，不把服务器 IP 固化为对外接入承诺。部署前请完成[部署核对清单](ops/deployment_checklist.md)。Console 已在应用中提供；可信网络/TLS 入口是否可交付需另行验证，不能以“页面返回 401”代替网络验收。

HTTP API Token、Console 用户凭据和 MCP Token 由服务方分别交付；本文仅说明其使用位置，不记录凭据值。

## 2. HTTP API

所有请求和响应使用 UTF-8 JSON。除探针外，业务接口都要求 `X-API-Key`。`<BASE_URL>` 和 API Token 由服务方通过安全渠道提供。

所有 HTTP 成功和错误响应都返回 `X-Request-ID`。调用方可传入 1–64 字符的
`X-Request-ID`，首字符必须是 ASCII 字母或数字，其余字符只允许 ASCII 字母、
数字、`.`、`_` 和 `-`；缺失、重复、超长或含空白、Unicode、换行/控制字符的值
会被替换为服务生成的随机 ID。该值只用于请求关联，不参与认证、限流或业务逻辑。
统一错误响应体中的 `request_id` 与响应头一致；正常成功响应体结构不增加该字段。

### 2.1 接口清单

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/live` | 仅检查应用进程仍可响应；不访问 OpenSearch。 |
| `GET` | `/startup` | 检查配置、查询预算、舱壁与 OpenSearch Client 已完成进程级初始化。 |
| `GET` | `/ready` | 检查实例当前可接收检索流量。 |
| `GET` | `/health` | 过渡期 liveness 兼容入口。 |
| `POST` | `/api/patent/search` | 统一执行布尔、向量或混合检索。 |
| `GET` | `/api/patent/detail/{patent_id}` | 查询单件专利详情。 |
| `GET` | `/api/patent/citations/{patent_id}` | 查询引用、被引和原始引证数据。 |
| `GET` | `/api/patent/legal-history/{patent_id}` | 查询法律状态历史。 |

`/live` 成功时返回 `{"status":"live"}`，不访问 OpenSearch。`/startup` 在初始化
完成后返回 `{"status":"started"}`；启动中、失败或关闭过程中返回 `503` 和
`{"status":"not_started"}`。`/ready` 只在启动完成且 OpenSearch 读目标的廉价
`HEAD/exists` 检查成功时返回 `{"status":"ready"}`，否则返回 `503` 和
`{"status":"not_ready"}`。它不执行检索、计数或集群健康 API，不经过查询预算或
业务舱壁；每个进程使用独立的一条 OpenSearch 连接和一个工作线程，短时缓存并合并
并发检查。所有探针响应都不包含节点、索引、配置、凭据或原始异常。

### 2.2 检索

`POST /api/patent/search`

| 字段 | 类型 | 默认值 | 约束/说明 |
|---|---|---|---|
| `mode` | string | `boolean` | `boolean`、`vector` 或 `hybrid`。省略时保持旧布尔语义。 |
| `q` | string | — | 1–1000 个字符；`boolean/hybrid` 必填，`vector` 禁止。 |
| `semantic_text` | string | — | 原始输入 1–1000 个字符且不能全为空白，随后去除首尾空白；`vector/hybrid` 必填，`boolean` 禁止。 |
| `vector_fields` | string[] | — | 只接受注册的业务名；不得重复。`vector` 1–5 个，`hybrid` 1–4 个；`boolean` 禁止。 |
| `top_k` | integer | `100` | 仅 `vector/hybrid`，1–1000；限定稳定排名和可访问的结果窗口，不是单页数量。 |
| `ds` | string | `cn` | `all` 或任意两位公开国别码；非 `all` 时按 `PublicationCountry` 大写值全局过滤。 |
| `sort` | string | `relation` | `relation/rank/relevance/score` 是相关性降序；`applicationDate`、`!applicationDate`、`documentDate`、`!documentDate` 分别是两个日期字段的升/降序。 |
| `page` | integer | `1` | 从 1 开始。 |
| `page_size` | integer | `50` | 1–100。 |
| `highlight` | integer | `0` | 仅接受 `0` 或 `1`；目前不返回高亮片段。 |

模式组合是严格合同：无关字段不会被静默忽略。例如 `vector`
不接受 `q`，`boolean` 不接受 `semantic_text/vector_fields/top_k`。
未注册字段、重复字段和模式字段组合错误在调用向量服务或 OpenSearch
前返回 `40002`。当前可选业务名为 `abstract`、`main_claim` 和
`independent_claims`；详细注册表、数据覆盖和排名事实见
[vector_search.md](vector_search.md)。

请求在解析和调用 OpenSearch 前受统一查询预算约束。默认值与代码硬上限均为：请求体 16 KiB、`q` 1000 个字符、括号/递归嵌套 32 层、词法 Token 256 个、AST 节点 256 个、布尔节点 128 个、`page_size` 100、`from + size` 10000。Token 计数不包含内部 EOF 标记；布尔节点包括 `AND`、`OR` 和 `NOT`。请求体按收到的字节数计算，即使请求没有 `Content-Length` 也会在流式读取时检查。

部署可通过 `QUERY_MAX_REQUEST_BODY_BYTES`、`QUERY_MAX_CHARS`、`QUERY_MAX_NESTING_DEPTH`、`QUERY_MAX_TOKENS`、`QUERY_MAX_AST_NODES`、`QUERY_MAX_BOOLEAN_CLAUSES`、`QUERY_MAX_PAGE_SIZE` 和 `QUERY_MAX_RESULT_WINDOW` 收紧生效值。环境变量在进程启动时读取，不能超过代码硬上限；非法配置会使服务拒绝启动。修改环境变量后必须通过正式发布流程重启服务，不能视为已动态生效。

```bash
curl -sS -X POST "$BASE_URL/api/patent/search" \
  -H 'Content-Type: application/json' \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"ipc:H02M AND ab:\"口腔数字印模仪器\"","ds":"cn","page":1,"page_size":10}'
```

单字段向量请求：

```json
{
  "mode": "vector",
  "semantic_text": "提高逆变器在轻载工况下的转换效率",
  "vector_fields": ["abstract"],
  "top_k": 100,
  "ds": "cn",
  "sort": "relation",
  "page": 1,
  "page_size": 20
}
```

多字段混合请求：

```json
{
  "mode": "hybrid",
  "q": "ipc:H02M AND applicant:华为",
  "semantic_text": "提高逆变器在轻载工况下的转换效率",
  "vector_fields": ["abstract", "main_claim"],
  "top_k": 100,
  "ds": "cn",
  "sort": "!applicationDate",
  "page": 1,
  "page_size": 20
}
```

成功时直接返回结果对象：

| 字段 | 说明 |
|---|---|
| `total`、`page`、`page_size`、`took_ms` | 命中/可访问窗口数、当前页、每页数量与 OpenSearch 返回的耗时。 |
| `total_pages` | 按总命中数计算的逻辑总页数，可能大于结果窗口实际允许访问的页数。 |
| `accessible_pages` | 当前生效结果窗口内可访问的最大页数；客户端应以此字段和 `next_page` 控制导航。 |
| `next_page` | 下一合法页码；已到逻辑末页或结果窗口边界时为 `null`。 |
| `records` | 专利记录数组；每项固定使用下列 snake_case 字段。 |
| `search_context` | 仅 `vector/hybrid` 返回 `mode/vector_fields/top_k/ranking_profile/sort`；不包含查询向量、候选放大量或供应商信息。 |

例如总命中 154812、`page_size=10` 时，`total_pages=15482` 仍表达完整命中规模；默认 10000 结果窗口下 `accessible_pages=1000`，第 1000 页的 `next_page=null`。第 1001 页请求继续返回 `40003`，且不会进入 Repository。结果窗口不能整除 `page_size` 时，可访问页数按向下取整计算。

每一条 `records` 的完整键集为：`id`、`application_number`、`publication_number`、`title`、`abstract`、`applicant`、`current_assignee`、`inventor`、`main_ipc`、`ipc_list`、`main_claim`、`application_date`、`publication_date`、`legal_status`、`type`、`score`。

`boolean` 的 `total` 保持完整命中数语义；`vector/hybrid` 的 `total`
是本次可访问的融合结果窗口，最大不超过 `top_k`。语义模式还必须满足
`from + size <= top_k`；如果 `top_k` 不能被 `page_size` 整除，不开放会跨出
窗口的尾页。日期排序时 `records[].score` 允许为 `null`，这是缺少相关性
分数的明确语义，不应转成 `0`。

`main_claim` 按 `MainClaim`、`MainClaimCN`、`MainClaimEN` 选择；搜索列表不返回独立权利要求或完整权利要求书。`publication_number` 对应 OpenSearch `PublicationNumber`。`main_ipc` 与 `ipc_list` 均为大写、无显示空格、无版本尾标的去重 IPC 值；`IPCListBase` 仅用于层级检索，不作为响应字段返回。`type` 只返回有明确映射的业务专利类型。文本型字段遇到上游数组时以 `;` 合并；`current_assignee` 只来自 `Assignee`，缺失时返回空字符串。

### 2.3 查询语法

检索式只接受英文双引号 `"`；HTTP 调用中 `“”` 不是短语符号。短语会按字段分词结果和词序进行匹配，通常比普通全文检索窄。为兼容 IK 对中文复合词的重叠分词，仅 `tscd` 中包含汉字的引号短语使用 `slop: 1` 的词位容差；该策略由服务端内部应用，调用方无需传参。其他引号短语仍保持严格连续匹配。

| 写法 | 含义 | 示例 |
|---|---|---|
| 裸词 | 在标题和摘要全文检索；形如 IPC 的裸词按 IPC 检索。 | `阀门`、`H02M` |
| 字段词 | `field:value` 或 `field:(表达式)`。 | `title:阀门`、`independentClaims:(电路 OR circuit)` |
| 短语检索 | 字段后直接使用英文双引号。 | `ab:"口腔数字印模仪器"` |
| 布尔/分组 | 支持 `AND`、`OR`、前缀 `NOT` 与括号。 | `(title:阀门 OR ab:缓冲) AND NOT type:外观设计` |
| 范围 | 仅支持方括号闭区间。 | `ad:[2020-01-01 TO 2020-12-31]` |

文本、`type`、`ipc`、`mainIpc` 和标识符字段可在字段值内使用 `AND`、`OR`、`NOT` 和括号；`legalStatus` 使用单个状态值。

| 类别 | 字段 | 含义 |
|---|---|---|
| 文本 | `title`、`ab`、`tscd` | 标题；摘要；标题、摘要、首权、完整权要、说明书、独权、从权综合检索。 |
| 文本 | `mainClaim`、`claims`、`description`、`independentClaims`、`dependentClaims` | 首权；完整权要；说明书；独权；从权。`claims` 是公开查询名，物理字段为 `Requirement`。 |
| 文本 | `applicant`、`currentAssignee`、`inventor`、`agency`、`agent` | 申请人、当前权利人、发明人（`Inventor`）、代理机构、代理人。 |
| 枚举 | `type`、`legalStatus` | 专利类型；法律状态。`有效专利`、`在审`、`失效`分别按内置状态集合匹配，其余值精确匹配状态字段。 |
| 分类 | `ipc`、`mainIpc` | `ipc` 匹配任一主/副 IPC 的标准化层级；`mainIpc` 只匹配主 IPC 的对应层级。 |
| 标识符 | `applicationNumber`、`documentNumber`、`publicationNumber`、`patentId` | 申请号；公开号/公告号（后两者是同一组字段）；内部专利 ID。 |
| 范围 | `ad`、`documentYear` | 申请日 `YYYY-MM-DD`；公开年 `YYYY`。 |

`title`、`ab`、`tscd`、`mainClaim`、`claims`、`description`、`independentClaims`、`dependentClaims` 按每个 AST 文本叶子路由：含任意 Han 字符或去除首尾空格后为纯数字时走 CN 字段，其他文本走 EN 字段。最终查询不回退至通用字段或 `*Original` 字段。中文 `tscd` 查询 `TitleCN`、`AbstractCN`、`MainClaimCN`、`RequirementCN`、`InstructionsCN`、`IndependentClaimsCN`、`DependentClaimsCN`；英文查询同名 EN 字段。无字段普通裸词只查同语言标题和摘要；能被识别为完整 IPC 层级的裸词（如 `H02M`）仍走 IPC 查询，无斜杠组族（如 `H02M1`）须显式使用 `ipc:`。

IPC 支持 `A`、`A01`、`A01B`、`A01B1/00`、`A01B1/02` 五级格式；仅显式 `ipc:`/`mainIpc:` 还支持无斜杠组族 `A01B1`。自动统一大小写；显示空格与末尾版本标记可放在英文双引号内。`ipc:A01B1` 以 `IPCListBase=A01B1` 查询整个组族；带斜杠的 `ipc:A01B1/00`、`ipc:A01B1/02` 均以完整值精确查询。`mainIpc:A01B1` 查询 `IPCLargeGroup=A01B1/00`；所有带斜杠的 `mainIpc:`（包括 `/00`）均以 `IPCSmallGroup` 精确查询。裸词 `A01B1` 仍按 Title/Abstract 全文检索。上述精确语义依赖 serving 索引完成 v2 `IPCListBase` 重算、全量只读复扫和固定样本验收。仅在显式 `ipc:`/`mainIpc:` 查询中，`A2`、`A01B1/0` 等非法格式返回 `40001`。

不支持通配符、模糊/邻近/boost 语法，也没有隐式 `AND`。不支持字段、空字段值、未闭合引号/括号、显式 `ipc`/`mainIpc` 中的非法 IPC、非法日期或逆序范围均返回 `40001`。

### 2.4 详情、引证与法律状态

所有详情请求的 `patent_id` 均应来自检索结果的 `id`，并按 URL 编码传入路径。

| 接口 | 参数 | 成功返回 |
|---|---|---|
| `GET /api/patent/detail/{patent_id}` | `include_description`：可选布尔值，默认 `false` | 详情业务字段；传 `true` 且说明书存在时额外有 `description`。 |
| `GET /api/patent/citations/{patent_id}` | 无 | `patent_id`、`cited_by`、`patent_references`、`non_patent_references`，以及原始兼容字段 `referencesCited`、`referencesCitedRaw`、`referencesCitedText`、`relatedDocuments`。归一化的引用专利项包含 `id`、`title`、`applicant`、`application_date`、`application_number`、`type`、`legal_status`、`main_ipc`。 |
| `GET /api/patent/legal-history/{patent_id}` | 无 | `patent_id`、`transaction_count`、`transactions`。`transactions` 保留上游法律状态历史条目结构。 |

详情返回 `id`，以及存在的业务字段：`application_number`、`publication_number`、`title`、`abstract`、`applicant`、`first_applicant`、`current_assignee`、`inventor`、`first_inventor`、`applicant_address`、`agency`、`agent`、`main_ipc`、`ipc_list`、`main_claim`、`independent_claims`、`claims`、`application_date`、`publication_date`、`legal_status`、`type`、`priority_numbers`、`pct_application_date`、`pct_application_number`、`pct_publication_number`、`image_path`、`images`、`family`，以及按请求返回的 `description`。

没有可靠值的可选字段不返回。文本型字段遇到上游数组时以 `;` 合并；`current_assignee` 只来自 `Assignee`，缺失时不返回。`priority_numbers` 是去重后的申请号数组。法律状态历史仅由 `legal-history` 接口返回。

`images` 仅包含规范化、去重后的 TOS 图片路径，优先以 `PatentImage` 开头并补全 `PatentImages`；不包含 HTTP(S) URL。`image_path` 优先使用该 TOS 主图路径；没有 TOS 图片时，才使用 `AbstractFigureUrl` 作为主图 URL。`drawings`、`DescriptionImages`、`pdf_list` 和 `loc` 不在详情契约中。

```bash
curl -sS "$BASE_URL/api/patent/detail/$PATENT_ID?include_description=true" \
  -H "X-API-Key: $API_TOKEN"
```

### 2.5 错误

错误统一是无外层 `detail` 的扁平 JSON。`message` 仅供人阅读，调用方只能以 `code`、HTTP 状态和 `retryable` 作机器判断；它不会包含内部异常、主机、索引、凭据或堆栈。

```json
{
  "success": false,
  "code": 50401,
  "message": "搜索依赖超时",
  "data": null,
  "request_id": "opaque-id",
  "retryable": true
}
```

每个 HTTP 响应都会有 `X-Request-ID` 响应头；错误响应中它与 `request_id` 相同，可用于定位服务日志。`42901`、`50301`、`50302` 还会返回 `Retry-After` 秒数，调用方在该时间前不应重试。

| code | HTTP | retryable | Retry-After | 含义 |
|---:|---:|:---:|---:|---|
| `40001` | 400 | false | — | 查询语法错误。 |
| `40002` | 400 | false | — | 请求字段或类型无效。 |
| `40003` | 400 | false | — | 分页参数或结果窗口越界。 |
| `40004` | 400 | false | — | 查询复杂度超限。 |
| `40101` | 401 | false | — | 未认证或凭据无效。 |
| `40400` | 404 | false | — | 路由不存在。 |
| `40401` | 404 | false | — | 专利不存在。 |
| `40500` | 405 | false | — | HTTP 方法不允许。 |
| `40901` | 409 | false | — | 管理参数草案的配置基线已变化。 |
| `41301` | 413 | false | — | 请求体过大。 |
| `42901` | 429 | true | 60 | 调用方触发限流。 |
| `50001` | 502 | false | — | 下游拒绝请求、响应无效或服务配置错误。 |
| `50002` | 500 | false | — | 未预期的程序内部错误。 |
| `50301` | 503 | true | 1 | 本服务并发舱壁或队列已满。 |
| `50302` | 503 | true | 5 | 搜索依赖连接失败或暂时不可用。 |
| `50401` | 504 | true | — | 搜索依赖超时。 |

`50301` 已接入 HTTP/Console 的应用级并发舱壁。所有 OpenSearch 业务请求先受每进程全局容量约束；检索和目标排名还受一个严格低于全局容量的重请求子上限约束，因此重搜索打满时仍为详情、引证和法律状态保留全局槽位。重请求先获取子许可再获取全局许可，未拿到子许可时不会占用预留容量。容量耗尽时请求不会进入 Repository。一个请求内部的目标排序、详情标识符回退等多阶段查询只各占用一次对应许可；`/health` 不进入舱壁。Console 的同步服务调用会在线程池中执行，因此已准入的慢 Console 查询也不会占住事件循环并阻塞健康检查。全局容量、重请求容量和获取等待都是必填部署配置，缺少任一项时服务拒绝启动，代码不会静默采用未经批准的 WIP 值。容量应在本环境测量，扩容需完成容量与过载拒绝验收。

`40004`、`41301` 已接入 HTTP API 和 Console API 的共同查询入口。超过查询字符、嵌套、Token、AST 或布尔节点预算返回 `40004`；超过分页大小或结果窗口返回 `40003`；超过请求体字节预算返回 `41301`。这些请求都不会进入 Repository。`50302` 与 `50401` 已接入实际 OpenSearch 失败路径。每个请求共享一份生效的总预算（默认及硬上限 240 秒，可配置收紧）；后续 OpenSearch 调用只使用剩余时间，预算耗尽后不会再发起查询。瞬时、幂等的读取最多显式重试一次，语法、鉴权、配置、序列化和响应格式错误不重试。`42901` 的实际触发路径仍由后续按调用方限流版本实现。请求字段验证统一返回 `40002`，分页字段验证返回 `40003`，不会返回 FastAPI 默认 `422`。

兼容性：正常成功响应保持不变。`0.8.0` 开始，连接失败、瞬时不可用和超时会分别稳定返回 `50302` 或 `50401`；下游应按 `retryable` 与 `Retry-After` 决定是否重试。HTTP 调用方的自身超时应略大于 240 秒，才能收到服务生成的 `50401`。

### 2.6 内部网页控制台

通过可信内部地址 `<INTERNAL_BASE_URL>/console/` 访问。浏览器收到认证挑战后输入运维单独交付的 Console 用户名和密码；认证成功后，同源 `/console-api/*` 请求会自动沿用浏览器管理的 Basic 凭据。页面可直接完成检索、分页、详情、引证和法律状态查看。

检索模式与 HTTP 合同一致：

- “布尔”显示 `q` 和原有高级构建器；
- “向量”显示 `semantic_text`、向量字段多选和 `top_k`，隐藏 `q` 构建器；
- “混合”同时显示 `q` 与语义控件。

`ds`、`sort`、`page`、`page_size` 和 `highlight` 继续复用原控件。
向量字段选项在服务端生成 Console HTML 时从共享注册表投影公开名，
没有 capabilities API，也不会向页面暴露 OpenSearch 字段、维度或模型名。
目标排名仍只支持布尔模式；语义模式下会明确拒绝，不会忽略语义条件。
日期排序结果的 `score=null` 显示为空，真实数值 `0` 仍显示为 0。

文本字段的构建器提供“普通全文”“短语检索”“OR 短语”三种模式。短语检索会生成如 `ab:"口腔数字印模仪器"` 的检索式；页面会把输入的中文引号 `“”` 规范为英文双引号。直接通过 HTTP 调用时仍必须自行使用英文双引号。`tscd` 中包含汉字的短语会由服务端内部使用 `slop: 1` 的词位容差，控制台不暴露该内部参数。

控制台 HTML 和全部 `/console-api/*` 路由接受独立 HTTP Basic 凭据，且为程序兼容保留 `X-API-Key`。缺失或无效凭据统一返回 HTTP 401 / `40101`，并携带 Basic 认证挑战。Console 用户名和密码只允许可打印 ASCII，用户名不能包含冒号，且 `CONSOLE_PASSWORD` 必须与 `API_TOKEN` 不同；后端 API Token 不会进入 URL、HTML、页面脚本、浏览器存储或 Console 日志。浏览器凭据由浏览器的 HTTP 认证机制管理，前端代码不读取或保存凭据。检索式、结果和详情仅保存在当前页面内存中；初始化会删除旧版本遗留的 `patentConsole:lastSearch`。TLS 与可信代理边界通过验收前，不得在非可信网络发布 Console，因为 Basic 凭据必须由 TLS 保护。

所有专利字段、错误消息和原始 JSON 均按纯文本渲染；页面不把后端数据写入 HTML 字符串或内联事件处理器。返回结构仍与对应正式 HTTP API 一致，第三方系统继续调用 `/api/patent/*`。

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

部署在本机或同一受控环境时，使用[stdio 配置模板](../mcp_server/examples/stdio_config.example.json)，将 Python 和脚本路径替换为安装目录的绝对路径。不要依赖客户端工作目录或系统 Python 已安装依赖。

MCP 进程只读取进程环境，**不会自动加载仓库 `.env`**。由客户端的秘密管理或父进程环境安全注入 `PATENT_SEARCH_API_TOKEN`；GUI 启动器未必继承终端环境，需在客户端确认。模板故意不提供 Token 字段，也不假设 JSON 会自动展开 `${VAR}`。服务地址和超时等非秘密值可使用模板中的 `env`。

### 3.3 工具

| 工具 | 参数 | 返回 |
|---|---|---|
| `patent_search` | `q`；可选 `ds`、`page`、`page_size`、`sort`、`highlight` | 固定组装 `mode=boolean`。 |
| `patent_vector_search` | `semantic_text`、`vector_fields`；可选 `top_k`、`ds`、`page`、`page_size`、`sort`、`highlight` | 固定组装 `mode=vector`。 |
| `patent_hybrid_search` | `q`、`semantic_text`、`vector_fields`；可选 `top_k`、`ds`、`page`、`page_size`、`sort`、`highlight` | 固定组装 `mode=hybrid`。 |
| `patent_get_detail` | `patent_id`；可选 `include_description` | 与 HTTP 详情相同。 |
| `patent_get_citations` | `patent_id` | 与 HTTP 引证相同。 |
| `patent_get_legal_history` | `patent_id` | 与 HTTP 法律状态历史相同。 |

三个检索工具都是薄适配：它们调用同一个
`POST /api/patent/search`，不直连 OpenSearch，不复制 parser、DSL、排序、分页
或字段注册逻辑。成功时都返回 `total/page/page_size/total_pages/`
`accessible_pages/next_page/took_ms/patents`；语义工具额外保留 HTTP
`search_context`。`patents` 每项使用 HTTP `records` 相同的 16 个
snake_case 字段。三个 MCP 检索工具的 `page_size` 默认是 **10**（HTTP API 默认 50），适配器按 `PATENT_SEARCH_PAGE_SIZE_LIMIT` 裁剪，默认上限 50；`highlight` 在 MCP 中是布尔值，默认 `false`，转发时转换成 HTTP 的 0/1。

MCP 工具失败时返回 `{"error":"...","code":<错误码>,"message":"...","request_id":"...","retryable":false}`，而非 HTTP 的 `success/data` 错误信封，并设置 MCP `isError=true`。有效的 HTTP 业务错误保留原 `code`、`message`、`request_id` 和 `retryable`；MCP 到 HTTP 的连接失败、超时、无效响应和内部异常分别稳定映射为 `50302`、`50401`、`50001` 和 `50002`。成功结果设置 `isError=false`；自托管检索路径透传 HTTP 的 `accessible_pages`，调用方应按 `next_page` 导航而不是遍历到 `total_pages`。MCP 不转发后端响应头、响应原文或内部异常。

MCP 到 FastAPI 的默认等待时间是 245 秒，略大于 FastAPI 的 240 秒请求总预算，以便优先收到后端生成并带有原 `request_id` 的 `50401`，而不是由 MCP 提前中断。部署配置应填写至少 245 的整数；可解析的更小整数会令 MCP 拒绝启动。当前环境读取器对缺失或非整数值回退到 245，发布前仍应验证原始配置，不能依赖拼写错误被拒绝。

六个工具入口使用异步薄包装，把现有同步 HTTP client 调用放入 worker thread，因此一个慢请求不会阻塞 MCP 事件循环。`MCP_MAX_CONCURRENT_TOOLS` 控制单个 MCP 进程允许的 worker 调用数，默认 4；缺失或非整数环境值回退到 4，可解析的非正整数拒绝启动。满载时不排队，立即返回带独立 `request_id` 的可重试 `50301`。该值应不高于其访问的单个 FastAPI 实例的全局业务舱壁容量；FastAPI 的应用舱壁仍是最终保护边界。此设置只改善 MCP 同时处理多个工具调用的能力，不会缩短 OpenSearch 单次查询本身的耗时。

先调用 `tools/list` 获取最终参数 schema；客户端应把 `patent_search` 返回的 `patents[*].id` 传给其余三个工具。

## 4. 验证与变更

| 场景 | 验证方式 |
|---|---|
| HTTP 探针 | `/live`、`/startup`、`/ready` 均返回对应的最小 `status`；OpenSearch 不可用时仅 `/ready` 返回 503。 |
| HTTP 兼容存活 | `GET /health` 仍返回 `data.status="healthy"`。 |
| HTTP 契约 | 运行 `python3 scripts/smoke_health.py <BASE_URL>`，并用有效 Token 发起检索、详情、引证、法律状态请求。 |
| MCP stdio | `python3 scripts/smoke_mcp_server.py <BASE_URL> "$API_TOKEN"`。 |
| MCP HTTP | `python3 scripts/smoke_mcp_http.py <MCP_URL>/mcp "$MCP_ACCESS_TOKEN"`。 |

新增或修改 HTTP/MCP 参数、返回字段、鉴权或查询语义时，必须同时更新本文、OpenAPI/工具 schema 测试与版本记录。索引切换不改变调用地址；若它改变可观察的查询语义或结果契约，应按服务发布规则升级版本并说明影响。
