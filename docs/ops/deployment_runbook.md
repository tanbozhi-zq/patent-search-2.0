# 专利检索服务部署手册

## 运行组件

| 组件 | systemd 服务 | 模板监听位置 |
|---|---|---|
| FastAPI | `patent-search-service.service` | `0.0.0.0:8000` |
| HTTP MCP | `patent-mcp.service` | `0.0.0.0:9000/mcp` |
| Prometheus | `patent-search-prometheus.service` | loopback，端口由部署配置提供 |

FastAPI 是检索读入口，MCP 经 HTTP 调用 FastAPI。unit、进程参数和真实入口以目标环境为准；模板中的端口由 `ExecStart` 指定，修改 `SERVICE_PORT` 不会改变 systemd 的监听端口。

## 配置约束

全部配置项及示例默认值见 [.env.example](../../.env.example)，启动校验见 [Settings](../../app/core/config.py)。真实值仅保存在受限环境文件或密钥管理系统中。

| 配置 | 必要约束 |
|---|---|
| API / Console | `ENABLE_AUTH=true` 时需提供 `API_TOKEN`、`CONSOLE_USERNAME`、`CONSOLE_PASSWORD`；Console 密码不得等于 API Token。Basic 凭据须为可打印 ASCII，用户名不能含冒号。 |
| OpenSearch | 配置连接、鉴权及读目标；服务读目标建议使用稳定 alias。 |
| 并发准入 | 三个 `PATENT_SEARCH_*BULKHEAD*` 参数必填；`1 <= heavy < global <= pool`，获取等待大于 0 且不超过 0.1 秒。每个 worker 独立计数，扩容须核算总并发。 |
| 请求预算 | `OPENSEARCH_TIMEOUT_SECONDS`、`PATENT_SEARCH_DEADLINE_SECONDS` 均在 1–240 秒内；依赖单次 timeout 不高于请求 deadline。 |
| 查询向量 | API Key 与一个 endpoint 配置配套提供；`QUERY_VECTOR_MODEL_ENDPOINT` 与 `QUERY_VECTOR_MODEL_ENDPOINTS` 不能同时设置。 |
| MCP | 配置 `PATENT_SEARCH_BASE_URL`、`PATENT_SEARCH_API_TOKEN`、`MCP_ACCESS_TOKEN`。等待时间至少 245 秒；并发上限是正整数，不应超过其访问的 FastAPI 实例全局容量。 |
| 运行身份 | 提供 `SERVICE_RELEASE_COMMIT`、`SERVICE_RELEASE_TAG`、`SERVICE_INSTANCE_ID`，用于核对运行代码和监控实例。 |

API Token、MCP Token 和 Basic 凭据分别管理。Console 与 Admin 默认使用独立凭据；明确选择共用时，两者视为同一已授权身份。启用管理入口的详细约束见[管理员手册](admin_dashboard.md)。

## 代码部署

