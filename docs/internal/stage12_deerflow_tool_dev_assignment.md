# Stage 12.2 DeerFlow Tool 开发派工单

## 1. 角色判断

本任务单由项目总控维护，用于开发人员实现 DeerFlow / Flow tool 前确认边界。项目总控只维护需求、边界、字段映射、API 设计、查询语法清单、Git 状态和交付文档，不直接承担开发实现。

开发人员负责按本文档实现代码；测试人员负责在测试前先 review 开发提交，再执行验收与联调用例。

## 2. 开发目标

在 `Stage 12.1 核心 API 兼容补点` 通过后，新增 DeerFlow / Flow 可调用的专利检索工具封装，使工具层具备支撑 agent 完成以下主链路的能力：

```text
patent_search -> patent_get_detail -> patent_get_citations
```

工具层必须调用自研 HTTP API，不允许直接查询 OpenSearch。

## 3. 开发顺序

Stage 12 按以下顺序推进：

1. `Stage 12.1`：核心 API 兼容补点，只补必要兼容字段和查询能力，不改接口路径。
2. `Stage 12.2`：DeerFlow Tool 封装和本地 smoke，新增 `deerflow_tool/`，内部调用自研 API。
3. `Stage 12.3`：DeerFlow / Flow agent 环境联调。
4. `Stage 12.4`：DeerFlow Tool 稳定后，再进入 MCP Server 派工。

开发人员不得跳过 Tool 联调直接实现 MCP Server。

进入本派工单前必须满足：

1. `docs/internal/stage12_1_api_compat_dev_assignment.md` 已完成。
2. `docs/internal/stage12_1_api_compat_test_acceptance.md` 验收通过。
3. 项目总控确认 5 个 API 兼容补点已经关闭或有明确豁免。

## 4. 目录与边界

后续开发预计新增：

```text
deerflow_tool/
├── tools.py
├── README.md
└── examples/
```

边界要求：

1. `app/` 继续作为核心 API 服务，不依赖 `deerflow_tool/`。
2. `deerflow_tool/` 只负责 DeerFlow / Flow tool 适配。
3. Tool 通过 `PATENT_SEARCH_BASE_URL` 调用自研 API。
4. Tool 通过 `PATENT_SEARCH_API_TOKEN` 传递 `X-API-Key`。
5. Tool 不读取 OpenSearch 配置，不创建 OpenSearch client。
6. `patent_harness_base_副本/` 仅作合约参考，不修改。
7. 不修改 `对外交付文档/`，除非项目总控明确要求同步对外版本。

## 5. 复用基础

优先复用或对齐：

1. `app/integrations/patenthub_adapter.py`
2. `tests/test_patenthub_adapter.py`
3. `docs/internal/saas_patent_contract_audit.md`
4. `docs/delivery/api_spec.md`
5. `docs/delivery/field_mapping.md`

当前 `PatentHubToolAdapter` 已具备：

1. self-hosted search/detail/citations HTTP 调用。
2. `records` 到 `patents` 的工具层映射。
3. `{success: false, code, message}` 到 `{error, code}` 的错误转换。
4. `PATENT_SEARCH_*` 环境变量读取。

后续开发应避免重新实现一套字段转换逻辑，除非 DeerFlow 的工具注册形式确实需要额外包装。

## 6. 第一版工具清单

| Tool | 入参 | 自研 API |
|---|---|---|
| `patent_search` | `q`, `ds`, `page`, `page_size`, `sort`, `highlight` | `POST /api/patent/search` |
| `patent_get_detail` | `patent_id`, `include_description` | `GET /api/patent/detail/{patent_id}` |
| `patent_get_citations` | `patent_id` | `GET /api/patent/citations/{patent_id}` |
| `patent_get_legal_history` | `patent_id` | 第一版可先返回基础法律状态历史结构，后续再接正式 API |

第一版不开放：

```text
enterprise_patent_portrait
```

## 7. DeerFlow Tool 配置项

Tool 需要支持：

```bash
PATENT_SEARCH_BASE_URL=http://127.0.0.1:8000
PATENT_SEARCH_API_TOKEN=实际token
PATENT_SEARCH_USE_SELF_HOSTED=true
PATENT_SEARCH_PAGE_SIZE_LIMIT=50
PATENT_SEARCH_TIMEOUT_SECONDS=30
```

密钥不得写入 Git 文档、代码注释、测试快照或日志。

## 8. 返回结构

`patent_search` 对外返回工具层结构：

