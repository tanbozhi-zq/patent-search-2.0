# 管理员运行看板、配置草案与单实例运行时应用

本页说明 Issue #58 的运行看板、Issue #59 的配置草案预检，以及 Issue #60 的单实例
runtime reload 与回滚。它们复用服务现有的 FastAPI、Settings、QueryBudget、结构化日志和
Prometheus 指标。草案和运行操作只写独立 SQLite 审计库；运行值只保留在当前进程的不可变
快照中，绝不写环境变量、文件、systemd 或 OpenSearch。它们不替代 Prometheus、Grafana 或
journald，也不构成部署或生产验收。

本页说明能力和默认边界，部署方需单独核验实例的实际配置。

## 访问与权限

管理页面为 `GET /admin/`，数据接口位于 `/admin-api/v1/*`。全部页面、静态资源、读取和
草案预检只接受同一个管理员 HTTP Basic 身份，不建设 viewer/operator/admin RBAC 或
审批流：

```text
ADMIN_ENABLED=true
ADMIN_VIEWER_USERNAME=<managed internal administrator>
ADMIN_VIEWER_PASSWORD=<managed secret>
ADMIN_CONFIG_DRAFTS_ENABLED=false
ADMIN_RUNTIME_CONFIG_ENABLED=false
ADMIN_CONFIG_DATABASE_PATH=/var/lib/patent-search-service/admin-config.sqlite3
```

`ADMIN_VIEWER_*` 名称为兼容已有部署而保留，运行角色统一显示为 `admin`。管理员密码
不能复用 `API_TOKEN`。默认应与 `CONSOLE_PASSWORD` 分开管理；
若部署者明确选择相同凭据，两个浏览器入口将不再具有独立密码边界。
`ENABLE_AUTH=false` 只影响业务 API/Console，不会旁路管理鉴权。未启用管理入口时
所有管理路径返回 404；启用后，匿名和业务 API Token 均返回 401。
草案能力还有独立的 `ADMIN_CONFIG_DRAFTS_ENABLED` 开关，默认关闭；因此仅升级代码不会
把已经启用的 #58 看板自动变成写入口。
`ADMIN_RUNTIME_CONFIG_ENABLED` 是比草案更窄的第二个开关：它只能在草案能力已经启用时
设为 true，默认仍为 false。关闭它时运行时状态可读取，但 apply/rollback 固定路径返回
404；开启它不改变启动期 `Settings`，也不会在进程重启后重放旧值。

TLS 与可信网络边界验收完成前，只允许在 loopback、预生产或明确的可信私网入口启用。生产入口必须
使用 TLS、最小权限、访问日志和可回滚的代理配置。浏览器页面使用 no-store、
no-referrer、no-sniff、anti-frame 和只允许同源资源的 CSP。

## 发布身份与指标来源

发布系统应注入：

```text
SERVICE_RELEASE_COMMIT=<deployed commit SHA>
SERVICE_RELEASE_TAG=<release tag>
SERVICE_INSTANCE_ID=<non-secret instance identifier>
ADMIN_PROMETHEUS_URL=http://prometheus.internal:9090
ADMIN_PROMETHEUS_JOB=patent-search
ADMIN_METRICS_TIMEOUT_SECONDS=2
```

应用导出 `patent_search_build_info{version,commit,tag}=1`；实例身份使用 Prometheus
抓取时的 `instance` label，不在应用指标内重复添加。`unknown` 只适用于本地或未发布
环境，生产验收必须确认 commit/tag 已被替换。

看板只执行代码内固定的 PromQL，不接受浏览器传入任意 PromQL。查询复用现有 Grafana
口径：Counter/Histogram 使用 `rate`/`increase` 和聚合直方图；舱壁同时返回全局总量、
逐实例原值和逐实例利用率，最差值在逐实例比率上取 `max`；probe、启动时间和
build info 保持逐实例。只支持 5/15/60 分钟三个固定窗口。每批查询具有独立 HTTP 连接池、最多 4 个并发、
5 秒短缓存和 single-flight；单查询响应上限为 1 MiB、200 条 series，
label 和 sample 值也有长度上限。HTTP 连接、等待并发槽与响应流共用同一个总时限。
单个查询失败只让对应卡片显示不可用，不返回业务
`50301`，也不影响公开检索 API。
每条 PromQL 都带有经过字符白名单校验的固定 `job` selector，避免同一
Prometheus 中其他环境的同名指标被误聚合。预生产必须核对该 job 与抓取配置一致。

