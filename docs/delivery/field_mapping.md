# 字段映射说明

## 1. 数据来源

自研服务底层数据来自 OpenSearch 索引：

```text
patent_index
```

字段依据：

1. `docs/delivery/patent_index_field_table.md`
2. `docs/delivery/mcp_parameter_field_dependency.md`
3. 已确认的交付口径和当前代码实现

## 2. 查询字段映射

| 外部查询字段 | 含义 | OpenSearch 字段 | 第一版要求 |
|---|---|---|---|
| `title` | 标题 | `Title`, `TitleCN`, `TitleEN`, `TitleOriginal` | 支持 |
| `ab` | 摘要 | `Abstract`, `AbstractCN`, `AbstractEN`, `AbstractOriginal` | 支持 |
| `tscd` | 标题+摘要+权利要求+说明书 | `Title`, `Abstract`, `MainClaim`, `Requirement`, `Instructions` | 支持 |
| `mainClaim` | 首权/主权利要求 | `MainClaim` | 阶段 10.5 支持 |
| `claims` | 完整权利要求书 | `Requirement` | 阶段 10.5 支持 |
| `description` | 说明书 | `Instructions` | 阶段 10.5 支持 |
| `ipc` | IPC 分类 | `IPC`, `IPCList`, `IPCSmallCategory`, `IPCLargeGroup`, `IPCSmallGroup` | 支持 |
| `applicant` | 申请人 | `Applicant`, `ApplicantNormalized`, `FirstApplicant` | 支持 |
| `currentAssignee` | 当前权利人 | `Assignee`, `AssigneeNormalized` | 支持 |
| `agency` | 代理机构 | `Agency`, `AgencyRaw` | Stage 12.1 支持 |
| `agent` | 代理人 | `Agent` | Stage 12.1 支持 |
| `ad` | 申请日 | `ApplicationDate` | 支持 |
| `documentYear` | 公开年 | `PublicationDate` | 支持 |
| `legalStatus` | 法律状态 | `LatestLegalStatus`, `LegalStatus`, `LegalStatusCode` | 支持基础映射 |
| `type` | 专利类型 | `Type`, `PatentTypeCode`, `Kind` | 支持 |

## 3. 搜索结果返回字段映射

| 对外返回字段 | OpenSearch 字段 | 类型建议 | 说明 |
|---|---|---|---|
| `id` | `patent_id` | string | 专利内部 ID |
| `patent_id` | `patent_id` | string | 专利内部 ID |
| `applicationNumber` | `ApplicationNumber` | string | 申请号 |
| `documentNumber` | `PublicationNumber` | string | 公开号/公告号 |
| `title` | `Title` | string | 标题 |
| `ti` | `Title` | string | 标题别名 |
| `abstract` | `Abstract` | string | 摘要 |
| `ab` | `Abstract` | string | 摘要别名 |
| `summary` | `Abstract` | string | SaaS 工具层摘要别名 |
| `applicant` | `Applicant` | string | 申请人 |
| `pa` | `Applicant` | string | 申请人别名 |
| `currentAssignee` | `Assignee` | string | 当前权利人 |
| `inventor` | `Inventor` | string | 发明人 |
| `mainIpc` | `IPC` | string | 主 IPC |
| `ipcMainList` | `IPCList` | array[string] | IPC 列表 |
| `applicationDate` | `ApplicationDate` | string | 申请日 |
| `ad` | `ApplicationDate` | string | 申请日别名 |
| `documentDate` | `PublicationDate` | string | 公开日/公告日 |
| `legalStatus` | `LatestLegalStatus` / `LegalStatus` | string | 法律状态 |
| `currentStatus` | `LatestLegalStatus` | string | 当前状态 |
| `type` | `Type` | string | 专利类型 |
| `score` | `_score` | number | OpenSearch 相关性分数 |

## 4. 专利详情字段

详情接口应在搜索结果字段基础上补充：

| 对外字段 | OpenSearch 字段 | 说明 |
|---|---|---|
| `claims` | `Requirement` | 完整权利要求书 |
| `mainClaim` | `MainClaim` | 主权利要求 |
| `description` | `Instructions` | 说明书正文，受 `include_description` 控制 |
| `family` | `Family`, `SimpleFamily`, `ExtendedFamily`, `DocDBFamily` | 同族信息 |
| `abstractFigure` | `AbstractFigure`, `AbstractFigureUrl` | 摘要附图信息 |
| `drawings` | `Drawings`, `DescriptionImages` | 附图信息 |
| `legalStatusHistory` | `LegalStatus`, `LegalStatusHistory` | 法律状态历史 |

## 5. 引证/相关文献字段

引证接口数据来源优先级：

```text
ReferencesCited
ReferencesCitedRaw
ReferencesCitedText
RelatedDocuments
```

建议返回：

