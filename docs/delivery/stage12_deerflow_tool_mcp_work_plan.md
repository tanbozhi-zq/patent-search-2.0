# Stage 12：DeerFlow Tool 联调与 MCP 交付准备工作计划

## 1. 当前目标

当前项目已经完成自研专利检索 API 的主体建设，Stage 12 不再从零开发接口，而是进入“自研 API 产品化 + Flow / DeerFlow 集成 + MCP 标准化交付”的阶段。

Stage 12 的交付目标分为两步：

1. 先完成 DeerFlow Tool 联调，让 Flow / DeerFlow 平台中的 agent 能直接调用自研专利检索能力。
2. Tool 联调稳定后，再封装 MCP 服务并进行 MCP 方式的二次联调。

项目能力边界按四层维护：

1. 自研专利检索 API：由 `app/` 提供，保持 HTTP 接口稳定。
2. PatentHub 兼容适配：由 `app/integrations/patenthub_adapter.py` 提供字段和错误转换参考。
3. DeerFlow / Flow tool 接入：由后续 `deerflow_tool/` 独立封装。
4. MCP 服务化交付：由后续 `mcp_server/` 独立封装。

Stage 12 的核心原则：

1. `app/` 只负责核心 API 能力。
2. DeerFlow Tool 和 MCP Server 都通过 HTTP 调用同一套自研 API。
3. DeerFlow Tool 和 MCP Server 不直接查询 OpenSearch。
4. `patent_harness_base_副本/` 仅作为合约参考，不做任何修改。
5. 本阶段先治理文档和交付边界，再进入工具代码开发。
6. 后续不再单独设置测试环境、测试人员派工、测试验收单或测试报告；质量门改为开发自查、项目总控 review、真实联调记录和交付文档复核。

## 2. 阶段一：自研接口部署与基础验证

### 2.1 工作任务

1. 将自研专利检索服务部署到服务器。
2. 配置服务运行环境变量。
3. 配置 OpenSearch 连接信息。
4. 配置 `X-API-Key` 鉴权 token。
5. 启动 FastAPI / Uvicorn 服务。
6. 确认服务可被 Flow / DeerFlow 所在环境访问。
7. 确认 search 返回 `total_pages`、`next_page`、`took_ms` 等 Stage 12 所需兼容字段的补齐计划。
8. 确认 `agency` / `agent` 检索、裸 IPC 自动识别、法律状态历史基础能力的实施边界。

### 2.2 需要验证的接口

1. `GET /health`
2. `POST /api/patent/search`
3. `GET /api/patent/detail/{patent_id}`
4. `GET /api/patent/detail/{patent_id}?include_description=true`
5. `GET /api/patent/citations/{patent_id}`

### 2.3 阶段交付物

1. 服务访问地址。
2. 鉴权方式说明。
3. `X-API-Key` token 交接方式。
4. 基础接口 smoke 自查结果。
5. 部署环境说明。
6. 回滚方式。

### 2.4 放行标准

1. `/health` 返回正常。
2. `search` 能返回专利列表。
3. `search` 返回的 `id` 能继续查询详情。
4. `detail` 默认返回权利要求 `claims`。
5. `include_description=true` 时返回 `description`。
6. `citations` 返回 `cited_by`、`patent_references`、`non_patent_references`。
7. 鉴权错误、查询语法错误、专利不存在错误均返回稳定错误结构。

## 3. 阶段二：DeerFlow Tool 封装

### 3.1 工作任务

1. 基于当前自研服务封装 DeerFlow 可调用的 tool。
2. 工具名优先沿用 PatentHub 原有工具名。
3. tool 内部通过 HTTP 调用自研服务。
4. tool 对外返回 PatentHub 工具层风格的数据结构。
5. 准备 Flow / DeerFlow 配置示例。

### 3.2 第一版工具清单

1. `patent_search`
2. `patent_get_detail`
3. `patent_get_citations`
4. `patent_get_legal_history`

### 3.3 法律历史第一版边界

`patent_get_legal_history` 第一版可先返回基础法律状态历史结构：

```json
{
  "patent_id": "xxx",
  "transaction_count": 0,
  "transactions": []
}
```

### 3.4 暂不开放工具

1. `enterprise_patent_portrait`