`ADMIN_PROMETHEUS_URL` 未配置时，发布身份、配置和关联日志仍可查看，聚合指标显示
不可用。该状态不能作为多实例指标验收通过的证据。

## 配置与秘密

配置 API 逐字段构造白名单，不序列化整个 `Settings`。页面只显示查询预算、请求预算、
Readiness、连接池和舱壁等非敏感参数。API Token、Console/Admin 密码和 OpenSearch
凭据只显示“已配置/未配置”。以下内容不会返回：

- Token、Cookie、密码或 Authorization；
- OpenSearch host、index/alias、用户、节点地址或 TLS 细节；
- 文件路径、systemd unit/命令、网络端口或任意未知环境变量；
- 完整查询、专利正文或原始依赖错误。

## 配置草案、预检与不可变审计

首版单管理员流程只有三个草案操作接口和一个只读导出接口：

```text
GET  /admin-api/v1/config-schema
POST /admin-api/v1/config-drafts
GET  /admin-api/v1/config-drafts?limit=20
GET  /admin-api/v1/config-drafts/export?id=<uuid>
```

`config-schema` 返回显式注册的 16 个非敏感参数定义、当前值、回滚基线值、类型、单位、
范围、apply mode、风险、观察指标、24 小时草案 TTL 和当前运行时版本 SHA-256。首版不登记 OpenSearch host、
index/alias、秘密、网络端口、路径、systemd，也暂不跨进程登记 MCP 参数。连接池、舱壁
和 query-budget provider 当前都由启动期对象持有，因此保守标记为 `restart_required`。Issue
#60 只重新绑定并实际支持以下四项 `runtime_reload` 参数：

- `opensearch.timeout_seconds`
- `opensearch.max_retries`
- `opensearch.retry_backoff_seconds`
- `request.deadline_seconds`

其中 OpenSearch timeout 与请求 deadline 的运行时上限都固定为 240 秒：它们不会越过
Prometheus 的 300 秒最大有限桶，deadline 也始终低于 MCP 到 FastAPI 至少 245 秒的实际等待时间。
300 秒桶只为取消清理和响应收尾保留观测余量，不会扩大服务执行预算。
若需提高 240 秒边界，必须同时变更指标桶、MCP 超时与部署契约，不能通过本控制面单独放宽。

启动基线与回滚目标使用同一硬约束。升级前应先把受管环境中的
`OPENSEARCH_TIMEOUT_SECONDS`、`PATENT_SEARCH_DEADLINE_SECONDS` 调整到 1--240 秒，并把
`PATENT_SEARCH_TIMEOUT_SECONDS` 提高到 245 或以上；系统不会 clamp，超限 FastAPI 基线或
过短 MCP timeout 都会令对应进程拒绝启动。运行时 SQLite 仅保留审计且启动时不重放，因此
无需迁移数据库中的 override；重启后的基线始终来自重新校验的部署环境。

它们通过独立 runtime snapshot 被请求 deadline 中间件和 OpenSearch Repository 读取；一个请求
在入口捕获完整快照，并在整个依赖重试循环中使用同一份值。因此 apply 不修改启动期
`Settings`、Client、连接池、舱壁或 QueryBudget provider。所有 `restart_required` 参数仍只显示
发布提示，页面没有热应用按钮。响应中的
`apply_mode_contract=issue_60_revalidation_required` 保留为机器可读护栏：后续若新增参数，必须
先逐项核对真实读取路径，而不能只因注册表标签把它加入运行时能力。

浏览器提交时必须回传刚读取的 `baseline_fingerprint`、1--500 字原因和最多 16 个候选
值，并使用 `application/json` 与 `X-Admin-Intent: create-config-draft`。服务端重新读取
当前值，不接受客户端提供 old value；基线已变化返回 `40901`，未知 key 在写库前返回
`40002`。已登记参数继续执行严格类型、有限数值、范围、no-op、
`heavy < global <= pool`、依赖 timeout/retry/backoff/deadline 以及 QueryBudget 硬上限和
组合校验。这与实际请求逻辑一致：单次 timeout 不高于 deadline，启用重试时
backoff 严格小于 deadline，每次依赖调用仍会被当前剩余预算截断。invalid 记录只
保存已规范化数值与稳定错误码，不保存原始错误值。

