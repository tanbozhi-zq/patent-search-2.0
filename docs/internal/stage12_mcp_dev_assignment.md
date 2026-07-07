# Stage 12 MCP Server 后续开发派工单

## 1. 阶段入口

MCP Server 不作为 Stage 12 的第一开发任务。只有在 DeerFlow Tool 完成本地 smoke 和 Flow / DeerFlow 主链路联调后，项目总控确认进入 MCP 阶段，开发人员才开始本文档任务。

进入条件：

1. `patent_search -> patent_get_detail -> patent_get_citations` Tool 主链路通过。
2. `docs/internal/stage12_3_deerflow_integration_plan.md` 已记录真实联调结果，并由项目总控确认可以进入 MCP 阶段。
3. DeerFlow Tool 字段缺口和错误转换问题已关闭或有明确豁免。
4. 项目总控确认 MCP Server 不直接查 OpenSearch。

## 2. 开发目标

新增 MCP Server，把同一套自研专利检索 API 封装为 MCP tools，供支持 MCP 的客户端或 Flow / DeerFlow MCP 接入方式调用。

## 3. 目录与边界

预计新增：

```text
mcp_server/
├── server.py
├── patent_api_client.py
├── settings.py
├── README.md
└── examples/
```

边界：

1. `mcp_server/` 只负责 MCP 协议封装。
2. MCP Server 通过 HTTP 调用 `patent-api.service`。
3. MCP Server 不读取 OpenSearch 配置。
4. MCP Server 不创建 OpenSearch client。
5. MCP Server 与 DeerFlow Tool 返回业务字段保持一致。
6. stdio 优先，Streamable HTTP 后续按部署条件补充。

## 4. MCP Tools 清单

| Tool | 入参 | 说明 |
|---|---|---|
| `patent_search` | `q`, `ds`, `page`, `page_size`, `sort`, `highlight` | 专利检索 |
| `patent_get_detail` | `patent_id`, `include_description` | 专利详情 |
| `patent_get_citations` | `patent_id` | 引证信息 |
| `patent_get_legal_history` | `patent_id` | 法律状态历史基础结构 |

## 5. 配置项

```bash
PATENT_SEARCH_BASE_URL=http://127.0.0.1:8000
PATENT_SEARCH_API_TOKEN=实际token
PATENT_SEARCH_TIMEOUT_SECONDS=30
```

## 6. 开发自查要求

开发人员需补充或说明：

1. MCP tools list 自查。
2. MCP `patent_search` 调用自查。
3. MCP `patent_get_detail` 调用自查。
4. MCP `patent_get_citations` 调用自查。
5. MCP `patent_get_legal_history` 调用自查。
6. API 错误到 MCP tool 错误的转换自查。

## 7. 交付文档

MCP Server 完成本地测试后，补充：

```text
docs/delivery/mcp_integration_guide.md
```

文档需包含：

1. MCP Server 启动方式。
2. 环境变量。
3. tools 清单。
4. 入参说明。
5. 返回字段说明。
6. stdio 接入示例。
7. Streamable HTTP 接入规划。
8. smoke 自查记录。
9. 部署和回滚说明。

## 8. 联通结论

日期：2026-07-07

结论：Stage 12.4 MCP stdio 联通成功。

项目负责人已确认 MCP 联通成功。项目总控据此关闭 Stage 12.4 stdio MCP 开发和联通工作，并将该阶段纳入版本管理。

### 8.1 放行边界

1. 本次放行范围为 stdio MCP Server。
2. MCP Server 通过自研 HTTP API 调用专利检索能力，不直接查询 OpenSearch。
3. Streamable HTTP MCP 接入不在本次放行范围内，后续按部署条件单独规划。
4. 若后续交付环境需要 Python 3.9，需先处理 MCP SDK 对 Python `>=3.10` 的运行时要求。

### 8.2 版本结论

Stage 12.4 MCP stdio Server 可作为稳定版本点管理；后续工作进入最终交付说明、部署交接或 Streamable HTTP 扩展规划。
