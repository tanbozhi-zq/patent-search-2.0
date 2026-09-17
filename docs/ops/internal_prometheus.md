# 内测 Prometheus 数据源

本文是 Issue #62 的单实例内测部署手册。它把现有 `/metrics`、告警规则和只读管理员
看板连接起来，不建设 Prometheus、Grafana、Alertmanager 或日志平台的生产高可用
集群，也不修改 OpenSearch、检索语义、舱壁容量或告警阈值。

代码合并、部署完成和内测验收是三个独立状态。仅通过仓库测试不能证明目标机器已经
安装 Prometheus，也不能证明管理看板已经取得真实数据。

以下为部署与验收方法，部署前应独立核对服务状态。

## 复用边界与版本来源

- Prometheus 版本和 Linux amd64 SHA256 的唯一仓库来源是
  `deployment/prometheus/version.env`；CI 和部署都读取该文件，不得另写一个未经 CI
  验证的版本或校验值。
- 主配置复用 `deployment/prometheus/prometheus.yml`，服务告警复用
  `deployment/observability/alert_rules.yml`。
- 管理看板继续使用现有固定 PromQL、5/15/60 分钟窗口、2 秒查询超时、最多 4 个
  并发、5 秒缓存和 single-flight；此组件不增加浏览器可提交的 PromQL。
- Prometheus 使用 `file_sd_configs`。真实抓取目标、端口、实例名和任何凭据只写入
  目标机器受限配置，不进入仓库、PR 或 Issue。

仓库配置固定使用 `job=patent-search`，必须与 `ADMIN_PROMETHEUS_JOB` 一致。名称不一致
时 Prometheus 自身可能健康，但管理员 API 的固定查询会得到空结果。

## 资源与故障边界

内测初始预算为 30 天和 2 GB TSDB blocks，以先达到者为准。Prometheus 自抓取提供
`prometheus_tsdb_storage_blocks_bytes` 和
`prometheus_tsdb_retention_limit_bytes`；仓库规则在 blocks 连续 10 分钟超过配置容量
80% 时告警。该容量指标不包含 WAL 和 head 的全部磁盘占用，因此部署前、升级前和告警
处置时仍必须检查数据目录所在文件系统的真实剩余空间。

模板为 `patent-search` job 设置 256 KB body、1,000 samples、16 labels、64-byte label
name、128-byte label value 和 10 targets 上限。部署前应测量本环境指标并确认足够余量。
超过任一上限时整次 scrape 必须失败并由 `up=0` 暴露，不能静默截断后通过验收。

systemd 同时设置 `MemoryHigh=384M`、`MemoryMax=512M`、`CPUQuota=50%` 和
`TasksMax=128`，避免 Prometheus 与同机检索服务无界争抢资源。部署前必须重新只读测量；
若正常响应已达到任一抓取上限的 50%，先审查基数变化并调整已验证预算，不能直接上线。
smoke 自动检查 response body bytes 和 sample count，仅在两者均严格低于 50% 时通过，
等于 50% 即失败。labels 数、label name/value 长度和 targets 数仍按部署前检查第 4 步
重新做人工只读测量，不能用 smoke 结果替代。

Prometheus 与检索服务没有 systemd `Requires`/`BindsTo` 关系。Prometheus 停止、查询
超时或返回部分结果时，管理员指标卡片应降级，公开 API、Console 和 MCP 必须继续工作。

## 部署前检查

1. 确认目标架构与 CI 固定的 Prometheus 归档一致。
2. 记录当前检索服务 commit/tag、`ADMIN_PROMETHEUS_URL` 是否已配置、服务和 MCP 状态。
3. 记录 Prometheus 数据目录所在文件系统的容量、已用和可用空间。
4. 确认服务本机 `/metrics` 可解析，保存 target 数、sample 数、响应字节数、最大 labels 数和
   label name/value 长度；不得保存完整指标正文。任何数值达到配置上限的 50% 时停止部署。
5. 运行仓库检查：

       make check-observability PROMTOOL=<verified-promtool>

6. 保存当前应用环境文件、systemd unit、Prometheus 配置、当前二进制链接目标和配置文件
   SHA256。备份目录必须是本次发布专用的显式路径，不能覆盖历史回滚点。

## 安装与配置

### 1. 安装固定版本

从 `deployment/prometheus/version.env` 读取 `PROMETHEUS_VERSION` 和对应 SHA256。下载官方归档后先
执行严格 SHA256 校验，再解压到新的版本目录。保留旧版本目录，并通过
`/opt/patent-search-prometheus/current` 符号链接切换；不得覆盖旧二进制来完成升级。