### 3.5 Tool 配置项

需要支持以下环境变量：

```bash
PATENT_SEARCH_BASE_URL=http://服务器IP:8000
PATENT_SEARCH_API_TOKEN=实际token
PATENT_SEARCH_USE_SELF_HOSTED=true
PATENT_SEARCH_PAGE_SIZE_LIMIT=50
PATENT_SEARCH_TIMEOUT_SECONDS=30
```

### 3.6 Tool 返回结构要求

`patent_search` 对外返回：

```json
{
  "total": 123,
  "page": 1,
  "page_size": 50,
  "patents": []
}
```

`patent_get_detail` 对外返回：

```json
{
  "id": "xxx",
  "patent_id": "xxx",
  "title": "...",
  "summary": "...",
  "application_number": "...",
  "document_number": "...",
  "legal_status": "...",
  "current_status": "...",
  "current_assignee": "...",
  "main_ipc": "...",
  "claims": "..."
}
```

`patent_get_citations` 对外返回：

```json
{
  "patent_id": "xxx",
  "cited_by": [],
  "patent_references": [],
  "non_patent_references": []
}
```

### 3.7 阶段交付物

1. DeerFlow tool 代码。
2. `docs/internal/stage12_deerflow_tool_dev_assignment.md`。
3. `docs/delivery/deerflow_tool_integration_guide.md`。
4. Tool 名称、参数、返回字段说明。
5. Flow / DeerFlow 配置示例。
6. Tool smoke 自查脚本或自查记录。

### 3.8 放行标准

1. Flow / DeerFlow 能加载 tool。
2. agent 能调用 `patent_search`。
3. agent 能使用 search 返回的 `id` 调用 `patent_get_detail`。
4. agent 能调用 `patent_get_citations`。
5. tool 错误响应能转换为 `{error, code}`。
6. 工具调用链路不依赖 PatentHub 的 60 分钟临时 session id。

## 4. 阶段三：DeerFlow 端到端联调

### 4.1 工作任务

1. 将 tool 注册到 Flow / DeerFlow 平台。
2. 配置联调 agent。
3. 使用自研 tool 替换或并行对比原 PatentHub tool。
4. 跑完整专利检索分析流程。
5. 记录工具调用日志、输入参数、返回字段和异常信息。
6. 对比自研结果与 PatentHub 结果。

### 4.2 联调场景

1. 普通关键词检索：`阀门`
2. 标题摘要权利要求说明书检索：`tscd:(阀门 AND 密封)`
3. IPC + 技术词检索：`ipc:H02M AND tscd:(均衡 OR 平衡)`
4. 申请人检索：`applicant:宁德时代新能源科技股份有限公司`
5. 当前权利人检索：`currentAssignee:华为技术有限公司`
6. 错误查询式：`ipc:H02M AND AND tscd:(均衡)`

### 4.3 重点检查项

1. agent 是否正确选择专利检索工具。
2. tool 入参是否符合预期。
3. search 是否返回可用 `patents`。
4. `patents[0].id` 是否能继续查详情。
5. detail 是否包含 `claims`。
6. `include_description=true` 是否能返回 `description`。
7. citations 是否能稳定返回。
8. agent 是否能继续生成专利分析结论。
9. 自研检索首页结果与 PatentHub 首页结果的重合度。
10. 自研检索排序与 PatentHub 排序的主要差异。

### 4.4 阶段交付物

1. DeerFlow 联调记录。
2. 端到端流程测试结果。
3. 问题清单。
4. 字段缺口清单。
5. 检索效果对比记录。
6. 是否进入 MCP 封装阶段的确认结论。

### 4.5 放行标准

1. Flow agent 能完成 search -> detail -> citations 主链路。
2. 主流程无工具调用异常。
3. 关键字段满足 agent 分析需要。
4. 检索结果能支撑后续分析。
5. 已确认需要补充的字段和排序差异。

## 5. 阶段四：MCP 服务封装

### 5.1 工作任务

1. 新增 `mcp_server/` 目录。
2. 封装 MCP server。
3. MCP server 内部继续调用自研 FastAPI 服务。
4. 暴露与 DeerFlow tool 同名或同义的 MCP tools。
5. 准备 stdio 和 Streamable HTTP 两种启动方式。
6. 编写 MCP 接入说明。
7. 编写 MCP smoke 自查命令。

