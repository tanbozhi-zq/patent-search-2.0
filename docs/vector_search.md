# 向量与混合检索工程事实

本文定义向量与混合检索行为，公共 JSON 契约见[api.md](api.md)。代码支持、数据覆盖、Pipeline 安装和部署验收应分别确认。

## 公共模式

三种模式共用唯一入口 `POST /api/patent/search`：

| mode | 必填 | 禁止 | 执行策略 |
|---|---|---|---|
| `boolean` | `q` | `semantic_text/vector_fields/top_k` | 保留现有 parser、AST 和 Boolean DSL。 |
| `vector` | `semantic_text/vector_fields` | `q` | 单字段 k-NN；多字段 Hybrid Query + RRF。 |
| `hybrid` | `q/semantic_text/vector_fields` | — | 一个 Boolean 分支加一个或多个 k-NN 分支，并集召回后 RRF。 |

省略 `mode` 时默认 `boolean`，旧客户端无需增加字段。
`semantic_text` 与 `q` 共用本次请求冻结的字符预算。非法模式组合、
未注册/重复字段和窗口越界均在外部调用前拒绝，无关字段不会被静默忽略。

## 字段注册与数据覆盖

请求只接受稳定业务名，不接受原始 OpenSearch 字段名：

| 公开名 | OpenSearch 字段 | 维度/距离 |
|---|---|---|
| `abstract` | `AbstractVector1024` | 1024 / `cosinesimil` |
| `main_claim` | `MainClaimVector1024` | 1024 / `cosinesimil` |
| `independent_claims` | `IndependentClaimsVector1024` | 1024 / `cosinesimil` |