每次预检以单事务写入一行 `config_change_drafts`，保存唯一 UUID、管理员、服务端 UTC
时间、过期时间、原因、注册表/服务/commit 版本、基线指纹、脱敏 diff 和验证结果。数据库触发器
拒绝 UPDATE/DELETE；修改或重新预检必须创建新 ID。读取历史时若当前指纹不同，仅在
响应中推导 `expired`，不会改写旧记录；达到 `expires_at` 同样推导为 `expired`。列表最多
返回最近 100 条；已知 ID 可通过固定 export 路径导出结构化 JSON，不读取或改写运行态。
数据库记录与 `validation_status` 不可变；导出时的 `status` 会按当前基线和 24 小时 TTL
派生，因此可能显示为 `expired`。
固定的 `/config-drafts/export?id=<uuid>` 形状是有意选择：当前指标中间件在路由匹配前按
原始 `scope.path` 精确排除 `ADMIN_PATHS`，固定路径可直接复用该边界，避免 Admin 导出被
计入业务 metrics；路由匹配后的日志仍使用现有 route template，不会记录真实草案 ID。
若未来改成资源式动态路径，必须先增加安全的 Admin path-pattern 排除，把归一化模板加入
系统活动集合，并补对应回归测试。
POST 正文最多 16 KiB，包括无 `Content-Length` 的流式请求。

### 单实例 runtime reload、回读与回滚

实现将 #59 模块按 `models`、`registry`、`validation`、`fingerprint` 和 `store` 拆分，并在
独立 `RuntimeConfig` 模块维护一个进程内完整、不可变的参数快照。固定接口为：

```text
GET  /admin-api/v1/runtime-config
POST /admin-api/v1/runtime-config/apply
POST /admin-api/v1/runtime-config/rollback
```

读取接口要求已启用草案能力；两个 POST 还要求
`ADMIN_RUNTIME_CONFIG_ENABLED=true`、管理员 Basic 身份、`application/json`、同源检查、
对应的 `X-Admin-Intent` 以及 16--128 字符的 `Idempotency-Key`。数据库只保存该键的 SHA-256，
同一键与不同请求内容稳定冲突；完全相同的键只会在当前版本仍等于该操作的 `current_version`
时重放原终态，不会再次替换快照。若其后已有其他 apply/rollback 推进版本，则旧键稳定冲突，
不能把旧结果误报为当前状态。单实例控制器串行化 apply/rollback，并对成功、失败和拒绝操作
使用有限速率。

apply 会读取 validated 草案，比较草案基线与 `expected_version`，拒绝过期、invalid、混入
`restart_required` 的草案和任何 CAS 冲突。它先把当前与候选快照作为不可变审计版本保存，再一次
替换整个内存快照；不会逐字段更新。随后它回读 provider 与已绑定 Repository 的实际快照，并强制
刷新 readiness 探针。验证失败或任务在成功终态提交前被取消时，自动恢复替换前的完整快照、
再次回读验证并写入失败审计；取消信号会在恢复与终态审计完成后继续向上抛出。手工
rollback 只能恢复当前快照的上一版本，但恢复值仍生成新的版本号，因此旧页面或旧请求无法用内容
相同的旧指纹覆盖它。

每个请求在入口保留自己的快照，所以正在执行的请求可以完整使用旧值，新请求完整使用新值，绝不
出现一次重试读到一半旧值、一半新值。运行版本、来源 (`deployment_baseline` 或
`runtime_override`)、实际值、可回滚版本和最近的脱敏操作会回显到管理页面。进程重启时不会从
SQLite 恢复 override，而是回到部署基线；页面必须显示该来源变化。

运行操作的自动验证只证明本进程 provider/Repository 绑定和 readiness 探针。HTTP、Console、MCP
smoke、Prometheus 变更前后观察和现场日志核对仍是单独的人工/发布验收，不能由 API 返回 200
替代。

