# Stage 12 DeerFlow Tool 开发任务单

## 1. 角色判断

本任务单由项目总控维护，用于后续开发人员实现 DeerFlow / Flow tool 前确认边界。当前工程治理阶段只产出任务单，不创建 `deerflow_tool/` 代码。

开发开始前，测试人员需要先对本阶段已修改代码进行 review；若本轮只有文档变更，则测试人员 review 文档与既有实现的一致性。

## 2. 开发目标

新增 DeerFlow / Flow 可调用的专利检索工具封装，使 agent 能通过自研专利检索 API 完成：

```text
patent_search -> patent_get_detail -> patent_get_citations
```

工具层必须调用自研 HTTP API，不允许直接查询 OpenSearch。

## 3. 目录与边界

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

## 4. 复用基础

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

## 5. 第一版工具清单

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

## 6. 配置项

Tool 需要支持：

```bash
PATENT_SEARCH_BASE_URL=http://127.0.0.1:8000
PATENT_SEARCH_API_TOKEN=实际token
PATENT_SEARCH_USE_SELF_HOSTED=true
PATENT_SEARCH_PAGE_SIZE_LIMIT=50
PATENT_SEARCH_TIMEOUT_SECONDS=30
```

密钥不得写入 Git 文档、代码注释、测试快照或日志。

## 7. 返回结构

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

## 8. 核心 API 兼容补点

开发 DeerFlow Tool 前，项目总控需要确认以下核心 API 补点是否已进入本阶段开发计划：

1. `sort` 支持更多 PatentHub 风格值。
2. 支持 `agency` / `agent` 字段检索。
3. 支持裸 IPC 自动识别。
4. search 返回 `total_pages` / `next_page` / `took_ms`。
5. 增加 `legal_history` 基础能力。

这些补点属于核心 API 兼容增强，开发人员不得在 Tool 层绕过 API 直接补 OpenSearch 查询。

## 9. 验收用例

联调至少覆盖：

| 场景 | 查询式 |
|---|---|
| 普通关键词 | `阀门` |
| 标题摘要权利要求说明书 | `tscd:(阀门 AND 密封)` |
| IPC + 技术词 | `ipc:H02M AND tscd:(均衡 OR 平衡)` |
| 申请人 | `applicant:宁德时代新能源科技股份有限公司` |
| 当前权利人 | `currentAssignee:华为技术有限公司` |
| 错误查询式 | `ipc:H02M AND AND tscd:(均衡)` |

验收标准：

1. Flow / DeerFlow 能加载 tool。
2. agent 能调用 `patent_search` 并得到 `patents`。
3. agent 能使用 `patents[0].id` 调用 `patent_get_detail`。
4. agent 能调用 `patent_get_citations`。
5. 错误查询式返回 `{error, code}`。
6. 工具调用链路不依赖 PatentHub 的临时 session id。

## 10. 后续交付文档

Tool 代码开发并 smoke 通过后，补充：

```text
docs/delivery/deerflow_tool_integration_guide.md
```

该文档需要记录：

1. Tool 文件位置。
2. Tool 名称清单。
3. 入参说明。
4. 返回字段说明。
5. 环境变量配置。
6. Flow / DeerFlow 注册示例。
7. smoke 测试命令与结果。
8. 已知限制和回滚方式。
