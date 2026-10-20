# 管理员运行看板、配置草案与单实例运行时应用

本页说明管理员运行看板、配置草案预检，以及单实例
runtime reload 与回滚。它们复用服务现有的 FastAPI、Settings、QueryBudget、结构化日志和
Prometheus 指标。草案和运行操作只写独立 SQLite 审计库；运行值只保留在当前进程的不可变
快照中，绝不写环境变量、文件、systemd 或 OpenSearch。它们不替代 Prometheus、Grafana 或
journald。

本页说明能力和默认边界。实际启用情况以目标环境配置为准。

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
把已经启用的只读看板自动变成写入口。
`ADMIN_RUNTIME_CONFIG_ENABLED` 是比草案更窄的第二个开关：它只能在草案能力已经启用时
设为 true，默认仍为 false。关闭它时运行时状态可读取，但 apply/rollback 固定路径返回
404；开启它不改变启动期 `Settings`，也不会在进程重启后重放旧值。

TLS 与可信网络边界验证完成前，只允许在 loopback、预生产或明确的可信私网入口启用。生产入口必须
使用 TLS、最小权限、访问日志和可回滚的代理配置。浏览器页面使用 no-store、
no-referrer、no-sniff、anti-frame 和只允许同源资源的 CSP。

## 版本信息与指标来源

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
环境，生产环境必须配置实际 commit/tag。

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

`ADMIN_PROMETHEUS_URL` 未配置时，版本信息、配置和关联日志仍可查看，聚合指标显示
不可用。

## 配置与秘密

配置 API 逐字段构造白名单，不序列化整个 `Settings`。页面只显示查询预算、请求预算、
Readiness、连接池和舱壁等非敏感参数。API Token、Console/Admin 密码和 OpenSearch
凭据只显示“已配置/未配置”。以下内容不会返回：

- Token、Cookie、密码或 Authorization；
- OpenSearch host、index/alias、用户、节点地址或 TLS 细节；
- 文件路径、systemd unit/命令、网络端口或任意未知环境变量；
- 完整查询、专利正文或原始依赖错误。

## 配置草案与运行时变更

| 接口 | 用途 |
|---|---|
| `GET /admin-api/v1/config-schema` | 参数定义、当前值、基线指纹和运行版本 |
| `POST /admin-api/v1/config-drafts` | 校验候选值并保存草案 |
| `GET /admin-api/v1/config-drafts?limit=20` | 最近草案，最多 100 条 |
| `GET /admin-api/v1/config-drafts/export?id=<uuid>` | 导出指定草案 |
| `GET /admin-api/v1/runtime-config` | 当前运行快照、来源和回滚版本 |
| `POST /admin-api/v1/runtime-config/apply` | 应用已验证草案 |
| `POST /admin-api/v1/runtime-config/rollback` | 恢复上一份快照，生成新版本 |

配置 schema 只登记非敏感参数。当前可热应用的白名单为：

- `opensearch.timeout_seconds`
- `opensearch.max_retries`
- `opensearch.retry_backoff_seconds`
- `request.deadline_seconds`

连接池、舱壁和查询预算仍需重启生效。参数类型、范围和读取路径以[注册表](../../app/core/admin_config/registry.py)及[校验器](../../app/core/admin_config/validation.py)为准；不能仅修改注册标签来增加热应用能力。

### 创建草案

1. 读取 schema，保留 `baseline_fingerprint`。
2. POST JSON，包含该指纹、1–500 字原因和最多 16 个候选值，并设置 `X-Admin-Intent: create-config-draft`。
3. 检查验证结果。未知参数、错误类型、非有限数值、越界、无变化及非法组合均不允许成为可应用草案；基线变化返回 `40901`。

请求体最多 16 KiB。草案按服务端当前值校验，不接受客户端指定旧值。草案在 24 小时 TTL 到期或基线改变后视为过期；记录和验证结果保持不可变，重新预检应新建草案。

### 应用与回滚

两个 POST 要求管理员 Basic、JSON、同源检查、对应 `X-Admin-Intent`、16–128 字符的 `Idempotency-Key`，以及期望运行版本。接口字段见[运行时 API](../../app/api/admin_runtime_config.py)。

- 仅应用 validated、未过期、基线和版本仍匹配、且全部为热应用参数的草案。
- 应用一次替换整个进程内快照；已进入的请求使用旧快照，新请求使用新快照。
- 回读 provider、Repository 和 readiness 失败，或成功提交前被取消时，恢复旧快照并保存失败审计。
- 同一幂等键只能在对应运行版本仍有效时重放终态；内容不同或已被后续操作推进时返回冲突。
- 手工回滚只恢复上一版本的值，并创建新版本；进程重启回到部署环境基线，不从 SQLite 重放旧覆盖值。

运行时 OpenSearch timeout 和请求 deadline 上限均为 240 秒；MCP 等待至少 245 秒。更改这些边界需同步预算、指标桶和 MCP 契约，不能仅放宽管理参数。

### 审计存储

草案和运行操作写入独立 SQLite，业务读请求不读取该库。草案触发器禁止 UPDATE/DELETE；操作记录保留管理员、原因、脱敏差异、版本、时间和结果。数据库故障返回脱敏错误，不展示路径或原始异常。

[服务 unit](../../deployment/patent-search-service.service)通过 `StateDirectory` 提供持久目录，目录为 0700、数据库为 0600；启用前核对服务用户和备份。应用拒绝符号链接、非普通文件、多硬链及 group/other 可读目录。数据库不能放在代码发布目录。

## 日志范围与权限

生产和预生产使用 `ADMIN_LOG_SOURCE=journal`。适配器复用成熟的
`systemd.journal.Reader`，固定读取 `patent-search` namespace，并用 unit 与
`SYSLOG_IDENTIFIER` 双白名单限定 FastAPI/MCP 事件。它只解析已定义的 JSON
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

## 操作检查与回滚

在隔离环境检查：

1. 匿名、业务 Token 和未授权的 Basic 身份不能读取管理数据；开关关闭时对应路由返回 404。
2. 草案校验拒绝未知参数、越界、无变化和基线冲突；仅四项热应用参数能进入 apply。
3. 应用、幂等重放、并发冲突、失败自动恢复及手工回滚符合版本约束，旧请求不混用新参数。
4. 重启后来源回到 `deployment_baseline`，草案和审计保留；普通日志不包含候选值、原因、秘密或原始依赖错误。
5. Prometheus 不可用、日志依赖缺失或日志权限不足时，仅对应卡片降级，业务 API 保持可用。
6. HTTP、Console、MCP 和指标在变更前后均可用；应用成功后的自动回读不代替这些业务检查。

`scripts/smoke_admin_runtime.py` 会实际应用并回滚参数，只能在明确允许写入的隔离或维护环境执行。

停止热应用时关闭 `ADMIN_RUNTIME_CONFIG_ENABLED` 并重启 FastAPI；停止草案能力时再关闭 `ADMIN_CONFIG_DRAFTS_ENABLED`。关闭整个管理入口时先收口入口，再关闭 `ADMIN_ENABLED` 并重启。保留审计库，不通过关闭业务鉴权或改用业务 Token 完成回滚。