SQLite 只位于管理控制面。FastAPI 的搜索、详情、Console、MCP、探针、metrics、舱壁、
deadline 和 QueryBudget 请求路径都不会读取它。同步 SQLite 操作由 FastAPI 同步路由的
线程池执行；WAL、`synchronous=FULL`、短 `busy_timeout`、`BEGIN IMMEDIATE` 和单行事务
避免半条记录。锁竞争返回脱敏 `50301`，损坏或不可写返回统一 `50002`，不泄露路径。

部署 unit 使用：

```text
StateDirectory=patent-search-service
StateDirectoryMode=0700
UMask=0077
```

因此数据库位于持久 `/var/lib/patent-search-service`，而不是代码发布目录；主文件权限为
0600。应用拒绝符号链接、非普通文件、多硬链目标或 group/other 可读的状态目录。启用前
确认目录属于服务用户，并备份已有数据库。页面仅在第二个开关开启时提供固定的
apply/rollback 动作；它始终没有 restart、deploy、shell 或环境变量写入动作。

## 日志范围与上限

生产和预生产使用 `ADMIN_LOG_SOURCE=journal`。适配器复用成熟的
`systemd.journal.Reader`，固定读取 `patent-search` namespace，并用 unit 与
`SYSLOG_IDENTIFIER` 双白名单限定 FastAPI/MCP 事件。它只解析 #36 的已知 JSON
事件和字段，丢弃 unstructured message、超限消息与未知事件。

每次查询最长 24 小时、最多扫描 5,000 条 journal entry、单条 `MESSAGE`
最大 16 KiB、返回最多 100 条，并使用不透明游标继续下一个有界扫描块。独立
单 worker 在线程内执行 250 ms wall-clock 检查；并发查询不排队，只降级当前
日志卡片。游标绑定生成它的时间窗口和筛选指纹，修改 request ID、route、业务
错误码或窗口后必须重新查询，不能继续旧游标。精确 request ID、route 和业务
错误码都使用服务端白名单校验。

非 Linux、可选 `systemd-python` 依赖缺失或运行用户无权读取时，日志分区返回
`unavailable`，不影响公开 API。`ADMIN_LOG_SOURCE=process` 只是显式的本地开发降级：
最近 2,000 条、进程重启后清空。两种模式都只覆盖当前实例；在没有集中日志
后端前，负载均衡入口不能宣称可跨实例定位任意 request ID。

Linux 部署需要在构建环境安装 `pkg-config` 和 `libsystemd` 开发包，再安装锁定的
可选依赖：

```text
<venv-python> -m pip install -r requirements-admin-journal.txt
```

`User=work` 的现有 unit 默认可能无权读取 namespace journal。禁止把 Web 服务加入
可读取全主机 journal 的 `systemd-journal`、`adm` 或 `wheel` 组。仓库使用 systemd
原生 sysusers/tmpfiles 机制创建专用 `patent-search-journal-readers` 组，并只对
`/var/log/journal/%m.patent-search` 与 `/run/log/journal/%m.patent-search` 追加只读/
目录遍历 ACL；默认 ACL 同时覆盖后续轮转文件。部署顺序如下：

```text
sudo install -D -m 0644 deployment/sysusers.d/patent-search-admin-journal.conf /etc/sysusers.d/patent-search-admin-journal.conf
sudo systemd-sysusers /etc/sysusers.d/patent-search-admin-journal.conf
sudo install -D -m 0644 deployment/tmpfiles.d/patent-search-admin-journal.conf /etc/tmpfiles.d/patent-search-admin-journal.conf
sudo systemctl start systemd-journald@patent-search.service
sudo systemd-tmpfiles --create /etc/tmpfiles.d/patent-search-admin-journal.conf
sudo install -D -m 0644 deployment/patent-search-admin-journal.conf /etc/systemd/system/patent-search-service.service.d/20-admin-journal.conf
sudo systemctl daemon-reload
```

然后按发布流程重启 FastAPI。drop-in 显式等待 namespace journald 实例，并在每次
服务启动前以固定的 tmpfiles 配置重放窄范围 ACL，因此 `/run` 目录重建后不会丢失
权限；Web 进程本身只加入上述专用组。应用内部仍固定 namespace/unit/identifier。
预生产必须分别验证依赖缺失、移除专用组、正常权限，以及该用户不能读取默认
namespace 四种情况。若首次手工执行时 namespace 目录尚未生成，先启动对应 journald
实例再执行 tmpfiles；不能退回全局 journal 组。

