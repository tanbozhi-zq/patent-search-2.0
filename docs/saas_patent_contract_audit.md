# SaaS Patent Contract Audit

## 1. Scope

审计对象为本地只读 SaaS 副本：

```text
patent_harness_base_副本/
```

该目录仅作为接口契约证据源，不纳入本项目源码交付。

## 2. Key Findings

### 2.1 SaaS entrypoint

SaaS 后端提供专利分析会话入口：

```text
POST /api/PatentSearch/sessions
```

证据文件：

```text
patent_harness_base_副本/backend/app/gateway/routers/patent_search.py
```

该接口用于创建 LangGraph thread/run，并固定使用：

```text
agent_name = patent-search-pro
assistant_id = patent-search-pro
```

前端/上游并不直接调用 `patent_get_detail` 或 `patent_get_citations` HTTP 接口；详情和引证由 Agent 在运行过程中调用工具完成。

### 2.2 Patent tool implementation

真正的外采专利工具契约定义在：

```text
patent_harness_base_副本/backend/packages/harness/deerflow/community/patenthub/tools.py
```

工具与外采端点对应关系：

| Tool | External endpoint | Purpose |
|---|---|---|
| `patent_search` | `/api/s` | 专利检索 |
| `patent_get_detail` | `/api/patent/base` + `/api/patent/claims` + optional `/api/patent/desc` | 专利详情 |
| `patent_get_legal_history` | `/api/patent/tx` | 法律状态历史 |
| `patent_get_citations` | `/api/patent/citing` | 引证/被引/非专利文献 |
| `enterprise_patent_portrait` | `/api/a/portrait` | 企业专利画像 |

阶段七只覆盖 `patent_get_detail` 与 `patent_get_citations`；`patent_get_legal_history` 可作为阶段七增强项或阶段八/后续兼容项。

### 2.3 Search result contract consumed by tools

`patent_search` 工具返回字段：

| Output field | Source field from vendor |
|---|---|
| `id` | `id` |
| `title` | `title` |
| `applicant` | `applicant` |
| `application_date` | `applicationDate` |
| `application_number` | `applicationNumber` |
| `document_number` | `documentNumber` |
| `document_date` | `documentDate` |
| `type` | `type` |
| `legal_status` | `legalStatus` |
| `main_ipc` | `mainIpc` |
| `rank` | `rank` |
| `inventor` | `inventor` |
| `summary` | `summary` |

这说明 SaaS Agent 侧更偏好 snake_case 工具输出；当前自研 search HTTP 响应仍保持 camelCase 外部 API 结构。阶段七设计应明确区分：

1. 自研 HTTP API 对 SaaS/业务后端的响应结构。
2. DeerFlow/Agent 工具层如要直接替换 PatentHub 工具时的工具输出结构。

### 2.4 Detail contract

`patent_get_detail` 入参：

| Parameter | Type | Default |
|---|---|---|
| `patent_id` | string | required |
| `include_description` | boolean | `false` |

`patent_get_detail` 输出字段：

| Output field | Source field from vendor |
|---|---|
| `id` | `patent.id` |
| `title` | `patent.title` |
| `type` | `patent.type` |
| `legal_status` | `patent.legalStatus` |
| `current_status` | `patent.currentStatus` |
| `application_number` | `patent.applicationNumber` |
| `application_date` | `patent.applicationDate` |
| `document_number` | `patent.documentNumber` |
| `document_date` | `patent.documentDate` |
| `applicant` | `patent.applicant` |
| `first_applicant` | `patent.firstApplicant` |
| `current_assignee` | `patent.currentAssignee` |
| `assignee` | `patent.assignee` |
| `inventor` | `patent.inventor` |
| `first_inventor` | `patent.firstInventor` |
| `applicant_address` | `patent.applicantAddress` |
| `agency` | `patent.agency` |
| `agent` | `patent.agent` |
| `ipc` | `patent.ipc` |
| `main_ipc` | `patent.mainIpc` |
| `loc` | `patent.loc` |
| `priority_number` | `patent.priorityNumber` |
| `full_priority_number` | `patent.fullPriorityNumber` |
| `pct_date` | `patent.pctDate` |
| `pct_application_data` | `patent.pctApplicationData` |
| `pct_publication_data` | `patent.pctPublicationData` |
| `image_path` | `patent.imagePath` |
| `pdf_list` | `patent.pdfList` |
| `summary` | `patent.summary` |
| `claims` | `/api/patent/claims` response `patent.claims` |
| `description` | `/api/patent/desc` response `patent.description` when `include_description=true` |

