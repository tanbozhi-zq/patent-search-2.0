# Stage 12：远程 HTTP MCP 服务化交付工作计划

## 1. 当前目标

当前项目已经完成自研专利检索 API 主体建设和 stdio MCP 验证。Stage 12 当前交付主线收敛为：

1. `app/` 提供 FastAPI 专利检索 HTTP 服务。
2. `mcp_server/` 提供远程 HTTP MCP Server。
3. 公司真实 DeerFlow / 工作台通过公网 MCP URL 接入。

本地 Tool 插件路径不作为当前交付范围，不再继续维护。

## 2. 目标调用链路

```text
公司真实 DeerFlow / 工作台
    -> HTTP MCP，公网访问
    -> http://服务器公网IP:9000/mcp 或 https://域名/mcp
    -> patent-mcp.service
    -> http://127.0.0.1:8000
    -> patent-api.service
    -> OpenSearch 专利索引
```

最终至少运行两个服务：

| 服务 | 职责 | 默认监听 |
|---|---|---|
| `patent-api.service` | FastAPI 专利检索服务 | `127.0.0.1:8000` 或部署需要的地址 |
| `patent-mcp.service` | 远程 HTTP MCP Server | `0.0.0.0:9000` |

## 3. 边界原则

1. `app/` 是核心 FastAPI 检索服务。
2. `mcp_server/` 只负责 MCP 协议封装。
3. MCP Server 只能通过 HTTP 调用 FastAPI 服务。
4. MCP Server 不读取 OpenSearch 配置，不创建 OpenSearch client，不直接访问 OpenSearch。
5. `patent_harness_base_副本/` 仅作为本地只读参考，不作为部署对象。
6. 不新增 FTO、异步 `task_id`、查新报告、企业画像或复杂 Prompt 工作流。
7. 不提交真实 token、OpenSearch 密码、服务器密码或生产 `.env`。
8. 第一版远程 MCP 以稳定接入为目标，采用静态 Header Token；OAuth 作为后续增强。

## 4. MCP 服务化任务

### 4.1 启动方式

保留 stdio 模式，新增 HTTP 模式：

```bash
python mcp_server/server.py --transport stdio
python mcp_server/server.py --transport http --host 0.0.0.0 --port 9000
```

项目 CLI 对外使用 `http`，内部按当前 MCP SDK `mcp==1.28.1` 映射到 Streamable HTTP transport。

### 4.2 HTTP 入口

HTTP MCP 对外入口为：

```text
/mcp
```

示例：

```text
http://服务器公网IP:9000/mcp
https://域名/mcp
```

### 4.3 Tools 清单

第一版只暴露四个工具：

1. `patent_search`
2. `patent_get_detail`
3. `patent_get_citations`
4. `patent_get_legal_history`

## 5. 鉴权设计

第一版采用 Header Token，避免公网裸露。

环境变量：

```bash
MCP_ACCESS_TOKEN=
```

请求头：

```text
Authorization: Bearer <MCP_ACCESS_TOKEN>
```

要求：

1. HTTP 模式启动时必须配置 `MCP_ACCESS_TOKEN`。
2. 未带 token 或 token 错误的请求必须拒绝。
3. stdio 模式不要求 `MCP_ACCESS_TOKEN`。
4. 后续如接入 OAuth 或网关鉴权，应作为增强项单独规划。

## 6. MCP 到 FastAPI 的调用方式

MCP Server 内部继续调用 FastAPI：

```text
POST /api/patent/search
GET  /api/patent/detail/{patent_id}
GET  /api/patent/citations/{patent_id}
GET  /api/patent/legal-history/{patent_id}
```

环境变量：

```bash
PATENT_SEARCH_BASE_URL=http://127.0.0.1:8000
PATENT_SEARCH_API_TOKEN=
PATENT_SEARCH_TIMEOUT_SECONDS=30
PATENT_SEARCH_PAGE_SIZE_LIMIT=50
```