用新版本的 `promtool` 验证仓库配置和规则：

    <new-promtool> check config --syntax-only deployment/prometheus/prometheus.yml
    <new-promtool> check rules deployment/observability/alert_rules.yml
    <new-promtool> test rules deployment/observability/alert_rules_test.yml

### 2. 创建最小权限运行身份和目录

安装仓库提供的 sysusers/tmpfiles 配置，并让 systemd 创建专用用户、配置目录和数据
目录：

    sudo install -D -m 0644 deployment/prometheus/sysusers.d/patent-search-prometheus.conf /etc/sysusers.d/patent-search-prometheus.conf
    sudo systemd-sysusers /etc/sysusers.d/patent-search-prometheus.conf
    sudo install -D -m 0644 deployment/prometheus/tmpfiles.d/patent-search-prometheus.conf /etc/tmpfiles.d/patent-search-prometheus.conf
    sudo systemd-tmpfiles --create /etc/tmpfiles.d/patent-search-prometheus.conf

配置和目标文件由 root 管理、专用组只读；TSDB 数据目录只允许专用用户写入。

### 3. 安装主配置、规则和 unit

    sudo install -m 0640 -o root -g patent-search-prometheus deployment/prometheus/prometheus.yml /etc/patent-search-prometheus/prometheus.yml
    sudo install -m 0640 -o root -g patent-search-prometheus deployment/observability/alert_rules.yml /etc/patent-search-prometheus/rules/patent-search.yml
    sudo install -m 0644 deployment/prometheus/patent-search-prometheus.service /etc/systemd/system/patent-search-prometheus.service

在 `/etc/patent-search-prometheus/runtime.env` 写入部署选择的本地查询端口：

    PROMETHEUS_WEB_PORT=<managed-loopback-port>

端口必须是 1024--65535 的非特权端口。文件权限必须为 root 管理、专用组只读。unit 把
Web/API 强制绑定到 `127.0.0.1`，不会读取环境变量来改变监听地址；管理 API 和
Prometheus 查询使用同一 loopback 入口。

### 4. 写入服务器侧 targets

创建 `/etc/patent-search-prometheus/targets/patent-search.yml`：

```yaml
- targets:
    - "<loopback-service-metrics-target>"
  labels:
    instance: "<non-secret-instance-id>"
```

创建 `/etc/patent-search-prometheus/targets/prometheus.yml`：

```yaml
- targets:
    - "<loopback-prometheus-target>"
  labels:
    instance: "<non-secret-prometheus-instance-id>"
```

两个文件都必须是 root 管理、专用组只读。`patent-search` job 只放同一内测环境的检索
实例；不得把其他环境同名指标混入看板聚合。

### 5. 目标机完整校验与启动

仓库模板只能做语法检查；目标文件和规则安装完成后，必须对实际路径做完整检查：

    /opt/patent-search-prometheus/current/promtool check config /etc/patent-search-prometheus/prometheus.yml
    sudo systemctl daemon-reload
    sudo systemctl enable --now patent-search-prometheus.service
    systemctl is-active patent-search-prometheus.service

只从目标机本地检查 readiness、targets、rules 和 `up`。从非受信网络连接 Prometheus
查询入口必须失败。Alertmanager 未配置属于本 Issue 允许的内测边界，但所有规则必须
成功加载和计算。

### 6. 连接管理员看板

在服务端受限环境文件中设置以下既有变量，不把实际 URL 或身份写入仓库：

    ADMIN_PROMETHEUS_URL=<managed-loopback-prometheus-url>
    ADMIN_PROMETHEUS_JOB=patent-search
    ADMIN_METRICS_TIMEOUT_SECONDS=2

按正常服务发布流程重启 FastAPI。只修改 Prometheus 配置或 targets 时不需要重启检索
服务；Prometheus 会刷新 file-SD，规则/主配置变更则在 `promtool` 通过后发送 SIGHUP。

## 内测验收

验收必须保存开始/结束时间、服务 commit、Prometheus 版本、配置摘要 SHA256、回滚点和
每项结果，但不得保存 Token、Cookie、密码、完整查询、专利正文、节点凭据或原始
metrics 全文。

1. 先配置并确认两个隔离 targets，其 `instance` label 必须分别等于对应服务的
   `SERVICE_INSTANCE_ID`；从此处到 final 完成前不得增删或替换 targets。在每个预期 target
   上各制造一轮受控初始化事件：正常业务 2xx、固定 `400/40002`、隔离的 search 依赖失败、
   global 拒绝和 heavy_search 拒绝，并等待至少两个抓取周期。这一轮只初始化五类原始
   Counter series，属于基线历史，不能作为正式验收事件。随后按下方两阶段命令捕获
   baseline。管理状态接口必须报告
   `metrics_source=prometheus`；baseline smoke 必须完整覆盖
   5/15/60 分钟，三个窗口均不再整体 unavailable，并把每条固定查询与 Prometheus
   在对应管理快照的 `generated_at` 时刻做直查和归一化比较。series labels 必须完全
   一致；考虑同一批查询可能跨过抓取边界，数值只允许 5% 相对误差或 `1e-9` 绝对误差。
