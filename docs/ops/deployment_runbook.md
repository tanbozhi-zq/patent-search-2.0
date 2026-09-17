# 专利检索服务部署与索引切换手册

本文定义发布操作与验收要求；部署前请完成[部署核对清单](deployment_checklist.md)。下列示例值不能代替目标环境的实际配置。

## 1. 运行组件

| 组件 | systemd 服务 | 本地端口 | 鉴权 |
|---|---|---:|---|
| FastAPI 检索服务 | patent-search-service.service | 8000 | HTTP API 使用 X-API-Key；Console 使用独立 HTTP Basic |
| Streamable HTTP MCP | patent-mcp.service | 9000 /mcp | Bearer Token |
| 内部 Prometheus | patent-search-prometheus.service | 127.0.0.1:9090（模板） | loopback/受限网络 |

MCP 不直接访问 OpenSearch，而是通过 FastAPI 调用。因此更换 FastAPI 的读索引会同时影响 HTTP API 与 MCP。

## 2. 常规代码发布

发布前先完成并核对发布身份；不要移动已经发布的 tag：

1. 在 `app/version.py` 更新服务版本，并把 `CHANGELOG.md` 的对应版本从
   `Unreleased` 改为发布日期，同时保留新的空 `Unreleased` 小节。
2. 合并发布提交后，在该提交上创建并推送 annotated tag，再从同一 tag 创建
   GitHub Release。tag、GitHub Release 和 `app/version.py` 必须是同一版本。
3. 部署时固定到该 tag 对应的完整 commit；通过 `/openapi.json` 与 `/metrics` 的
   `patent_search_build_info` 核对 version、tag、commit，不能只凭健康检查判断版本。

先在发布窗口中把服务器 `/opt/patent-search-service` 的工作树切到已验证 tag 对应的提交，核对没有本地改动，并同步 `.env` 的 `SERVICE_RELEASE_COMMIT`、`SERVICE_RELEASE_TAG`。随后执行：

    git rev-parse --short HEAD
    .venv/bin/pip install -r requirements.txt
    sudo systemctl restart patent-search-service.service
    sudo systemctl restart patent-mcp.service
    systemctl is-active patent-search-service.service patent-mcp.service

发布后至少验证：

    curl -fsS http://127.0.0.1:8000/live
    curl -fsS http://127.0.0.1:8000/startup
    curl -fsS http://127.0.0.1:8000/ready
    curl -fsS http://127.0.0.1:8000/health
    .venv/bin/python scripts/smoke_health.py http://127.0.0.1:8000

真实 API Token、Console 密码、MCP Token 和 OpenSearch 凭据仅在服务器 .env 或密钥管理系统中维护。

## 3. 环境变量边界

服务认证变量如下：

    ENABLE_AUTH=true
    API_TOKEN=<managed in server environment>
    CONSOLE_USERNAME=<managed internal username>
    CONSOLE_PASSWORD=<separate managed secret; must not equal API_TOKEN>

启用鉴权时，Console 用户名和密码都是必填项；缺失任一项或 Console 密码复用
`API_TOKEN`，服务都会拒绝启动。浏览器只使用 Console Basic 凭据，后端 API Token
不会发送到浏览器。用户名和密码必须使用可打印 ASCII，用户名不得包含冒号；这与
FastAPI 内置 HTTP Basic 解析器的实际能力一致。凭据轮换后重启 FastAPI，并通知已
登录用户重新认证。

服务读取 OpenSearch 的关键变量如下：

    OPENSEARCH_HOST=<managed in server environment>
    OPENSEARCH_PORT=9200
    OPENSEARCH_USE_HTTPS=true
    OPENSEARCH_USER=<managed in server environment>
    OPENSEARCH_PASS=<managed in server environment>
    OPENSEARCH_INDEX=patent_search_read
    OPENSEARCH_VERIFY_CERTS=false
    OPENSEARCH_TIMEOUT_SECONDS=240
    OPENSEARCH_POOL_MAXSIZE=10
    OPENSEARCH_MAX_RETRIES=1
    OPENSEARCH_RETRY_BACKOFF_SECONDS=0.1
    PATENT_SEARCH_BULKHEAD_CAPACITY=<measured per-process capacity>
    PATENT_SEARCH_HEAVY_BULKHEAD_CAPACITY=<measured heavy-search sub-capacity>
    PATENT_SEARCH_BULKHEAD_ACQUIRE_TIMEOUT_SECONDS=<validated admission wait>
    PATENT_SEARCH_DEADLINE_SECONDS=240
    READINESS_TIMEOUT_SECONDS=1
    READINESS_SUCCESS_CACHE_SECONDS=2
    READINESS_FAILURE_CACHE_SECONDS=1

