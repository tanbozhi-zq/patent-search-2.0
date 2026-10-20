# 内测 Prometheus 数据源

本文说明单实例内测环境的 Prometheus 配置。它把现有 `/metrics`、告警规则和只读管理员
看板连接起来，不建设 Prometheus、Grafana、Alertmanager 或日志平台的生产高可用
集群，也不修改 OpenSearch、检索语义、舱壁容量或告警阈值。

## 复用边界与版本来源

- Prometheus 版本和 Linux amd64 SHA256 的唯一仓库来源是
  `deployment/prometheus/version.env`；CI 和部署都读取该文件，不得另写一个未经 CI
  验证的版本或校验值。
- 主配置复用 `deployment/prometheus/prometheus.yml`，服务告警复用
  `deployment/observability/alert_rules.yml`。
- 管理看板的固定查询、窗口和访问限制见[管理员手册](admin_dashboard.md#版本信息与指标来源)。
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

`patent-search` job 设置 256 KB body、1,000 samples、16 labels、64-byte label
name、128-byte label value 和 10 targets 上限。
超过任一上限时整次 scrape 必须失败并由 `up=0` 暴露，不能静默截断指标。

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
查询入口必须失败。内测环境可不配置 Alertmanager，但所有规则必须成功加载和计算；
需要发送告警通知时，应另行配置 Alertmanager 接收方。

### 6. 连接管理员看板

在服务端受限环境文件中设置以下既有变量，不把实际 URL 或身份写入仓库：

    ADMIN_PROMETHEUS_URL=<managed-loopback-prometheus-url>
    ADMIN_PROMETHEUS_JOB=patent-search
    ADMIN_METRICS_TIMEOUT_SECONDS=2

按正常服务发布流程重启 FastAPI。只修改 Prometheus 配置或 targets 时不需要重启检索
服务；Prometheus 会刷新 file-SD，规则/主配置变更则在 `promtool` 通过后发送 SIGHUP。

## 指标与故障检查

保留服务提交、Prometheus 版本、配置摘要、回滚点和检查结果。指标口径统一见[可观测性手册](observability.md)，不要保存完整指标正文、秘密、完整查询或专利正文。

### 日常检查

应用 `/metrics` 本身不要求业务认证，其保护依赖网络入口。

1. 从目标机读取 readiness、targets、rules，确认预期 target 的 `up=1`，管理员状态为 `metrics_source=prometheus`。
2. 检查保留策略、blocks/WAL/head 占用和文件系统余量；确认存储预算告警已加载。
3. 用同一时间窗口比较管理卡片与 Prometheus 查询；P50/P95/P99 以秒为单位，舱壁利用率按逐实例比率取最大值。
4. 在维护窗口检查 Prometheus 重启后历史数据仍可读取；停止 Prometheus 时管理指标应降级，HTTP、Console、MCP 仍可用。
5. 独立验证网络访问控制，非受信网络不能访问原始指标或 Prometheus 查询入口。

### 两阶段事件检查

此检查需两个固定的隔离测试实例。故障、依赖失败和舱壁拒绝只在隔离实例制造，不修改真实 OpenSearch 地址、索引或 alias，不向在线实例施加无界并发。

1. 为两个实例配置 targets，各 `instance` 与 `SERVICE_INSTANCE_ID` 一致。检查全程保持服务提交、启动时间和 targets 不变。
2. 在每个实例先初始化五类 Counter series：业务 2xx、固定 `400/40002`、search 依赖失败、global 拒绝、heavy_search 拒绝。等待至少两个抓取周期，随后保持实例安静并捕获基线：

```sh
<venv-python> -m scripts.smoke_admin_metrics <admin-base-url> <loopback-prometheus-url> --minimum-instances 2 --expected-release-commit <deployed-commit> --capture-acceptance-baseline
```

3. 基线成功后，每个实例再产生一轮上述五类事件，等待至少两个抓取周期。使用输出的 `acceptance_baseline_time`，在基线后 15 分钟内运行最终检查：

```sh
<venv-python> -m scripts.smoke_admin_metrics <admin-base-url> <loopback-prometheus-url> --minimum-instances 2 --expected-release-commit <deployed-commit> --acceptance-baseline-time <captured-baseline-time> --require-acceptance-events
```

4. 正式事件完成后到最终命令输出前，不再制造同类事件，不重启、重新部署或修改 targets。完整检查使用默认 `300/900/3600` 秒（5/15/60 分钟）窗口，不用单一诊断窗口替代。

管理员凭据从受控进程环境注入。脚本核对元数据类型、跨实例聚合、正增量、管理数据可见性、新鲜抓取及全过程版本/计数器稳定性，只输出检查布尔值和基线时间。原始事件区间须为 30–900 秒；详细读取顺序由[检查脚本](../../scripts/smoke_admin_metrics.py)及[测试](../../tests/test_internal_prometheus_smoke.py)维护。

只运行不带两阶段参数的 smoke 可检查查询链路和静态聚合，不能证明本轮故障事件已反映到管理看板。guard 抓取后至命令输出前仍需保持实例安静。

## 存储预算告警处置

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
5. 保留新旧版本目录、数据及回滚备份，直到恢复检查完成且确认无需再次恢复。
