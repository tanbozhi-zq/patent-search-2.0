# Stage 10 SaaS Integration Design

## 1. Background

阶段 9 已完成外采服务契约对比。当前代码契约层未发现 `blocking` 差异，允许进入阶段 10 SaaS 联调准备。

阶段 10 目标是把自研专利检索服务接入 SaaS/Agent 调用链路，验证 search/detail/citations 三个核心工具调用可以在真实业务流程中稳定工作，并具备灰度和回滚条件。

## 2. Goals

阶段 10 目标：

1. 设计 SaaS PatentHub 工具到自研 HTTP API 的适配方式。
2. 明确 search/detail/citations 的参数转换、字段转换和错误转换。
3. 在测试或联调环境完成 SaaS Agent 调用链路验证。
4. 输出 SaaS 联调报告、问题清单、灰度建议和回滚方案。
5. 给出是否进入阶段 11 部署上线与交付沉淀的总控建议。

## 3. Non-Goals

阶段 10 不做：

1. 不直接修改 `patent_harness_base_副本/` 作为交付源码；该目录仍为只读参考。
2. 不做生产全量切流。
3. 不修改 OpenSearch mapping。
4. 不重建索引。
5. 不实现企业专利画像。
6. 不实现高亮片段，除非联调证明是阻塞项并经总控重新冻结需求。
7. 不追求与外采 PatentHub 召回和排序完全一致。

## 4. Integration Strategy

推荐采用“工具适配层”方式，而不是直接要求自研 HTTP API 改成 PatentHub 原始响应。

### 4.1 Search adapter

PatentHub 工具层：

```text
patent_search(q, ds, page, page_size, sort, highlight)
```

自研接口：

```text
POST /api/patent/search
```

适配规则：

| Tool field | Self-hosted field | Rule |
|---|---|---|
| `q` | `q` | 原样传递 |
| `ds` | `ds` | 原样传递，仅支持 `cn/all` |
| `page` | `page` | 原样传递 |
| `page_size` | `page_size` | 工具层最大建议限制为 50；自研 API 最大 100 |
| `sort` | `sort` | 阶段 10 仅允许 `relation`、`!applicationDate` |
| `highlight` | `highlight` | 转为 `0/1`；仅兼容接收 |

响应适配：

| Self-hosted field | Tool field |
|---|---|
| `records` | `patents` |
| `total` | `total` |
| `page` | `page` |
| `records[].id` | `patents[].id` |
| `records[].summary` | `patents[].summary` |
| `records[].application_number` | `patents[].application_number` |
| `records[].document_number` | `patents[].document_number` |
| `records[].application_date` | `patents[].application_date` |
| `records[].document_date` | `patents[].document_date` |
| `records[].legal_status` | `patents[].legal_status` |
| `records[].main_ipc` | `patents[].main_ipc` |

### 4.2 Detail adapter

PatentHub 工具层：

```text
patent_get_detail(patent_id, include_description=false)
```

自研接口：

```text
GET /api/patent/detail/{patent_id}
GET /api/patent/detail/{patent_id}?include_description=true
```

适配策略：

1. `patent_id` 使用自研 search 返回的稳定 `id` / `patent_id`。
2. 不复制 PatentHub 60 分钟 session-bound ID 机制。
3. 工具层优先消费 snake_case 字段。
4. `description` 仅在 `include_description=true` 时请求。

### 4.3 Citations adapter

PatentHub 工具层：

```text
patent_get_citations(patent_id)
```

自研接口：

```text
GET /api/patent/citations/{patent_id}
```

适配策略：

1. `cited_by`、`patent_references`、`non_patent_references` 直接消费自研响应。
2. 保留 `referencesCited`、`relatedDocuments` 供排查数据差异。
3. 对 `DocNumber/Country/Kind/Date` 已有归一化摘要，不需 SaaS 侧重复处理。

## 5. Error Handling

自研服务错误响应为：

```json
{
  "success": false,
  "code": 40001,
  "message": "...",
  "data": null
}
```

工具适配层应将错误转换为工具层稳定 JSON：

```json
{
  "error": "...",
  "code": 40001
}
```

阶段 10 必须覆盖：

| Self-hosted code | Meaning | Adapter behavior |
|---|---|---|
| `40001` | 查询语法错误 | 返回工具错误，不重试 |
| `40002` | 参数非法 | 返回工具错误，不重试 |
| `40003` | 分页非法 | 返回工具错误，不重试 |
| `40101` | 鉴权失败 | 阻塞联调，检查 token 配置 |
| `40401` | 专利不存在 | 返回工具错误 |
| `50001` | OpenSearch 异常 | 记录并返回工具错误，可人工重试 |
| `50002` | 服务内部异常 | 记录并返回工具错误 |

## 6. Configuration

阶段 10 推荐新增或确认以下配置：

```text
PATENT_SEARCH_BASE_URL=http://127.0.0.1:8000
PATENT_SEARCH_API_TOKEN=...
PATENT_SEARCH_USE_SELF_HOSTED=true/false
PATENT_SEARCH_PAGE_SIZE_LIMIT=50
```

联调默认应支持一键回退外采 PatentHub：

```text
PATENT_SEARCH_USE_SELF_HOSTED=false
```

## 7. Rollout Strategy

阶段 10 只做联调和灰度准备：

1. 本地/测试环境工具适配。
2. 单 Agent 或单测试账号灰度。
3. 只覆盖 search/detail/citations。
4. 记录每次联调问题和自研/外采差异。
5. 保留配置开关快速回退外采。

## 8. Acceptance Criteria

阶段 10 通过标准：

1. SaaS Agent 可通过适配层调用自研 search。
2. search 返回 id 可继续调用 detail/citations。
3. detail/citations 在 SaaS 工具层输出中字段完整。
4. 错误响应能被 Agent 以稳定 JSON 识别。
5. 鉴权 token 与 base_url 配置可控。
6. 回退外采配置已验证。
7. 阶段 10 SaaS 联调报告已输出。
8. 总控确认是否进入阶段 11 部署上线与交付沉淀。