语义检索还需要受管的查询向量路由：

    QUERY_VECTOR_API_URL=<managed HTTPS provider URL>
    QUERY_VECTOR_API_KEY=<managed secret>
    QUERY_VECTOR_MODEL_ENDPOINT=<single current model endpoint>
    # 或者在多模型时使用受控 model -> endpoint JSON；两种方式不能同时设置
    QUERY_VECTOR_MODEL_ENDPOINTS=<managed JSON mapping>

API key 与 endpoint 配置必须同时存在或同时缺失。凭据和真实 endpoint 不进仓库、
不写入日志/指标/验收产物。当前单模型可使用单 endpoint 兼容形式；
新模型必须使用显式 model route，缺失路由时零网络 fail closed。

FastAPI 每个请求共享一份生效的总预算，默认及硬上限为 240 秒，可配置收紧。
OpenSearch 连接池默认 10，可通过配置调整；SDK 隐式重试关闭，瞬时幂等读取
只在剩余预算内按配置显式重试，最多一次。

每个 Uvicorn 进程共享一个全局应用舱壁；检索和目标排名还要经过重请求子舱壁，
详情、引证和法律状态只经过全局舱壁。重请求子容量必须大于等于 1 且严格小于全局
容量，全局容量不得高于 `OPENSEARCH_POOL_MAXSIZE`，否则服务拒绝启动。重请求先获取
子许可再获取全局许可，因此子舱壁拒绝不会占用给轻请求保留的全局容量。任一容量
满载后返回 `50301` 和 `Retry-After: 1`；等待时间必须大于 0 且不得超过 0.1 秒。
每条舱壁事件日志都记录进程实际使用的容量、层名和等待时间，容量验收必须以该
服务端日志核对发压脚本声明的部署值。
多 worker 部署的总准入上限是 worker 数乘以单进程全局容量，调整 worker 数时必须
重新核算连接池与 OpenSearch 总并发。

这三个舱壁参数都是必填项，代码没有 WIP 回退值；缺少任一项时服务拒绝启动。
部署配置必须填写经发布评审批准的值，不能直接照抄尚未通过门禁的测试候选值。
容量脚本和验收方法见 [benchmarks/capacity/README.md](../../benchmarks/capacity/README.md)；示例配置不是本环境的容量或 SLA。扩容时需重新记录最终配置，执行阶梯、轻重混合和独立过载测试，并核对应用拒绝 P99 与 OpenSearch CPU、Heap、队列、breaker/backpressure。

MCP 调用 FastAPI 的等待时间必须略大于服务总预算，才能收到服务生成的超时响应：

    PATENT_SEARCH_TIMEOUT_SECONDS=245
    MCP_MAX_CONCURRENT_TOOLS=4

每次发布前检查目标环境：`OPENSEARCH_TIMEOUT_SECONDS` 和
`PATENT_SEARCH_DEADLINE_SECONDS` 必须位于 1--240 秒，`PATENT_SEARCH_TIMEOUT_SECONDS`
应填写至少 245 的整数。前两项越界时 FastAPI 拒绝启动；MCP 超时为可解析的较小
整数时拒绝启动，缺失或非整数值则回退到 245，发布检查应额外核对原始环境值。
应先修改受管 `.env`，再按正常流程重启并分别验收 FastAPI 与 MCP。
`MCP_MAX_CONCURRENT_TOOLS` 必须是大于零的整数，并且不应高于 MCP 所访问的单个
FastAPI 实例全局舱壁容量；默认 4。MCP 满载时会立即返回可重试 `50301`，不会建立
额外等待队列。

## 3.1 部署探针

`/live`、`/startup`、`/ready` 均不要求业务 API 凭据，但只能经可信网络/
TLS 入口访问，不能将裸服务端口直接暴露给公网。旧 `/health` 保留为 liveness 兼容
入口；新部署编排必须使用下面三种不同信号：

