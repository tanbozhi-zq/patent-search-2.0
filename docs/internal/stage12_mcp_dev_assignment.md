# Stage 12 MCP Server 远程 HTTP 服务化开发派工单

## 1. 阶段入口

项目总控已确认公司真实 DeerFlow / 工作台支持 `type: "http"` 的远程 MCP Server，且真实接入路径为公网访问。本阶段直接进入远程 HTTP MCP 服务化，不再依赖本地 Tool 插件联调作为前置条件。

进入条件：

1. 自研 FastAPI 专利检索服务已具备稳定 HTTP API。
2. stdio MCP Server 已验证可列出和调用核心专利工具。
3. 公司真实 DeerFlow / 工作台服务器可以访问我方 MCP 服务地址。
4. 项目总控确认 MCP Server 不直接查 OpenSearch。

## 2. 开发目标

把同一套自研专利检索 API 封装为远程 HTTP MCP Server，供公司真实 DeerFlow / 工作台通过公网 MCP URL 调用。

目标启动方式：

```bash
python mcp_server/server.py --transport stdio
python mcp_server/server.py --transport http --host 0.0.0.0 --port 9000
```

HTTP MCP 入口：

```text
/mcp
```

## 3. 目录与边界

当前目录：

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
5. MCP Server 不作为外部 Python 插件包被公司工作台 import。
6. 当前 `mcp_server/patent_api_client.py` 可继续复用同仓库内的 `app.integrations.patenthub_adapter`。
7. 如未来需要独立包交付，再单独解耦 `app.*` 依赖。

## 4. MCP Tools 清单

| Tool | 入参 | 说明 |
|---|---|---|
| `patent_search` | `q`, `ds`, `page`, `page_size`, `sort`, `highlight` | 专利检索 |
| `patent_get_detail` | `patent_id`, `include_description` | 专利详情 |
| `patent_get_citations` | `patent_id` | 引证信息 |
| `patent_get_legal_history` | `patent_id` | 法律状态历史基础结构 |

不新增 FTO、异步 `task_id`、报告生成、企业画像或 MCP prompts。

## 5. 配置项

MCP 调 FastAPI：

```bash
PATENT_SEARCH_BASE_URL=http://127.0.0.1:8000
PATENT_SEARCH_API_TOKEN=
PATENT_SEARCH_TIMEOUT_SECONDS=30
PATENT_SEARCH_PAGE_SIZE_LIMIT=50
```

公司工作台调 MCP：

```bash
MCP_ACCESS_TOKEN=
```

`PATENT_SEARCH_API_TOKEN` 与 `MCP_ACCESS_TOKEN` 不能混用。仓库只允许提交 `.env.example` 或文档示例，不允许提交真实 `.env` 或真实 token。

## 6. 鉴权要求

HTTP MCP 请求必须携带：

```text
Authorization: Bearer <MCP_ACCESS_TOKEN>
```

验收要求：

1. HTTP 模式未配置 `MCP_ACCESS_TOKEN` 时启动失败。
2. 未带 token 的请求被拒绝。
3. token 错误的请求被拒绝。
4. token 正确时可进入 MCP `tools/list` 和工具调用。
5. stdio 模式不要求 `MCP_ACCESS_TOKEN`。

## 7. 开发自查要求

开发人员需补充或说明：

1. 单元测试结果。
2. MCP stdio tools list 自查。
3. MCP stdio `patent_search`、detail、citations、legal history 调用自查。
4. HTTP MCP 启动自查。
5. HTTP MCP 鉴权自查。
6. HTTP MCP tools list 自查。
7. HTTP MCP 主链路调用自查。
8. API 错误到 MCP tool 错误的转换自查。

## 8. 交付文档

开发完成后更新：

```text
docs/delivery/mcp_integration_guide.md
mcp_server/README.md
```

文档需包含：

1. MCP Server stdio 和 HTTP 启动方式。
2. 环境变量。
3. HTTP 鉴权方式。
4. tools 清单。
5. 入参说明。
6. 返回字段说明。
7. 公司工作台 `type: "http"` 接入示例。
8. smoke 自查命令。
9. 部署和回滚说明。

## 9. 放行边界

只有满足以下条件，才允许进入公司真实工作台联调：

1. 本地 Tool 插件路径已从当前交付主线移除。
2. MCP Server 通过自研 HTTP API 调用专利检索能力。
3. MCP Server 不直接查询 OpenSearch。
4. HTTP MCP 未鉴权访问会被拒绝。
5. 带正确 token 的 `tools/list` 和四个工具调用可用。
6. 若后续交付环境需要 Python 3.9，需先处理 MCP SDK 对 Python `>=3.10` 的运行时要求。