2. baseline 成功返回后，产生一次正常业务请求和一次固定 `400/40002`；核对 request
   rate、success rate、P50/P95/P99、HTTP status/code 和 route 指标。
3. 在同一 baseline 之后制造 search 依赖失败和双层舱壁拒绝，并在五类事件全部完成后
   等待至少两个抓取周期。故障/拒绝只允许在 loopback 隔离验收实例中制造；不得修改真实
   OpenSearch host/index/alias、停真实依赖或用无界并发冲击在线实例。
4. 使用 baseline 前已固定的两个隔离实例，逐项核对 Counter/Histogram 的跨实例聚合、
   Gauge 的逐实例值、总在途求和和逐实例利用率后取最大值；final 完成后再移除临时
   targets。
5. 用同一固定窗口直接查询 Prometheus，并与管理 API 返回值比较。延迟分位数单位为秒。
6. 记录一个已落盘时间序列的值与时间范围，重启 Prometheus，再查询同一时间范围，确认
   数据仍在且 targets/rules 恢复。
7. 停止 Prometheus，确认管理指标降级，同时 HTTP/Console/MCP smoke、错误码和
   `Retry-After` 不变；随后重新启动并确认自动恢复。
8. 核对每个预期 target 的 `up=1`、retention 自指标精确等于 2,592,000 秒和
   2,147,483,648 bytes、TSDB 目录大小及文件系统剩余空间；规则列表中必须包含存储预算
   告警。
9. 使用部署侧哨兵值扫描管理页面/API、Prometheus 查询结果和日志，确认秘密、完整查询、
   完整正文和节点凭据均未出现。

完成上述每个预期 target 的初始化轮次并等待至少两个抓取周期后，在已安全注入
`ADMIN_VIEWER_USERNAME`/`ADMIN_VIEWER_PASSWORD` 的进程环境中捕获基线：

    <venv-python> -m scripts.smoke_admin_metrics <admin-base-url> <loopback-prometheus-url> --minimum-instances 2 --expected-release-commit <deployed-commit> --capture-acceptance-baseline

脚本先完成管理员窗口与 Prometheus 直查等静态比较，再读取最终服务状态并捕获初始快照；
随后在全部初始快照查询完成后建立 barrier，等待另一次新抓取作为稳定 guard。只在静态检查
全部通过、初始快照到 guard 的五类 Counter 完全不变，并且管理员
`status.release` 不含 `unknown` 占位值、每个 Prometheus `build_info` 的
version/commit/tag、精确实例数、服务启动时间、新鲜抓取时间和健康 target 一致时输出
`acceptance_baseline_time`；此时每个预期实例的五类验收 Counter series 还必须都已存在。
脚本同时通过固定 job、固定 metric family 和有界 limit 调用 Prometheus
`/api/v1/targets/metadata`，要求三个底层 family 在每个预期实例上各有且仅有一条元数据、
`metric` 精确等于所查询 family 且 `type=counter`，不得缺失、重复或出现额外 target；final 与
guard 会重新读取并要求该映射稳定。
最终输出的 `acceptance_baseline_time` 使用 guard 的时点，而非较早的初始快照时点。基线证据点
因此建立在静态比较完成之后；初始化轮次、命令开始前尚未抓取的事件以及静态比较期间出现的
事件都会进入基线，初始快照到 guard 之间观测到同类事件则整次失败。从基线命令开始到成功
输出必须保持隔离实例安静；自动证据截至输出的 guard 时点，guard 固定时点到进程输出的剩余
有界查询区间仍由该操作纪律约束，脚本不能证明这一区间没有尚未抓取的事件。命令成功返回后
再制造正式的第二轮事件：
通过 `/api/patent/search` 制造一次正常 2xx 和一次固定 `400/40002` 无效请求，并制造第 3 步
的 search 依赖失败及两类隔离拒绝事件；等待至少两个抓取周期，期间不得重启/重新部署应用
或修改 targets。从正式事件开始直到 final 命令最终输出前，也不得再制造同类事件。在基线后
15 分钟内运行：

    <venv-python> -m scripts.smoke_admin_metrics <admin-base-url> <loopback-prometheus-url> --minimum-instances 2 --expected-release-commit <deployed-commit> --acceptance-baseline-time <captured-baseline-time> --require-acceptance-events