### 5.2 MCP tools 清单

1. `patent_search`
2. `patent_get_detail`
3. `patent_get_citations`
4. `patent_get_legal_history`

### 5.3 MCP 服务配置项

```bash
PATENT_SEARCH_BASE_URL=http://服务器IP:8000
PATENT_SEARCH_API_TOKEN=实际token
PATENT_SEARCH_TIMEOUT_SECONDS=30
```

### 5.4 MCP 启动方式

第一版支持 stdio：

```bash
python mcp_server/server.py
```

后续支持 Streamable HTTP：

```text
http://服务器IP:9000/mcp
```

### 5.5 阶段交付物

1. `mcp_server/` 源码。
2. `docs/delivery/mcp_integration_guide.md`。
3. MCP tools 参数说明。
4. MCP tools 返回字段说明。
5. stdio 接入示例。
6. Streamable HTTP 接入示例。
7. MCP smoke 自查记录。
8. MCP 部署说明。

### 5.6 放行标准

1. MCP client 能连接 server。
2. MCP client 能列出 tools。
3. MCP client 能调用 `patent_search`。
4. MCP client 能调用 `patent_get_detail`。
5. MCP client 能调用 `patent_get_citations`。
6. 错误响应稳定。
7. 与 DeerFlow tool 阶段验证过的业务结果保持一致。

## 6. 阶段五：MCP 联调与最终交付

### 6.1 工作任务

1. 将 MCP server 部署到目标环境。
2. 将 MCP server 接入 Flow / DeerFlow 或目标 MCP client。
3. 复用 DeerFlow Tool 阶段的联调场景。
4. 跑 MCP 方式端到端流程。
5. 对比 Tool 方式与 MCP 方式的调用结果。
6. 修复 MCP 封装层问题。
7. 输出最终交付文档。

### 6.2 阶段交付物

1. MCP 联调记录。
2. MCP 接入配置。
3. MCP 端到端联调结果。
4. 服务部署说明。
5. 回滚说明。
6. 最终交付说明。

### 6.3 放行标准

1. MCP 方式能完成 search -> detail -> citations 主链路。
2. MCP 返回字段满足 agent 分析需要。
3. MCP 与 Tool 方式结果一致或差异可解释。
4. 服务异常、鉴权异常、查询语法异常均可识别。
5. 对方可根据文档独立完成接入配置。

## 7. 总体交付清单

1. 自研专利检索服务部署地址。
2. 自研服务鉴权 token。
3. DeerFlow tool 代码。
4. DeerFlow tool 配置说明。
5. DeerFlow 联调记录。
6. MCP server 代码。
7. MCP server 配置说明。
8. MCP 联调记录。
9. 接口字段说明。
10. 常用检索式样例。
11. 错误码说明。
12. 部署说明。
13. 回滚说明。
14. 最终交付说明。

## 8. 当前下一步任务

1. 在 `feature/stage-12-deerflow-tool-mcp` 分支开展 Stage 12 工作。
2. 完成 `docs/` 文档分层治理和 `docs/README.md` 文档索引。
3. 生成 DeerFlow Tool 开发任务单，明确工具封装边界、字段契约和放行标准。
4. 确认自研服务本地自查通过。
5. 确认服务器部署环境和 Flow / DeerFlow 可访问路径。
6. 部署或复用自研 API 服务并执行 smoke 自查。
7. 开发 DeerFlow Tool，先跑通 search -> detail -> citations 主链路。
8. DeerFlow Tool 联调通过后，再启动 MCP Server 开发。

## 9. Git 与提交边界

Stage 12 分支：

```text
feature/stage-12-deerflow-tool-mcp
```

建议提交顺序：

1. `docs: add Stage 12 DeerFlow tool and MCP work plan`
2. `docs: organize project documentation structure`
3. `feat: add DeerFlow patent search tools`
4. `feat: add patent search MCP server`
5. `chore: add integration smoke self-checks and delivery guide`

本轮工程治理只执行前两个文档类提交，不提交 `agents.md`、`frontend/index.html`、`会议记录/` 等与 Stage 12 文档治理无关的已有工作区变更。
