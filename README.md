# patent-search-2.0

基于 FastAPI 和 OpenSearch 的自托管专利检索服务，提供 HTTP API、MCP 工具、内部检索控制台和管理员看板。

## 部署要求

需要自行准备 OpenSearch、符合字段契约的专利数据和查询向量服务。部署前请完成[部署核对清单](docs/ops/deployment_checklist.md)。

## 提供的能力

- `POST /api/patent/search`：布尔、向量、布尔与向量混合检索；支持分页和日期排序。
- 专利详情、引证、法律状态历史三个读取接口。
- 标题、摘要、首权、完整权要、说明书、独权、从权及 `tscd` 的逐叶 CN/EN 查询路由；支持 `inventor:` 发明人查询。
- MCP 提供 6 个工具，通过 HTTP API 访问服务，不直接查询 OpenSearch。
- `/console/`：内部检索界面；`/admin/`：运行指标、日志、配置草案及受限的单实例参数应用/回滚。
- `/live`、`/startup`、`/ready` 探针及 `/metrics` 指标。

HTTP API 使用 `X-API-Key`，MCP 使用 Bearer Token，Console 和管理员入口分别使用 Basic 身份认证。浏览器和指标入口的网络访问边界须独立确认。

语义字段公开名为 `abstract`、`main_claim`、`independent_claims`，对应 `*Vector1024` 字段。字段需要自行建立 mapping 并写入向量；具体可用性取决于部署环境的数据覆盖。

## 本地开始

使用 Python 3.11；`make check` 的前端检查还需要 Node.js。

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
make check
```

启动前按 `.env.example` 填写本环境的 OpenSearch、鉴权及三个必填并发准入参数。语义检索另需查询向量 API Key 和模型 endpoint 配套配置；示例中的空值不能直接用于启动。

```sh
make run
```

本地 `.env`、令牌和真实运行数据不提交到仓库。部署方式见[部署手册](docs/ops/deployment_runbook.md)。

## 目录与文档

| 路径 | 用途 |
|---|---|
| `app/` | FastAPI、查询解析、DSL、OpenSearch 读取、响应映射及控制台。 |
| `mcp_server/` | MCP 传输和 HTTP 客户端。 |
| `tests/` | 单元、契约及前端测试。 |
| `benchmarks/` | 受控性能与路由验证工具；历史测量不代表当前容量。 |
| `scripts/` | smoke 和运维检查工具。 |
| `deployment/` | systemd、Prometheus、告警及 OpenSearch 排序管道资产。 |
| `docs/` | 接口、开发、运行核对与运维说明。 |

- [项目总览](PROJECT_OVERVIEW.md)：架构、职责和管理边界。
- [接口文档](docs/api.md)：HTTP/MCP 参数、查询语法、返回值及错误。
- [向量与混合检索](docs/vector_search.md)：字段、融合及分页语义。
- [开发说明](docs/development.md)与[完整文档目录](docs/README.md)。