`PATENT_SEARCH_API_TOKEN` 用于 MCP 调 FastAPI，`MCP_ACCESS_TOKEN` 用于公司工作台调 MCP，两个 token 不混用。

## 7. 部署任务

新增：

```text
deployment/patent-mcp.service
```

建议运行参数：

```text
WorkingDirectory=/opt/patent-search-service
EnvironmentFile=/opt/patent-search-service/.env
ExecStart=/opt/patent-search-service/.venv/bin/python mcp_server/server.py --transport http --host 0.0.0.0 --port 9000
```

公网第一阶段可先通过 IP + 端口完成联调。正式化时建议：

1. `patent-api.service` 只监听 `127.0.0.1:8000`。
2. `patent-mcp.service` 监听 `0.0.0.0:9000`。
3. 防火墙只向公司工作台服务器来源开放 9000。
4. 如需正式域名，再由 Nginx + HTTPS 反向代理到 `127.0.0.1:9000/mcp`。

## 8. Smoke 验证

保留 stdio smoke：

```bash
python scripts/smoke_mcp_server.py http://127.0.0.1:8000 "$API_TOKEN"
```

新增 HTTP MCP smoke：

```bash
python scripts/smoke_mcp_http.py http://服务器公网IP:9000/mcp "$MCP_ACCESS_TOKEN"
```

HTTP smoke 至少验证：

1. MCP HTTP 连接成功。
2. `tools/list` 能发现四个工具。
3. `patent_search` 调用成功。
4. 可从搜索结果取首个 `id` 或 `patent_id`。
5. `patent_get_detail` 调用成功。
6. `patent_get_citations` 调用成功。
7. `patent_get_legal_history` 调用成功。
8. 错误查询式稳定返回 `{error, code}`，不导致 MCP Server 崩溃。

## 9. 公司工作台接入交付物

需要给公司工作台负责人提供：

1. MCP URL。
2. 鉴权方式：`Authorization: Bearer <token>`。
3. 四个 tools 清单。
4. 配置示例。
5. smoke 结果。
6. 已知限制。
7. 回滚方式。

配置示例：

```json
{
  "mcpServers": {
    "patent-search": {
      "enabled": true,
      "type": "http",
      "url": "http://服务器公网IP:9000/mcp",
      "headers": {
        "Authorization": "Bearer ${PATENT_MCP_TOKEN}"
      },
      "description": "自研专利检索 MCP 服务"
    }
  },
  "skills": {}
}
```

## 10. Git 提交边界

按两个实现提交推进：

1. `chore: remove unused DeerFlow tool integration path`
   - 删除不再使用的本地 Tool 线代码、测试、smoke 和接入文档。
   - 更新 README/docs，把主线改成 FastAPI + 远程 HTTP MCP。
2. `feat: add remote HTTP MCP server deployment support`
   - 新增 HTTP MCP transport。
   - 新增 MCP Header Token 鉴权。
   - 新增 `patent-mcp.service`。
   - 新增 HTTP MCP smoke。
   - 更新 MCP 接入和部署文档。

## 11. 验收标准

只有满足以下条件，才认为可以进入公司真实工作台联调：

1. 本地 Tool 插件路径已从当前交付主线移除。
2. FastAPI 单元测试通过。
3. MCP stdio smoke 通过。
4. MCP HTTP 服务可启动。
5. 公网 MCP URL 可访问。
6. 未带 token 时访问被拒绝。
7. 带正确 token 时 `tools/list` 成功。
8. `patent_search` 调用成功。
9. `patent_search -> patent_get_detail -> patent_get_citations` 主链路成功。
10. `patent_get_legal_history` 调用成功。
11. 错误查询式稳定返回错误结构。
12. `.env` 未提交。
13. 真实 token 未写入文档或 Git。
14. `patent_harness_base_副本/` 未被修改。
15. 已产出公司工作台接入配置示例。