阶段七详情接口应至少覆盖以上字段中能从 `patent_index` 映射出的字段。

### 2.5 Citations contract

`patent_get_citations` 入参：

| Parameter | Type | Default |
|---|---|---|
| `patent_id` | string | required |

`patent_get_citations` 输出字段：

| Output field | Source field from vendor |
|---|---|
| `patent_id` | request `patent_id` |
| `cited_by` | `citedList` summarized |
| `patent_references` | `patentXref` summarized |
| `non_patent_references` | `noPatentXref` |

每条专利引用摘要字段：

| Output field | Source field from vendor |
|---|---|
| `id` | `id` |
| `title` | `title` |
| `applicant` | `applicant` |
| `application_date` | `applicationDate` |
| `application_number` | `applicationNumber` |
| `type` | `type` |
| `legal_status` | `legalStatus` |
| `main_ipc` | `mainIpc` |

当前 `docs/field_mapping.md` 中的 `referencesCited` / `relatedDocuments` 与 SaaS 工具层的 `cited_by` / `patent_references` / `non_patent_references` 命名不同。阶段七需要优先兼容 SaaS 工具层命名，或同时保留两套别名。

### 2.6 Session-bound vendor ID behavior

外采 PatentHub 有特殊限制：

```text
patent_search 返回的 id 有 60 分钟有效期；
patent_get_detail / patent_get_citations 必须使用最近一次 patent_search 返回的 id；
任意构造 id 可能返回 code=215。
```

脚本版客户端还记录了 claims 接口的 `code=215` 处理：先 search 建立会话，再重试 claims。

自研服务不应复制该临时会话限制。阶段七应明确：

1. 自研 `patent_id` 应为稳定 ID。
2. detail/citations 可直接用 search 返回的 `patent_id` 或 `id` 查询。
3. 如调用方传入 `documentNumber`，是否兼容查询需在阶段七设计中决定。

## 3. Impact on Stage 7

阶段七不应只按原 `docs/api_spec.md` 的宽泛描述实现。现在已有 SaaS 源码证据，应把阶段七定位调整为：

```text
实现自研 detail/citations HTTP 接口，同时输出兼容 DeerFlow PatentHub 工具层消费的核心字段。
```

建议阶段七采用双层字段策略：

1. HTTP API 层保留当前项目已约定的 camelCase 字段。
2. 详情/引证对象中同时提供 SaaS 工具层已使用的 snake_case 关键字段别名。

这样可以降低 SaaS Agent 工具替换成本，也不破坏本项目已完成的 search 接口风格。

## 4. Recommended Stage 7 Scope

### Must have

1. `GET /api/patent/detail/{patent_id}`
2. `GET /api/patent/citations/{patent_id}`
3. `include_description` 控制 `description` 是否返回。
4. `claims` 默认返回。
5. detail 返回基础著录信息、申请人、权利人、发明人、IPC、法律状态、摘要、权利要求。
6. citations 返回 `cited_by`、`patent_references`、`non_patent_references`，并保留 `referencesCited`、`referencesCitedRaw`、`referencesCitedText`、`relatedDocuments` 兼容字段。
7. 专利不存在返回 `40401`。
8. OpenSearch 异常返回 `50001`。

### Should have

1. 兼容用 `documentNumber` 查询详情。
2. `legalStatusHistory` / `transactions` 可从 `LegalStatusHistory` 或 `LegalStatus` 输出基础版本。
3. `pdf_list`、`image_path`、`family`、`drawings` 尽量保留原始结构。

### Not in Stage 7

1. 不实现 SaaS 业务流程改造。
2. 不改 DeerFlow SaaS 源码。
3. 不实现企业专利画像。
4. 不实现完整法律历史接口，除非阶段七设计明确纳入。
5. 不复制 PatentHub 60 分钟 session-bound ID 机制。

## 5. Next Action

基于本审计结果，下一步应更新阶段七设计文档：

```text
docs/superpowers/specs/2026-07-06-stage-7-detail-citations-design.md
```

设计文档应以 SaaS 源码中的 `patenthub/tools.py` 为核心契约证据。