1. 从通过检查的提交构建部署版本；版本管理规则见[开发规范](../development.md#版本号)。固定完整提交和对应 tag，不移动已发布 tag。
2. 首次部署时按 [FastAPI unit](../../deployment/patent-search-service.service) 和 [MCP unit](../../deployment/patent-mcp.service) 准备服务用户、目录、venv 和受限环境文件，核对 unit 路径及网络访问控制。
3. 在服务器 `/opt/patent-search-service` 核对工作树，切换到目标提交；保存旧提交、环境配置和可用回滚点。
4. 同步部署身份和配置，安装依赖，重启两个服务：

```sh
.venv/bin/pip install -r requirements.txt
sudo systemctl restart patent-search-service.service
sudo systemctl restart patent-mcp.service
systemctl is-active patent-search-service.service patent-mcp.service
```

5. 检查下面的探针，再用实际凭据调用搜索、详情、引证、法律历史及 MCP。`/openapi.json` 版本和 `/metrics` 的 `patent_search_build_info` 应与目标提交一致。
6. 观察错误、拒绝率、请求延迟及日志；记录部署结果和回滚入口。

涉及共享 Search Pipeline 的新增配置应先完成[管道配置](#排序管道)，再部署引用它的代码。

## 部署探针

| 路径 | 成功返回 | 用途 |
|---|---|---|
| `/live` | `{"status":"live"}` | 进程可响应，不访问 OpenSearch |
| `/startup` | `{"status":"started"}` | 启动初始化完成 |
| `/ready` | `{"status":"ready"}` | 读目标可访问，可接收流量 |
| `/health` | `data.status="healthy"` | 兼容存活检查 |

启动未完成时 `/startup` 返回 503；依赖不可用时 `/ready` 返回 503，已经启动的进程仍可通过存活和启动探针。readiness 使用独立连接、线程和短缓存，只检查读目标存在，不执行业务检索。

```sh
curl -fsS http://127.0.0.1:8000/live
curl -fsS http://127.0.0.1:8000/startup
curl -fsS http://127.0.0.1:8000/ready
.venv/bin/python scripts/smoke_health.py http://127.0.0.1:8000
```

探针与 `/metrics` 不要求业务 Token，应由网络或代理限制在可信入口。业务成功仍需用真实接口确认。

## 监控与管理员入口

- 指标、告警规则、Grafana 和多实例聚合见[可观测性](observability.md)。
- Prometheus 安装、存储预算、检查和备份见[Prometheus 手册](internal_prometheus.md)。
- 管理员鉴权、日志权限、草案和运行时参数变更见[管理员手册](admin_dashboard.md)。

仅修改 Prometheus 的配置或抓取目标不需要重启检索服务。管理员参数热应用只覆盖其明确列出的四项参数；其他配置按正常部署流程生效。

## 排序管道

定义唯一来源是 [search_pipelines_v1.json](../../deployment/opensearch/search_pipelines_v1.json)。单字段向量不需要管道，多字段向量和混合检索需要对应管道。

1. 确认 OpenSearch 版本兼容、读目标和回滚点，并固定资产文件摘要。
2. 对每个 ID 执行 `GET /_search/pipeline/<id>`；已有定义必须与资产规范化比较一致，发现差异时停止，不覆盖未知配置。
3. 仅为返回 404 的 ID 执行 `PUT`，请求体使用资产中对应定义，并记录新建 ID。
4. 独立 GET 回读 processor、分支权重和 `rank_constant`，再验证单向量、多向量、混合、分页及日期排序。

缺失或失败的管道返回依赖错误，不降级为单分支或原始分数。回滚时先恢复不再引用新管道的兼容代码，仅删除本次新建、回读仍一致且已无引用的管道。

## 索引切换

读别名、数据对齐、字段兼容、设置变更和索引回滚统一按[索引切换手册](index_cutover.md)执行。

仅切换同一 alias 的物理目标不需要为加载索引名重启服务；修改 `OPENSEARCH_INDEX` 才需按正常部署流程重启。两种操作均要验证 HTTP 和 MCP。

## 日志

应用以单行 JSON 输出到 stdout/stderr，由 `patent-search` journald namespace 接管。部署时关闭 Uvicorn 默认 access log；事件只记录路由模板及受控字段，使用 `request_id` 关联链路。

```sh
systemctl status patent-search-service.service patent-mcp.service
journalctl --namespace=patent-search -u patent-search-service.service -n 100 --no-pager -o cat
journalctl --namespace=patent-search -u patent-mcp.service -n 100 --no-pager -o cat
```

首次启用独立 namespace 需 systemd 245 或以上，并安装保留策略：

```sh
sudo install -D -m 0644 deployment/journald/60-patent-search-retention.conf /etc/systemd/journald@patent-search.conf.d/60-retention.conf
sudo systemctl daemon-reload
sudo systemctl restart systemd-journald@patent-search.service
journalctl --namespace=patent-search --disk-usage
```

磁盘和保留上限以[日志配置](../../deployment/journald/60-patent-search-retention.conf)为准。管理员读取此 namespace 的最小权限安装步骤见[日志范围与权限](admin_dashboard.md#日志范围与权限)。本地开发默认只写终端，需要持久化时使用带轮转的采集方式。

日志不保存密码、Token、Cookie、Authorization、完整查询、完整正文或原始依赖错误。request ID 不作为指标 label 或授权依据。

## 控制台检查

通过受 TLS 和可信网络保护的 `<INTERNAL_BASE_URL>/console/` 访问，使用独立 Console Basic 凭据；同源 Console API 沿用浏览器认证。第三方程序调用 `/api/patent/*`。

1. 匿名请求应返回 401；认证后检查布尔、向量、混合检索、分页和详情。
2. 检查申请日/公开日的升降序、空分数显示以及引证和法律历史。
3. 检查页面源码、浏览器存储及日志不持久化凭据、完整查询和正文；业务数据按纯文本渲染。
4. 确认未受保护的端口无法从非可信网络访问；应用认证不能替代网络访问控制。

## 回滚

代码回滚使用预先确认的完整提交和配套环境，核对字段、向量维度、模型及管道与当前索引兼容，再重启并复验。不要固定回滚到某个历史版本或索引名称。

索引回滚按索引切换手册原子恢复读目标；仅恢复同一 alias 的物理目标不额外重启服务。保留旧版本和备份，直到业务检查及观察完成。
