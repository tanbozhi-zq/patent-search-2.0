# 检索服务指标、告警与基础面板

本页只覆盖检索后端进程自身的聚合观测。它不监控 OpenSearch 集群 CPU、Heap、分片或
节点内部状态，也不建设 Prometheus、Alertmanager 或 Grafana 的高可用平台。

## 抓取与访问边界

每个 FastAPI 实例在 `GET /metrics` 导出自己的 Prometheus 文本格式指标。该请求不访问
OpenSearch，不进入查询预算、全局/重请求舱壁或后续按调用方限流。服务继续采用一个
Uvicorn worker 对应一个实例；横向扩容时应抓取每个实例，不能把随机落到多个 worker
之一的抓取结果当成完整进程指标。

`/admin` 与 `/admin-api/*` 也不计入 HTTP 指标，避免管理看板刷新或草案预检稀释业务
成功率和延迟分位数；它们仍保留独立管理审计和 HTTP 完成日志。

`/metrics` 不使用业务 API Key，安全边界与 `/live`、`/startup`、`/ready` 相同：只允许
Prometheus 从私网或受信入口访问。部署前必须在防火墙、服务发现或反向代理层确认公网
无法访问该路径；在网络入口收口验收完成前，不得把裸 `:8000` 端口暴露到非可信网络。

内测部署复用 `deployment/prometheus/prometheus.yml` 和 Prometheus 原生
`file_sd_configs`。仓库固定 job 为 `patent-search`，与管理员看板的
`ADMIN_PROMETHEUS_JOB` 默认值一致；真实目标由服务器受控文件提供，不提交地址或端口：

```yaml
scrape_configs:
  - job_name: patent-search
    scrape_interval: 15s
    scrape_timeout: 2s
    metrics_path: /metrics
    file_sd_configs:
      - files: ["<deployment-only-target-file>"]
```

单实例内测配置保留 30 天、最多 2 GB TSDB blocks，以先达到者为准；具体安装、磁盘
边界、备份和回滚见 `docs/ops/internal_prometheus.md`。生产最终保留期仍应由实际序列量、
磁盘预算和事故回溯窗口共同确定。Prometheus 的 `rule_files` 加载
`deployment/observability/alert_rules.yml`，Alertmanager 接收方必须在平台私有配置中
设置，仓库不保存 webhook、邮箱凭据或 Token。规则上线前执行：

```bash
promtool check rules deployment/observability/alert_rules.yml
promtool test rules deployment/observability/alert_rules_test.yml
```

## 指标契约

| 指标 | 类型 | label | 语义 |
|---|---|---|---|
| `patent_search_http_requests_total` | Counter | `method,route,status,code` | 已完成 HTTP 请求；`route` 只取模板。 |
| `patent_search_http_requests_in_flight` | Gauge | `method` | 当前进程正在执行的 HTTP 请求。 |
| `patent_search_http_request_duration_seconds` | Histogram | `method,route,status,code` | 端到端 HTTP 秒数。 |
| `patent_search_opensearch_calls_total` | Counter | `operation,outcome` | OpenSearch 业务与 readiness 调用结果。 |
| `patent_search_opensearch_call_duration_seconds` | Histogram | `operation,outcome` | OpenSearch 调用秒数。 |
| `patent_search_opensearch_retries_total` | Counter | `operation,outcome` | 按触发原因记录应用显式重试次数。 |
| `patent_search_query_stage_calls_total` | Counter | `stage,mode,vector_field_count,sort_type,ranking_profile,outcome` | 已完成的检索阶段及固定请求分类。 |
| `patent_search_query_stage_duration_seconds` | Histogram | `stage,mode,vector_field_count,sort_type,ranking_profile,outcome` | 查询向量、OpenSearch wall/`took` 和 SearchService 端到端阶段耗时。 |
| `patent_search_bulkhead_capacity` | Gauge | `bulkhead` | `global` 或 `heavy_search` 的单实例容量。 |
| `patent_search_bulkhead_in_flight` | Gauge | `bulkhead` | 单实例已准入在途量。 |
| `patent_search_bulkhead_rejections_total` | Counter | `bulkhead` | 单实例累计拒绝量。 |
| `patent_search_probe_status` | Gauge | `probe` | `live/startup/ready` 最近状态，1 可用、0 不可用。 |
| `patent_search_service_start_time_seconds` | Gauge | 无 | 本实例初始化开始的 Unix 时间。 |
| `patent_search_build_info` | Gauge | `version,commit,tag` | 本实例构建身份；实例名使用 Prometheus scrape 的 `instance` label。 |
| `patent_search_rate_limit_rejections_total` | Counter contract | `scope=caller` | 为 #45 预留；限流未实现前不创建调用方序列。 |