每次管理读取都会产生 `admin_read_completed` 审计事件，记录 admin、动作、结果、
时间窗口和返回数量；不会记录筛选内容、目标 request ID、查询全文或错误原文。
草案完成事件 `admin_config_completed` 与存储失败事件 `admin_config_store_failed` 都记录
request ID、管理员、角色、动作和结果；完成事件另记录草案 ID 与固定数量，失败事件不带
数据库错误原文或路径。candidate values 与 reason 不进入普通日志。运行操作使用
`admin_runtime_config_completed`，只记录 request ID、动作、结果、草案 ID 和操作 ID；回滚原因、
Idempotency-Key、候选值和依赖错误原文不进入普通日志。

## 验收与回滚

Issue #47 的完整单实例验收顺序、自动 runtime smoke、Prometheus/业务联合检查和四阶段

预生产至少验证：

1. 未认证和仅携带业务 Token 的请求无法访问页面、资源和 API。Console 与 Admin 使用独立
   凭据时，Console 凭据同样必须失败；部署者若显式共享两者凭据，则应记录它们是同一已
   授权身份及失去独立密码边界的风险，不能再把该场景记为 Console/Admin 隔离成功。
2. 关闭草案开关时 schema/draft/runtime 读取接口返回 404；仅开启草案开关时 runtime
   apply/rollback 仍返回 404；两项开关都开启后才显示固定的 apply/rollback 控件。
3. 仅含四项 `runtime_reload` 白名单之一的 validated 草案可以应用，页面实际值、版本、
   来源、Repository 行为与回读结果一致；混入 `restart_required` 的草案没有热应用按钮且
   POST 稳定拒绝。
4. 缺失管理员身份、JSON/同源/Intent、合法 Idempotency-Key 的写请求均被拒绝；相同键重放
   不重复替换，不同内容复用同一键、旧版本、过期草案和并发请求稳定冲突或限速。
5. 注入验证失败后，完整旧快照恢复、旧请求/新请求不会混用字段，失败审计完整；手工回滚
   生成新版本并保留 old/new 版本、操作者、草案 ID（如有）和脱敏结果。
6. 进程重启后确认运行来源回到 `deployment_baseline`，不从 SQLite 回放旧 override；草案和
   审计历史仍在。
7. 逐实例 probe/build/start-time 和多实例舱壁聚合与 Prometheus 原始结果一致；对应用前后
   指标、HTTP、Console 和 MCP smoke 做独立现场记录，不能把 API 200 当作该验收的替代。
8. 使用一次失败响应的 request ID 查询当前实例 journal，响应与日志链路一致。
9. 以哨兵值配置全部秘密、host/index 和 Prometheus URL，确认页面、API、指标和审计均
   不出现这些值。
10. Prometheus 超时、返回超限和不可用时，公开搜索、详情、探针、错误码、Retry-After
    和双层舱壁语义不变。
11. 绕过页面提交未知、秘密、错误类型、越界、no-op 和非法组合，确认不会产生 validated
    草案；以旧基线提交返回 `40901`。
12. 并发创建后 UUID 唯一、行数完整；直接 UPDATE/DELETE 被数据库拒绝；重启进程后历史
    仍在，基线变化或 24 小时 TTL 到期后原 ID 显示 expired。
13. 以 ID 导出较旧草案，确认内容完整、响应 no-store；只创建草案时 Settings、环境变量、
    Repository/Client、两个舱壁、QueryBudget provider 和 readiness 对象做身份和值比较，
    确认完全不变且 OpenSearch 调用为零。

只停止 runtime reload 时先设置 `ADMIN_RUNTIME_CONFIG_ENABLED=false` 并按正常发布流程重启
FastAPI；当前进程的 override 随重启回到部署基线，SQLite 文件保留供审计。只回滚草案能力时
再设置 `ADMIN_CONFIG_DRAFTS_ENABLED=false`；无需关闭 #58 读取看板。回滚整个管理入口时再在
可信入口阻断 `/admin` 和 `/admin-api/*`，设置 `ADMIN_ENABLED=false` 并按正常代码发布流程
回滚；不得改用业务 Token、关闭业务鉴权或公开裸端口。Issue 合并、代码部署和生产启用必须
分别记录。
