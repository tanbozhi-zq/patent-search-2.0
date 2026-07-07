# Stage 12.1 核心 API 兼容补点开发派工单

## 1. 角色判断

本派工单由项目总控维护，供开发人员执行。项目总控只确认边界、字段映射、API 设计、查询语法清单、Git 状态和交付文档，不直接开发。

Stage 12.1 只处理核心 API 兼容补点，不开发 `deerflow_tool/`，不开发 `mcp_server/`。

## 2. 阶段目标

在不改动既有接口路径、不修改 OpenSearch mapping、不重建索引的前提下，补齐 DeerFlow Tool 前置所需的 5 个 API 兼容点。

完成 Stage 12.1 后，开发人员才能进入：

```text
Stage 12.2 DeerFlow Tool 封装
```

## 3. 开发边界

本阶段允许修改：

1. `app/schemas/search.py`
2. `app/query/`
3. `app/mappings/`
4. `app/api/`
5. `app/services/`
6. `tests/`
7. `docs/delivery/api_spec.md`
8. `docs/delivery/query_syntax.md`
9. `docs/delivery/field_mapping.md`

本阶段不做：

1. 不新增 `deerflow_tool/`。
2. 不新增 `mcp_server/`。
3. 不修改接口路径。
4. 不移除 `records`。
5. 不把 Tool 层 `patents` 放进 HTTP API 顶层结构。
6. 不修改 `patent_harness_base_副本/`。
7. 不修改 `对外交付文档/`。

## 4. 五个兼容补点

### A1：sort 支持更多 PatentHub 风格值

目标：

1. 保留现有 `relation`。
2. 保留现有 `!applicationDate`。
3. 补充 PatentHub 风格常用值的兼容接收和映射。
4. 未知 sort 值继续返回稳定参数错误。

建议第一批兼容值：

| 输入值 | 映射 |
|---|---|
| `relation` | `_score` |
| `relevance` | `_score` |
| `score` | `_score` |
| `!applicationDate` | `ApplicationDate desc` |
| `applicationDate` | `ApplicationDate asc` |
| `!documentDate` | `PublicationDate desc` |
| `documentDate` | `PublicationDate asc` |

开发人员如发现 PatentHub 合约中还有必需值，应在开发报告中说明，并由项目总控确认是否纳入本阶段。

### A2：支持 `agency` / `agent` 字段检索

目标：

| 查询字段 | OpenSearch 字段 |
|---|---|
| `agency` | `Agency`, `AgencyRaw` |
| `agent` | `Agent` |

验收查询：

```text
agency:(知识产权代理)
agent:(张)
```

### A3：裸 IPC 自动识别

目标：

用户输入裸 IPC 时，服务应按 IPC 查询处理，而不是只当普通标题摘要关键词。

第一批识别：

```text
H02M
H02M7/483
F16K
```

保护要求：

1. 普通中文词 `阀门` 仍按关键词检索。
2. 普通英文词不应被误判为 IPC，除非符合 IPC 基础格式。
3. 显式 `ipc:H02M` 行为不变。

### A4：search 返回分页和耗时 metadata

目标：

`POST /api/patent/search` 顶层在保留既有字段基础上补充：

```json
{
  "total_pages": 3,
  "next_page": 2,
  "took_ms": 35
}
```

规则：

1. `total_pages = ceil(total / page_size)`。
2. `total=0` 时 `total_pages=0`。
3. 有下一页时 `next_page=page+1`。
4. 无下一页时 `next_page=null`。
5. `took_ms` 优先来自 OpenSearch raw response 的 `took`，缺失时可返回 `null`。

### A5：legal history 基础能力

目标：

先提供稳定基础结构，支撑 Tool 层 `patent_get_legal_history`。

第一版允许返回：

```json
{
  "patent_id": "cn-xxx",
  "transaction_count": 0,
  "transactions": []
}
```

开发人员需要在以下两种方案中选择一种，并在开发说明中记录：

1. 核心 API 新增法律历史 HTTP 端点。
2. 先在既有 detail 数据中映射法律历史字段，Tool 层读取后转换。

项目总控推荐：若数据源字段尚不稳定，先实现基础结构，避免直接承诺完整法律事务历史。

## 5. 建议提交粒度

开发人员不要一次提交五个补点。建议按补点拆提交：

```text
test: cover Stage 12 sort compatibility
feat: add Stage 12 sort compatibility
test: cover agency and agent query fields
feat: add agency and agent query fields
test: cover bare IPC query normalization
feat: add bare IPC query normalization
test: cover search pagination metadata
feat: add search pagination metadata
test: cover legal history base response
feat: add legal history base response
docs: update Stage 12 API compatibility docs
```

每个补点完成后运行：

```bash
.venv/bin/python -m pytest -q
```

## 6. 交付物

开发人员完成 Stage 12.1 后应提交：

1. 代码和测试。
2. 更新后的 `docs/delivery/api_spec.md`。
3. 更新后的 `docs/delivery/query_syntax.md`。
4. 更新后的 `docs/delivery/field_mapping.md`。
5. 开发说明或 commit message，说明每个补点是否完成。

测试人员按：

```text
docs/internal/stage12_1_api_compat_test_acceptance.md
```

执行验收。