固定枚举中，OpenSearch `operation` 只有 `search/count/readiness`；`outcome` 只有
`success/timeout/connection_error/unavailable/invalid_response/error/unexpected_error`。
检索阶段 `stage` 只有 `query_vector/opensearch/opensearch_took/end_to_end`；
`mode` 只有 `boolean/vector/hybrid`；`vector_field_count` 只有 `0..5`；
`sort_type` 只有 `relevance/date`；`ranking_profile` 只有 `boolean`、
`patent-knn-cosine-v1`、受支持分支数限定的 `patent-vector-rrf-v1-N` 和
`patent-hybrid-rrf-v1-N`；`outcome` 只有
`success/timeout/failure/rejected`。未知值统一收敛为 `other`。
未知 HTTP 方法、状态或错误码收敛为 `other`，未匹配 URL 收敛为
`__unmatched__`；探针/health 的非 2xx 没有业务错误码，因此固定导出 `code=0`，
不会伪装成 `50301` 应用过载。禁止把 request ID、`q`、`semantic_text`、
向量值、精确字段组合、provider endpoint、模型路由、专利号、原始 URL/path、
Token、Cookie、节点地址、异常原文或调用方身份放入 label。

所有 HTTP、OpenSearch 和查询阶段延迟直方图的单位均为秒，bucket 为：

```text
0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10,
30, 60, 120, 180, 240, 300, +Inf
```

服务执行预算最高为 240 秒；额外的 300 秒有限桶用于容纳 deadline 后的取消清理、
错误映射和响应收尾耗时，避免轻微超出 240 秒的观测全部落入 `+Inf`。

四个阶段延迟不能互换：

- `query_vector` 是一个逻辑检索请求的查询向量生成总阶段，多字段不按 provider 调用拆成高基数序列；
- `opensearch` 是 Repository 调用的客户端 wall time；
- `opensearch_took` 只在成功响应中记录 OpenSearch 返回的服务端执行时间；
- `end_to_end` 从 `SearchService.search()` 开始到它返回/失败，它已在 schema、鉴权和舱壁之后；完整 HTTP ingress-to-response 仍看 `patent_search_http_request_duration_seconds`。

schema/鉴权拒绝不会伪造 mode 标签，继续由 HTTP `status/code` 表达；舱壁拒绝由
HTTP `50301` 和既有 bulkhead Counter 表达。这些事件发生在 SearchService 之前。

应用不导出预计算分位数。业务延迟和错误率查询排除 `/live`、`/startup`、`/ready`
和 `/health`，避免控制面轮询稀释或放大业务 SLI；探针状态由独立 gauge 和告警负责。
Prometheus 在查询时从 bucket 计算，例如全实例业务 P95：

```promql
histogram_quantile(
  0.95,
  sum by (le) (
    rate(patent_search_http_request_duration_seconds_bucket{route!~"/(live|startup|ready|health)"}[5m])
  )
)
```

例如查看混合检索的 OpenSearch 客户端 wall P95：

```promql
histogram_quantile(
  0.95,
  sum by (le) (
    rate(patent_search_query_stage_duration_seconds_bucket{
      stage="opensearch",mode="hybrid",outcome="success"
    }[5m])
  )
)
```

## 多实例聚合

Counter 和 Histogram bucket 使用 `sum(rate(...))` 或 `sum(increase(...))` 聚合。Gauge
必须按含义处理：服务总在途可对实例求和；容量利用率先逐实例相除，再取 `max`；ready
用逐实例值或 `min by (job)` 判断是否存在不可用实例。启动时间保持逐实例查看，不能
求和。示例：

```promql
sum(rate(patent_search_http_requests_total[5m]))
sum(patent_search_bulkhead_in_flight) by (bulkhead)
max by (job, bulkhead) (
  patent_search_bulkhead_in_flight / patent_search_bulkhead_capacity
)
min by (job) (patent_search_probe_status{probe="ready"})
patent_search_build_info
```

`version/commit/tag` 是低基数部署身份，不得加入 request ID、节点地址或任意运行时值。
生产验收必须显式注入 commit/tag；`unknown` 只适用于本地或尚未发布的环境。

## 面板导入

在 Grafana 导入 `deployment/observability/grafana_dashboard.json`，选择该环境的
Prometheus 数据源。面板按四个失败边界分组：调用方 4xx、应用过载 `50301`、
OpenSearch 连接错误/不可用/超时/无效响应/内部错误、未知程序异常 `50002`；另含吞吐、P95/P99、舱壁状态、
探针和重启信号。该 JSON 可被 #47 只读看板复用，但不提供用户权限、配置修改、日志
全文检索或回滚按钮。

## Alert response

仓库规则中的阈值都是 canary 待校准候选值，不是已经批准的生产 SLO。舱壁拒绝和
OpenSearch 超时是五分钟滚动事件数规则，窗口一旦超限立即触发，不再叠加额外
`for` 持续时间；因此单次突发与持续异常都可被捕获，窗口恢复后自动解除。告警触发后：

1. 先按 `code` 和 OpenSearch `outcome` 区分调用方错误、应用过载、依赖故障和未知异常。
2. 核对告警实例、当前发布提交、部署时间、探针和同窗口结构化日志，不凭单一图表归因。
3. 先停止 canary 或流量增长；只有回归与当前版本相关时，才按
   `docs/ops/deployment_runbook.md` 的已验证提交执行服务回滚。
4. 不通过临时提高舱壁容量止损；容量调整必须经过 Issue #49 的阶梯压测和保护线。
5. 恢复后确认规则回到 inactive，并记录触发、处置、恢复时间和后续校准结论。

告警接收方、静默和升级链由实际值班平台配置；仓库只保存无凭据的规则与核查入口。