| 探针 | 用途 | 建议周期 | 外部超时 | 建议阈值 |
|---|---|---:|---:|---|
| `/live` | 只确认进程和事件循环仍能响应；不访问 OpenSearch。 | 10 秒 | 1 秒 | 连续失败 3 次后重启。 |
| `/startup` | 等待配置、查询预算、应用舱壁和两个 OpenSearch Client 初始化完成。 | 2 秒 | 1 秒 | 最多 30 次；超限不加入流量并调查启动失败。 |
| `/ready` | 决定实例是否可加入或保留在检索流量中。 | 5 秒 | 2 秒 | 连续失败 3 次摘流量；成功 1 次可恢复。 |

`/ready` 对配置的读 alias/索引只做一次廉价的 `HEAD/exists`，不执行真实检索、
`count` 或集群健康 API。它使用独立的一条 OpenSearch 连接和一个工作线程，不占用
业务 OpenSearch 连接池、查询预算或应用舱壁。默认内部超时为 1 秒，成功结果缓存 2
秒、失败结果缓存 1 秒；缓存失效时并发请求共享一次检查，因此每个实例不会因高频
探针形成请求风暴。按网络实际情况可在 `.env` 中收紧或放宽这三个 `READINESS_*`
配置，但超时不得超过 5 秒、缓存不得超过 30 秒。

OpenSearch 故障时 `/live` 仍应为 200，`/startup` 在已完成初始化后仍为 200，只有
`/ready` 快速返回 503。关闭服务前应用会先变为 not-ready，供入口摘流量；真正的
多实例排空、canary、故障注入和回滚验收需单独验收；不能从探针成功推断这些验收已经完成。

## 3.2 指标、告警与基础面板

每个实例通过 `/metrics` 导出自己的低基数 Prometheus 指标。该入口不使用业务
API Key，不访问 OpenSearch，也不进入查询预算、业务舱壁或后续按调用方限流；它与
探针一样只能由私网或受信抓取入口访问。发布前必须确认防火墙或反向代理没有把该路径
暴露到非可信网络，并验证抓取不会增加 readiness/OpenSearch 调用：

    curl -fsS http://127.0.0.1:8000/metrics | head

部署 Prometheus 规则前执行官方离线检查和触发/恢复测试：

    promtool check rules deployment/observability/alert_rules.yml
    promtool test rules deployment/observability/alert_rules_test.yml

抓取周期、保留期、Alertmanager 接收方、Grafana 面板导入、直方图 bucket 和多实例
Counter/Histogram/Gauge 聚合口径见 `docs/ops/observability.md`。仓库告警阈值都是
canary 待校准候选值，不是已批准的生产 SLO；真实接收方凭据不得提交到仓库。
单实例内测 Prometheus 的固定版本安装、file-SD、存储预算、看板连接、备份、停止和
回滚见 `docs/ops/internal_prometheus.md`。

OPENSEARCH_INDEX 是服务的读目标，必须设置为稳定读 alias `patent_search_read`，
不得长期绑定到某个物理索引名。

## 3.3 管理员运行看板、配置草案与单实例 runtime reload

`/admin/` 和 `/admin-api/v1/*` 使用与业务 API、Console 都不同的管理员 Basic
凭据。配置、数据范围、查询上限、验收和回滚见
`docs/ops/admin_dashboard.md`。在网络/TLS 边界验收完成前只能在 loopback、预生产或明确的
私网入口启用，不能从非可信网络暴露。
新部署示例中草案能力由 `ADMIN_CONFIG_DRAFTS_ENABLED=false` 独立关闭；部署时确认实例开关。启用时将
`ADMIN_CONFIG_DATABASE_PATH` 指向 unit 的 `/var/lib/patent-search-service` StateDirectory，
不得放入 `/opt/patent-search-service` 发布目录。草案只做预检和不可变审计，不代表生效。
运行时参数应用的 `ADMIN_RUNTIME_CONFIG_ENABLED=false` 同样是新部署默认值；只有已启用草案、
可信单实例入口和明确的内测窗口才可启用。它仅允许独立文档列出的 runtime-reload 白名单，
其中 OpenSearch timeout 与请求 deadline 均不得高于 240 秒，以匹配指标桶并保持低于 MCP
至少 245 秒的实际等待时间。直方图另保留 300 秒有限桶来观测取消清理和响应收尾的轻微
超时。它不写 `.env`、systemd 或数据库运行值，也不会自动重启服务。关闭该开关并按正常发布流程
重启后，当前 override 回到部署基线而审计记录仍保留。apply/rollback 成功只证明本地快照与
readiness 回读；HTTP、Console、MCP smoke 和 Prometheus 前后观察仍必须独立记录。
生产日志卡片还需要 `requirements-admin-journal.txt` 和仓库提供的可选
systemd sysusers/tmpfiles ACL 与服务 drop-in；缺任一项时只能让日志分区降级，
不得把服务加入可读全主机日志的 `systemd-journal`/`adm`/`wheel` 组，也不得放宽
业务鉴权。具体安装顺序和权限验收见 `docs/ops/admin_dashboard.md`。