不传 `--window-seconds` 时脚本默认遍历 300/900/3600 秒；该参数只用于重复指定诊断
窗口，不能作为完整验收记录。脚本还会核对 success rate 在 0--1、P50 <= P95 <= P99，
并核对 global/heavy_search 两层舱壁的逐实例 series、总量求和、最坏利用率聚合和两层
拒绝事件。最终检查在 baseline 和当前 Prometheus 时间分别读取原始 Counter，要求业务
2xx、固定 `400/40002`、search 依赖失败、global 拒绝和 heavy_search 拒绝均有严格正
增量，并在 3600 秒管理员窗口确认同合同事件实际可见；首次出现的 Counter series 不会被
Prometheus 推断为从零增长，因此缺少初始化轮次会在基线阶段失败。较长验收中 300 秒窗口
可自然过期。final 会先固定 raw Counter after，再轮询三个管理窗口；每个管理快照的
`generated_at` 必须严格晚于 raw after 加现有 5 秒单批查询硬上限，因而不能复用 raw 前启动
的 in-flight 或缓存批次。完成逐查询直查比较后，脚本还会等待一次严格晚于全部管理快照的
时间且严格晚于直查完成后 Prometheus barrier 的新抓取，要求每个实例的 release/启动时间、
`up`、拓扑以及五类 Counter map 与 raw after 完全一致，最后再读一次管理状态。任一新事件、
陈旧抓取、重启、target 变化或计数器回退只要在 raw after 到 completion-guard scrape 的
证据区间内被观测到就会失败，30--900 秒之外的 raw baseline-to-after 间隔也会失败；final
的缓存淘汰、直查和 completion guard 用时不计入该事件间隔。guard scrape 后到命令输出前
仍依靠上述隔离和禁止变化的操作纪律，自动输出不证明这一小段时间内持续无变化。除通过
基线模式时额外输出一个时间戳外，它只输出各检查项的
布尔结果，不输出凭据、URL、series label 或响应正文。省略两阶段参数只能证明查询链路及
静态聚合口径可用；历史窗口中的正值不能代替本次事件，raw Counter 增长也不能替代管理员
看板数据路径可见性。

应用 `/metrics` 本身不要求业务认证，其保护依赖网络入口。没有独立网络证据时，不能将 Prometheus 查询成功或 Issue 关闭解释为“未认证用户不能访问原始指标”；TLS 与端口收口必须单独验收。

## Storage budget response

`PatentSearchPrometheusStorageBudgetHigh` 触发后：

1. 停止新增 targets 和额外 series，保存当前配置摘要、活跃 series 数、blocks/WAL/head
   大小和文件系统剩余空间。
2. 检查是否出现未知 job、动态 label 或抓取目标重复；不通过删除任意时间序列来掩盖
   基数回归。
3. 若文件系统余量不足，先停止 Prometheus 保护主机，再按已验证回滚点恢复配置或缩短
   内测保留期。
4. 配置修复后运行完整 `promtool check config`，重启或 reload，并观察至少两个抓取与
   规则计算周期。告警恢复后记录原因、影响窗口和后续容量选择。

## 备份、升级与停止

- 配置备份包含主配置、targets、rules、runtime env、unit、版本链接目标和所有文件
  SHA256；runtime env 和 targets 不得进入仓库或 Issue。
- TSDB 备份使用冷备：先停止 Prometheus，再把显式数据目录复制到本次备份专用目录，
  完成后启动并验证。内测不启用 Prometheus admin API，也不使用未校验的在线复制作为
  可恢复备份。
- 升级先停止服务并保存与旧版本匹配的冷备，再安装新版本目录，用新 `promtool` 完整
  验证当前目标机配置，最后原子切换 current 链接并重启。未确认 TSDB/WAL 向后兼容时，
  不得让旧二进制直接打开已被新版本写入的数据；降级必须同时恢复对应旧版本冷备。
- 临时停止只停止 `patent-search-prometheus.service`；不得停止或重启 OpenSearch。停止
  期间管理指标应明确降级，业务服务继续运行。

## 回滚

1. 恢复应用环境文件中旧的 `ADMIN_PROMETHEUS_URL` 状态并按服务发布流程重启 FastAPI。
2. 验证管理指标降级符合预期，公开 HTTP、Console 和 MCP smoke 仍通过。
3. 停止并禁用 `patent-search-prometheus.service`。
4. 如需回退 Prometheus 版本，恢复旧配置和 current 链接；如需恢复 TSDB，保持服务停止
   后从对应冷备恢复，再检查属主、完整配置和规则。
5. 不删除新旧版本目录、数据或回滚备份，直到验收记录确认无需再次恢复。

回滚完成只说明恢复到部署前状态，不代表 Issue #29、#37、#49、#63 或生产级多实例
观测已经完成。
