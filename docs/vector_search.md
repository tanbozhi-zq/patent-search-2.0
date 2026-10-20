# 向量与混合检索

本文说明向量与混合检索的字段、查询流程、排序及运行配置。
请求参数和响应格式见 [api.md](api.md)。使用前需完成向量字段配置和数据准备；多字段向量及混合检索还需安装 Search Pipeline。

三种模式的必填参数、禁止组合、排序、`top_k`、分页、返回字段及错误统一见 [API 契约](api.md#22-检索)。本文维护执行流程与索引配置。

## 字段注册与数据覆盖

请求只接受稳定业务名，不接受原始 OpenSearch 字段名：

| 公开名 | OpenSearch 字段 | 维度/距离 |
|---|---|---|
| `abstract` | `AbstractVector1024` | 1024 / `cosinesimil` |
| `main_claim` | `MainClaimVector1024` | 1024 / `cosinesimil` |
| `independent_claims` | `IndependentClaimsVector1024` | 1024 / `cosinesimil` |

查询使用 `doubao-embedding-vision-250615` 原生 1024 维输出，不截断旧向量，也不回退查询旧 `*Vector` 字段。各字段的可用性取决于目标索引的 mapping 和搜索可见数据覆盖。

数据为空或仅部分覆盖时，该字段可能返回少量或零候选。应在目标数据集上检查查询结果的相关性，并根据实际数据规模测试查询容量。

`vector` 最多选 5 个字段；`hybrid` 的 Boolean 分支占用一个
OpenSearch 3.3 Hybrid 子查询名额，因此最多选 4 个字段。当前注册表只有
3 项。可选字段以本文注册表为准；服务未提供公开 capabilities API。

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

## 排序窗口与指标

`top_k` 同时控制 k-NN 的 `k`、Hybrid 的 `pagination_depth` 和有界计数；翻页不扩大候选窗口。日期排序在候选融合后执行，不追加分数作为二级排序。

查询阶段指标统一见[指标契约](ops/observability.md#指标契约)，避免把 OpenSearch 服务端 `took` 与客户端等待或完整 HTTP 耗时混用。

## Pipeline 配置与排障

版本化定义位于 `deployment/opensearch/search_pipelines_v1.json`。启用多字段向量或混合检索前，
按部署手册安装并通过 GET 确认定义一致；查询请求不会创建或修改共享 pipeline。
提交定义文件不会自动安装 pipeline，也不会修改 mapping 或回填向量。

常见边界：

- 单字段向量不依赖 Search Pipeline；
- 多字段向量/混合在 profile 未安装或定义不一致时明确失败，不回退到原始分数；
- `timed_out=true` 或任意失败分片不能作为完整成功返回；
- 字段 mapping 与向量数据覆盖情况均需检查。

## 维护索引

| 维护内容 | 代码位置 | 相关文档/测试 |
|---|---|---|
| 公开向量字段、OS 字段、维度、距离、模型 | `app/mappings/query_field_mapping.py` | 本文数据表、OpenAPI/Console/MCP 契约测试、数据覆盖记录。 |
| `top_k` 默认/上限与模式组合 | `app/schemas/search.py` | [api.md](api.md) 和 schema/分页测试。 |
| profile 选择、候选与排序 | `app/query/semantic_dsl_builder.py` | 本文、DSL/集成测试和性能矩阵。 |
| RRF 权重与 pipeline 定义 | `deployment/opensearch/search_pipelines_v1.json` | 本文、回读测试、部署/回滚手册和性能矩阵。 |
| 公共 total/分页/错误投影 | `app/services/search_service.py` | [api.md](api.md) 和服务/接入层测试。 |
| 指标阶段与 label 枚举 | `app/core/metrics.py` | [ops/observability.md](ops/observability.md) 和低基数契约测试。 |

任何修改字段注册、`top_k`、权重、profile 或模式语义的 PR 都必须同步
更新该表对应的文档与契约测试。