## 3.4 语义 Search Pipeline 发布

版本化定义的唯一来源是
`deployment/opensearch/search_pipelines_v1.json`。安装前必须独立核验当前环境：

1. 固定本次服务 commit 与 pipeline 文件 SHA-256，确认 OpenSearch 为 3.3.x，读 alias/物理索引和回滚点已明确。
2. 对资产中每个 ID 执行 `GET /_search/pipeline/<id>`。HTTP 404 才表示可以新建；如已存在，必须将服务端定义与当前文件规范化后精确比较。任一不同立即停止，不覆盖未知现有配置。
3. 只对预检为 404 的 ID 执行 `PUT /_search/pipeline/<id>`，请求体取自资产的对应 definition。安装列表必须留存于该次发布记录。
4. 再次 GET 每个 ID，对 processor、分支权重、`rank_constant` 和说明做精确回读。只有“PUT 成功 + 独立 GET 一致”才算安装成功。
5. 在受控环境先执行单向量、多向量和混合 smoke，核对 profile、去重、分页、四种日期排序、null score 与统一错误；随后才能进入服务 canary。

多向量/混合请求在 pipeline 缺失或失败时必须返回稳定依赖错误，
不能降级为单分支、Boolean 或未融合原始分数。回滚时先将服务恢复到不引用
这些 pipeline 的已验证版本，完成探针和 Boolean smoke；只可删除“本次发布新建、
回读仍一致、且已无任何发布引用”的 ID。预先存在的 pipeline 不属于本次回滚目标。

## 4. OpenSearch 读目标切换

不得仅修改 OPENSEARCH_INDEX 并重启服务。按以下顺序执行：

1. 确认新索引的历史、增量、更新和删除数据完整。
2. 验证 dynamic: strict 下入库没有未知字段失败。
3. 合入并验证 `ipc` 的 `IPCListBase` 层级查询、`mainIpc` 的五级主 IPC 字段查询、Type、AgencyRaw 和多语言正文查询的兼容代码。
4. 核对新索引副本、刷新与资源预算。1 副本、10s refresh 是建议服务配置；实际值需在部署环境核对。刷新或调整设置属于独立写操作，须在发布窗口明确执行并验证，不能在只读核对时顺手修改。
5. 创建并验证读 alias。
6. 若修改了 `OPENSEARCH_INDEX`，按发布流程重启 FastAPI；若只切换同一 alias 的物理目标，无需为加载索引名重启。MCP 通过 FastAPI 自动使用读目标。
7. 在 alias 上执行搜索、详情、引证、法律历史及 MCP smoke。

完整 alias、回滚和验收规则见 docs/ops/opensearch_v2_cutover.md。

## 5. 运行状态与日志

    systemctl status patent-search-service.service patent-mcp.service
    journalctl --namespace=patent-search -u patent-search-service.service -n 100 --no-pager -o cat
    journalctl --namespace=patent-search -u patent-mcp.service -n 100 --no-pager -o cat
    ss -ltnp | grep -E ':(8000|9000)[[:space:]]'

FastAPI 与 MCP 的应用事件以单行 JSON 输出到 stdout/stderr，由 systemd journal
接管；应用不再向 `/var/log/patent-search-service/*.log` 追加无轮转文件。Uvicorn
默认 access log 必须关闭，避免原始 URL 中的专利号进入日志。每个 HTTP 完成事件
使用路由模板，
例如 `/api/patent/detail/{patent_id}`，并包含 `request_id`、`method`、`status`、
`code` 和 `elapsed_ms`。OpenSearch、MCP 和舱壁事件也使用同一个 request ID。