查询使用 `doubao-embedding-vision-250615` 原生 1024 维输出，不截断旧向量，也不回退查询旧 `*Vector` 字段。mapping 和数据覆盖需按[部署核对清单](ops/deployment_checklist.md#索引与向量覆盖)逐项确认，不能只看字段注册表。

数据空或只部分覆盖时，该字段可能返回少量或零候选，这不是程序错误。
多字段受控 fixture 可以证明查询与融合路径可执行，但不能外推为首权/独权已全量，
也不能替代数据全量后的相关性和容量验收。

`vector` 最多选 5 个字段；`hybrid` 的 Boolean 分支占用一个
OpenSearch 3.3 Hybrid 子查询名额，因此最多选 4 个字段。当前注册表只有
3 项，通用逻辑不依赖这个当前数量。未提供公开 capabilities API。

## 查询向量和请求 deadline

`SearchService` 按注册表中的 `{embedding_model, dimensions}` 分组。同一配置在一个
请求内只生成一次查询向量，并复用给该组的每个字段；新配置按注册表自然扩展。
适配器根据模型选受控 provider endpoint，响应必须通过模型、维度、数值、
有限值和非全零校验。

一个绝对 deadline 覆盖向量生成、OpenSearch 和结果映射。Ark HTTP 在 deadline
到达时主动取消在途 I/O，各阶段不重置一份新超时。向量生成失败时请求失败；
不回退到 Boolean，也不会继续进入 OpenSearch。

## OpenSearch 查询与融合

- 单向量使用原生 k-NN，profile 为 `patent-knn-cosine-v1`，无 Search Pipeline。
- 多向量使用 Hybrid Query，每个向量字段一个 k-NN 分支，使用
  `patent-vector-rrf-v1-2` 至 `patent-vector-rrf-v1-5`，各分支等权。
- 混合模式的第一个分支是原 Boolean query，后续是向量分支，使用
  `patent-hybrid-rrf-v1-1` 至 `patent-hybrid-rrf-v1-4`。Boolean 侧权重合计 0.5，
  向量侧合计 0.5 并在所选字段之间均分。
- 所有 RRF profile 使用版本控制的 `score-ranker-processor` 定义，
  `rank_constant=60`。请求方不能传 pipeline、权重、RRF 参数或候选放大量。

`ds` 是对全部分支生效的全局过滤。Hybrid 使用并集召回：命中 Boolean
或任一向量分支的文档都可进入融合，同一文档只返回一次。`q` 中的 `NOT`
只属于 Boolean 分支，不自动升格为全局排除。OpenSearch 请求固定
`allow_partial_search_results=false`；`timed_out=true`、失败分片、缺失/失败 pipeline
和查询错误都 fail closed，不返回部分成功或未融合排序。

## 排序、`top_k` 和分页

`relation/rank/relevance/score` 都按 `_score` 降序。`applicationDate`、
`!applicationDate`、`documentDate`、`!documentDate` 分别按申请日/公开日升序或降序。
日期排序仍先形成并融合候选，但最终顺序只由日期决定，不追加 `_score`
作为二级排序；此时响应 `score` 允许为 `null`。

`top_k` 默认 100、最大 1000，是可排名和可访问的最终结果窗口。k-NN
`k`、Hybrid `pagination_depth` 和 `track_total_hits` 的有界阈值由该窗口决定，
不随页码变化。请求必须满足 `from + size <= top_k`；不能整除时不开放
会跨过窗口的尾页。翻页时除 `page` 外的所有检索条件必须保持一致。

Boolean `total` 继续表示完整命中数。语义模式的 `total` 是当前可访问的融合窗口，
最大不超过 `top_k`；OpenSearch 返回 `hits.total.relation=gte` 时仍按这一有界语义投影。
`total_pages/accessible_pages/next_page` 都按这一窗口计算。

## 响应、错误和可观测性

三种模式共用 `SearchResponse` 和 `records`。仅 `vector/hybrid` 增加
`search_context`，其中只有 `mode`、公开字段名、`top_k`、固定
`ranking_profile` 和 `sort`。不返回查询向量、分支原始分数、候选数或供应商信息。

稳定错误仍使用 [api.md](api.md) 的统一信封。向量供应商拒绝/无效响应、
OpenSearch 查询或 pipeline 失败为依赖失败；绝对 deadline 耗尽为超时。
任何语义失败都不降级为 Boolean。

两个阶段指标共用固定 label
`stage,mode,vector_field_count,sort_type,ranking_profile,outcome`：

- `patent_search_query_stage_calls_total`：已完成阶段计数；
- `patent_search_query_stage_duration_seconds`：同一阶段的耗时直方图。

`stage` 只有 `query_vector/opensearch/opensearch_took/end_to_end`。`opensearch`
是 Repository 调用的客户端 wall time；`opensearch_took` 只记录成功响应中
OpenSearch 返回的服务端执行时间；`end_to_end` 是通过 schema、鉴权和舱壁
后的 `SearchService.search()` 时间。完整 ingress-to-response 时间继续使用
`patent_search_http_request_duration_seconds`。`outcome` 只有
`success/timeout/failure/rejected`。schema/鉴权/舱壁在 Service 之前发生，分别由
既有 HTTP `status/code` 与 bulkhead 指标表达。

指标和日志不允许用 `q`、`semantic_text`、向量值、精确字段组合、
provider endpoint、模型路由、凭据或 request ID 做 label。更完整的指标口径见
[ops/observability.md](ops/observability.md)。

## Pipeline 发布与排障

版本化定义位于 `deployment/opensearch/search_pipelines_v1.json`。它们必须在正式服务
放量前按发布手册安装并精确 GET 回读；请求期永远不创建或修改共享 pipeline。
部署时应核验安装状态，见[部署核对清单](ops/deployment_checklist.md)。提交定义文件不会自动安装 pipeline，也不会修改 mapping 或回填向量。

常见边界：

- 单字段向量不依赖 Search Pipeline；
- 多字段向量/混合在 profile 未安装或定义不一致时明确失败，不回退到原始分数；
- `timed_out=true` 或任意失败分片不能作为完整成功返回；
- “mapping 已存在”不等于“向量数据已覆盖”。

## 持续维护源表

| 变更事实 | 代码唯一来源 | 必须同步的文档/测试 |
|---|---|---|
| 公开向量字段、OS 字段、维度、距离、模型 | `app/mappings/query_field_mapping.py` | 本文数据表、OpenAPI/Console/MCP 契约测试、数据覆盖记录。 |
| `top_k` 默认/上限与模式组合 | `app/schemas/search.py` | [api.md](api.md)、本文和 schema/分页测试。 |
| profile 选择、候选与排序 | `app/query/semantic_dsl_builder.py` | 本文、DSL/集成测试和性能矩阵。 |
| RRF 权重与 pipeline 定义 | `deployment/opensearch/search_pipelines_v1.json` | 本文、回读测试、发布/回滚手册和性能矩阵。 |
| 公共 total/分页/错误投影 | `app/services/search_service.py` | [api.md](api.md)、本文和服务/接入层测试。 |
| 指标阶段与 label 枚举 | `app/core/metrics.py` | 本文、[ops/observability.md](ops/observability.md) 和低基数契约测试。 |

任何修改字段注册、`top_k`、权重、profile 或模式语义的 PR 都必须同步
更新该表对应的文档与契约测试；聊天记录、PR 评论和本地临时文件不是长期事实源。