| 对外字段 | OpenSearch 字段 | 说明 |
|---|---|---|
| `referencesCited` | `ReferencesCited` | 结构化引证文献 |
| `referencesCitedRaw` | `ReferencesCitedRaw` | 原始引证文本 |
| `referencesCitedText` | `ReferencesCitedText` | 文本化引证列表 |
| `relatedDocuments` | `RelatedDocuments` | 相关文献 |

## 6. 空值处理规则

第一版统一规则：

1. 字符串字段缺失时返回空字符串 `""`。
2. 数组字段缺失时返回空数组 `[]`。
3. 数值字段缺失时返回 `null`。
4. 日期字段缺失时返回空字符串 `""`。
5. `legalStatus` 优先使用 `LatestLegalStatus`；缺失时使用 `LegalStatus`；仍缺失则返回空字符串。
6. `currentAssignee` 优先使用 `Assignee`；缺失时允许回退到 `Applicant`。

## 7. 数组处理规则

1. OpenSearch 原始字段为数组时保持数组。
2. OpenSearch 原始字段为分号分隔字符串时，搜索结果第一版可保持字符串；需要数组时由明确字段承载。
3. `ipcMainList` 必须返回数组。
4. 引证、同族、附图等复杂对象保持原始结构优先，后续再做标准化。

## 8. 法律状态基础映射

第一版法律状态先采用基础映射，不追求完全精确。

| 外部查询值 | OpenSearch 判断建议 |
|---|---|
| 有效专利 | `LatestLegalStatus` 包含 `授权`、`有效` |
| 在审 | `LatestLegalStatus` 包含 `公开`、`实质审查` |
| 失效 | `LatestLegalStatus` 包含 `终止`、`届满`、`撤回`、`驳回` |

待业务方确认：

1. `有效专利` 是否包含全部授权状态。
2. `实质审查的生效` 是否统一归为在审。
3. `未缴年费专利权终止` 是否统一归为失效。
4. 法律状态是否需要输出三分类字段。

## 阶段六查询字段映射

| q 字段 | OpenSearch 字段 |
|---|---|
| `title` | `Title`, `TitleCN`, `TitleEN` |
| `ab` | `Abstract`, `AbstractCN`, `AbstractEN` |
| `tscd` | `Title`, `Abstract`, `MainClaim`, `Requirement`, `Instructions` |
| `ipc` | `IPC`, `IPCList`, `IPCSmallCategory`, `IPCLargeGroup`, `IPCSmallGroup` |
| `applicant` | `Applicant`, `ApplicantNormalized`, `FirstApplicant` |
| `currentAssignee` | `Assignee`, `AssigneeNormalized` |
| `agency` | `Agency`, `AgencyRaw` |
| `agent` | `Agent` |
| `legalStatus` | `LatestLegalStatus`, `LegalStatus` |
| `type` | `Type`, `PatentTypeCode`, `Kind` |
| `ad` | `ApplicationDate` |
| `documentYear` | `PublicationDate` |

## 阶段 10.5 细粒度文本查询字段映射

阶段 10.5 新增 `mainClaim`、`claims`、`description` 三个 q 字段，不修改 OpenSearch mapping，不改变 `tscd` 既有范围。

| q 字段 | OpenSearch 字段 | `compat` 模式 | `normal` 模式 |
|---|---|---|---|
| `mainClaim` | `MainClaim` | phrase `multi_match` | 普通 `multi_match` |
| `claims` | `Requirement` | phrase `multi_match` | 普通 `multi_match` |
| `description` | `Instructions` | phrase `multi_match` | 普通 `multi_match` |

## Hotfix: 企业实体字段 analyzer 兼容匹配

当前 OpenSearch 中 `Applicant` / `Assignee` 使用中文分词，`ApplicantNormalized` / `AssigneeNormalized` 使用 standard analyzer。为避免企业归属检索在默认 `compat` 模式下被普通分词查询放大，实体字段查询策略如下：

| q 字段 | OpenSearch 字段 | `compat` 模式 | `normal` 模式 |
|---|---|---|---|
| `applicant` | `Applicant`, `ApplicantNormalized`, `FirstApplicant` | phrase `multi_match` | 普通 `multi_match` |
| `currentAssignee` | `Assignee`, `AssigneeNormalized` | phrase `multi_match` | 普通 `multi_match` |
| `agency` | `Agency`, `Agency.keyword`, `AgencyRaw` | phrase `multi_match` | 普通 `multi_match` on `Agency`, `AgencyRaw` |
| `ipc` 中的 `IPCList` | `IPCList.keyword`, `IPCList` | `term` on `IPCList.keyword` + `match_phrase` on `IPCList` | 普通 `match` on `IPCList` |

该 hotfix 不修改 OpenSearch mapping，不重建索引；如需真正精确企业归属检索，后续应补充 keyword / normalizer 字段并重建索引。

## Stage 12.1 API 兼容补点字段