首次部署本日志契约前确认目标机器为 systemd 245 或更新版本，然后安装仓库中的
专用 journald namespace 上限，并检查目标机器剩余空间满足配置：

    systemctl --version
    sudo install -D -m 0644 deployment/journald/60-patent-search-retention.conf /etc/systemd/journald@patent-search.conf.d/60-retention.conf
    sudo systemctl daemon-reload
    sudo systemctl restart systemd-journald@patent-search.service
    journalctl --namespace=patent-search --disk-usage

该配置只对 `patent-search` 日志 namespace 设置 1 GiB 持久化上限、256 MiB 运行时
上限、14 天最长保留、单文件 64/32 MiB 和每日轮转边界，并分别至少保留 2 GiB/
512 MiB 可用空间，不改变主机默认 journal 或其他服务的日志策略。
本地开发默认仅输出到终端，不创建持久日志文件；需要保存时必须使用受轮转约束的
采集工具，不能用无限期追加重定向替代。

可用 request ID 定位同一链路，`-o cat` 保留应用输出的原始 JSON：

    REQUEST_ID=<response X-Request-ID>
    journalctl --namespace=patent-search -u patent-search-service.service -u patent-mcp.service -o cat --no-pager | grep -F "\"request_id\":\"$REQUEST_ID\""

日志不得打印 API Token、Console 密码、MCP Token、Cookie、完整 Authorization、
完整查询、完整专利正文、原始下游错误体或 OpenSearch 节点地址。request ID 也不得
成为指标 label、文件名或任何授权依据。

### 5.1 Console 发布验收

Console 只能通过已完成 TLS/可信网络收口的 `<INTERNAL_BASE_URL>/console/`
交付给浏览器，裸 HTTP 公网地址不是合法入口。发布前后使用一个全新的浏览器会话
完成以下验收：

1. 不提供凭据打开 `/console/`，确认返回 `401 / 40101` 并出现认证挑战。
2. 输入独立 Console 凭据后打开页面，先用旧式布尔条件完成一次搜索并切换到下一页。
3. 分别执行单字段向量与多字段混合搜索，核对字段多选、`top_k`、分页、
   `search_context` 和请求日志中的 mode 与当前控件一致。
4. 在回归矩阵中覆盖申请日/公开日的升序与降序；日期排序的 null score 应显示为空，
   真实数值 0 仍显示为 0。
5. 从结果进入详情，并分别加载引证和法律历史。
6. 确认静态页面源码、响应正文及浏览器存储不泄露 `API_TOKEN` 或 Console 密码。
   浏览器认证请求头承载 Basic 凭据，需要 TLS 保护。检索请求和结果响应正常包含
   本次查询及专利内容，页面也会展示它们，但 Local Storage、Session Storage 和
   服务日志不得持久化完整查询或正文。确认旧键 `patentConsole:lastSearch` 已被删除。
7. 直接访问未纳入 TLS/可信网络边界的端口必须被网络策略阻断；网络验收不能用 Console 应用认证替代。

回滚 Console 认证时，先在入口阻断 `/console` 和 `/console-api/*`，再回滚服务；
不得把 `ENABLE_AUTH` 改为 `false`，也不得让 Console 密码复用 `API_TOKEN`。

## 6. 回滚

代码回滚必须使用本次发布记录中已验证的完整提交号，同时核对查询字段、向量维度、模型路由、pipeline 和当前数据兼容性。不能固定回滚到 `v0.10.1`：它使用旧向量字段，而 1024 维迁移后旧摘要值可能已被移除。索引回滚则必须将读 alias 原子切回实际存在、数据已对齐且已验证的物理索引。
不得假定历史名称 `patent_index` 仍然存在。两者均完成后，重启两个服务并执行：

    curl -fsS http://127.0.0.1:8000/live
    curl -fsS http://127.0.0.1:8000/startup
    curl -fsS http://127.0.0.1:8000/ready
    curl -fsS http://127.0.0.1:8000/health
    .venv/bin/python scripts/smoke_health.py http://127.0.0.1:8000

切换或回滚完成后，应记录提交号、alias 目标、时间、执行人和 smoke 结果。