```json
{
  "total": 123,
  "page": 1,
  "page_size": 50,
  "total_pages": 3,
  "next_page": 2,
  "took_ms": 35,
  "patents": []
}
```

`patent_get_detail` 对外返回专利详情对象，至少保留：

```json
{
  "id": "cn-xxx",
  "patent_id": "cn-xxx",
  "title": "专利标题",
  "summary": "摘要",
  "application_number": "CN...",
  "document_number": "CN...",
  "legal_status": "授权",
  "current_status": "授权",
  "current_assignee": "权利人",
  "main_ipc": "H02M",
  "claims": "权利要求"
}
```

`patent_get_citations` 对外返回：

```json
{
  "patent_id": "cn-xxx",
  "cited_by": [],
  "patent_references": [],
  "non_patent_references": []
}
```

`patent_get_legal_history` 第一版返回：

```json
{
  "patent_id": "cn-xxx",
  "transaction_count": 0,
  "transactions": []
}
```

错误响应统一转换为：

```json
{
  "error": "错误说明",
  "code": 40001
}
```

核心 API 兼容增强不得在 Tool 层绕过 API 直接补 OpenSearch 查询。如发现 12.1 缺口，开发人员应退回项目总控确认，不在 Tool 层自行补底层查询。

## 9. 开发任务清单

1. 新增 `deerflow_tool/tools.py`。
2. 复用 `PatentHubToolAdapter` 或抽取共享 HTTP client，避免重复字段映射。
3. 暴露 `patent_search`、`patent_get_detail`、`patent_get_citations`、`patent_get_legal_history`。
4. 新增 `deerflow_tool/README.md`。
5. 新增 `deerflow_tool/examples/` 示例配置。
6. 新增 Tool 层测试，覆盖成功链路、错误转换、page_size 限制和环境变量。
7. 新增或更新本地 smoke 脚本，供测试人员在进入真实 Flow / DeerFlow 前验证工具函数。
8. 不接入真实 Flow / DeerFlow agent；真实联调归入 `Stage 12.3`。

### 9.1 提交建议

建议开发人员按以下粒度提交：

```text
feat: add DeerFlow patent search tools
docs: add DeerFlow tool usage guide
test: add DeerFlow tool smoke coverage
```

每次提交前运行：

```bash
.venv/bin/python -m pytest -q
```

## 10. 本地验收用例

本阶段只验收本地 Tool 封装，不验收真实 Flow / DeerFlow agent 加载。测试至少覆盖：

| 场景 | 查询式 |
|---|---|
| 普通关键词 | `阀门` |
| 标题摘要权利要求说明书 | `tscd:(阀门 AND 密封)` |
| IPC + 技术词 | `ipc:H02M AND tscd:(均衡 OR 平衡)` |
| 申请人 | `applicant:宁德时代新能源科技股份有限公司` |
| 当前权利人 | `currentAssignee:华为技术有限公司` |
| 错误查询式 | `ipc:H02M AND AND tscd:(均衡)` |

验收标准：

1. `deerflow_tool.tools` 可被本地 Python 测试导入。
2. `patent_search` 返回 `patents`，不返回 `records`。
3. `patent_search` 受 `PATENT_SEARCH_PAGE_SIZE_LIMIT` 限制。
4. `patent_get_detail` 可使用 search 返回的 `id` 查询详情。
5. `patent_get_citations` 返回 `cited_by`、`patent_references`、`non_patent_references`。
6. `patent_get_legal_history` 返回稳定基础结构。
7. 错误查询式返回 `{error, code}`。
8. 工具调用链路不依赖 PatentHub 的临时 session id。
9. Tool 层不直接查询 OpenSearch。

真实 Flow / DeerFlow agent 能否加载 tool、agent 能否自动选择 tool、agent 是否能生成分析结论，均归入：

```text
docs/internal/stage12_3_deerflow_integration_acceptance.md
```

## 11. 后续交付文档

Tool 代码开发并本地 smoke 通过后，补充：

```text
docs/delivery/deerflow_tool_integration_guide.md
```

该文档需要记录：

1. Tool 文件位置。
2. Tool 名称清单。
3. 入参说明。
4. 返回字段说明。
5. 环境变量配置。
6. 本地 smoke 测试命令与结果。
7. Flow / DeerFlow 注册示例草案。
8. 已知限制和回滚方式。

真实 Flow / DeerFlow 注册结果和端到端调用记录由 `Stage 12.3` 联调阶段补充。