| 能力 | 字段 / 结构 | 说明 |
|---|---|---|
| sort 兼容 | `relation` / `rank` / `relevance` / `score` | 映射 `_score` |
| sort 兼容 | `applicationDate` / `!applicationDate` | 映射 `ApplicationDate` 升序 / 降序 |
| sort 兼容 | `documentDate` / `!documentDate` | 映射 `PublicationDate` 升序 / 降序 |
| 裸 IPC | `H02M`、`H02M7/483`、`F16K` | 自动按 IPC 字段查询 |
| search metadata | `total_pages`、`next_page`、`took_ms` | 顶层补充字段，`records` 保留 |
| legal history | `patent_id`、`transaction_count`、`transactions` | `transactions` 来源优先为结构化 `LegalStatusHistory` 数组；缺失返回 `[]` |

## 阶段七详情与引证字段映射

阶段七新增 `GET /api/patent/detail/{patent_id}` 与 `GET /api/patent/citations/{patent_id}` 两个接口，详情接口对核心关键字段同时输出 camelCase 与 snake_case 别名，引证接口同时输出 SaaS PatentHub 工具层字段与原始兼容字段。

### 详情字段映射

| 对外字段（camelCase / snake_case） | OpenSearch 字段 | 回退规则 |
|---|---|---|
| `applicationNumber` / `application_number` | `ApplicationNumber` | 无 |
| `documentNumber` / `document_number` | `PublicationNumber` | 无 |
| `applicationDate` / `application_date` | `ApplicationDate` | 无 |
| `documentDate` / `document_date` | `PublicationDate` | 无 |
| `legalStatus` / `legal_status` | `LatestLegalStatus` | 缺失时回退 `LegalStatus`，仍缺失返回 `""` |
| `currentStatus` / `current_status` | `LatestLegalStatus` | 缺失返回 `""` |
| `currentAssignee` / `current_assignee` | `Assignee` | 缺失时回退 `Applicant` |
| `mainIpc` / `main_ipc` | `IPC` | 无 |
| `ipcMainList` / `ipc_main_list` | `IPCList` | 缺失返回 `[]` |
| `firstApplicant` / `first_applicant` | `FirstApplicant` | 无 |
| `firstInventor` / `first_inventor` | `FirstInventor` | 无 |
| `priorityNumber` / `priority_number` | `PriorityNumber` | 无 |
| `fullPriorityNumber` / `full_priority_number` | `FullPriorityNumber` | 无 |
| `pctDate` / `pct_date` | `PCTDate` | 无 |
| `pctApplicationData` / `pct_application_data` | `PCTApplicationData` | 无 |
| `pctPublicationData` / `pct_publication_data` | `PCTPublicationData` | 无 |
| `imagePath` / `image_path` | `AbstractFigureUrl` / `ImagePath` | 无 |
| `pdfList` / `pdf_list` | `PDFList` | 无 |
| `legalStatusHistory` / `legal_status_history` | `LegalStatusHistory` | 缺失时回退 `LegalStatus` |
| `claims` | `Requirement` | 缺失返回 `""` |
| `mainClaim` / `main_claim` | `MainClaim` | 缺失返回 `""` |
| `description` | `Instructions` | 仅 `include_description=true` 时返回 |

### 引证字段映射

| 对外字段 | OpenSearch 字段 | 处理规则 |
|---|---|---|
| `cited_by` | `RelatedDocuments` | 尽力归一化为被引专利摘要数组；无法结构化时返回 `[]` |
| `patent_references` | `ReferencesCited` | 尽力归一化为引用专利摘要数组；无法结构化时返回 `[]` |
| `non_patent_references` | `ReferencesCitedRaw` / `ReferencesCitedText` | 非专利文献或原始引用文本，缺失返回 `""` 或 `[]` |
| `referencesCited` | `ReferencesCited` | 原始结构化引证字段，保持原始结构 |
| `referencesCitedRaw` | `ReferencesCitedRaw` | 原始引证文本，缺失返回 `""` |
| `referencesCitedText` | `ReferencesCitedText` | 文本化引证列表，缺失返回 `""` |
| `relatedDocuments` | `RelatedDocuments` | 原始相关文献，缺失返回 `[]` |

引证摘要对象（`cited_by`、`patent_references` 结构化条目）字段：`id`、`title`、`applicant`、`application_date`、`application_number`、`type`、`legal_status`、`main_ipc`。

当 OpenSearch 原始引用条目使用 `DocNumber`、`Country`、`Kind`、`Date` 结构时，mapper 采用以下兼容规则：

1. `id` 使用 `Country + DocNumber + Kind` 合成，例如 `CN112501955A`。
2. `application_date` 在缺少 `ApplicationDate` 时使用 `Date`。
3. 其他缺失摘要字段按空值规则返回 `""`。
4. 归一化摘要会跳过完全无法识别的空摘要，并对完全相同的摘要条目去重。
5. 原始结构仍保留在 `referencesCited` 或 `relatedDocuments`。

空值规则沿用本文件第 6 节：字符串缺失返回 `""`，数组缺失返回 `[]`，对象缺失按字段语义返回 `[]` 或 `{}`。
