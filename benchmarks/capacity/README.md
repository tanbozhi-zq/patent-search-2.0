# 并发容量与过载验收

本目录提供容量和过载验证工具。`query_plan.json` 是合成演示查询，不代表真实业务负载；容量验收应使用部署方自行准备的授权查询集。

## 安全边界

这些脚本对 OpenSearch 只调用节点统计读接口，但会通过检索服务产生真实
查询负载。因此：

- 仅在已批准的测试实例和时间窗口运行；
- 运行前核对部署版本、别名物理目标、分片分配、正在进行的写入/合并和集群健康；
- 阶梯测试使用单实例、单 worker；全部请求都是重搜索，所以重请求子容量必须覆盖
  当前发压并发，全局容量必须严格大于重请求子容量，Client 槽位不得低于全局容量。
  若同一配置要跑到 10 路，至少需要重请求容量 10、全局容量 11，并先验证相应
  Client 槽位；脚本会拒绝不可能在真实服务上成立的配置；
- 每次只运行一个档位，脚本不会自动继续到下一档；
- 不得因为脚本输出 `safe_to_continue=true` 而跳过人工核对。

API Token 和 OpenSearch 凭据只从进程环境读取，不写入仓库或结果文件。采样账号
需要 `cluster:monitor/nodes/stats` 权限。

## 停止策略

[`capacity_policy.json`](capacity_policy.json) 是真实测试前必须人工复核的候选策略。
默认采样间隔为 2 秒，任一条件命中都不得继续下一档：

- 应用请求出现失败或 `50301`；
- OpenSearch 节点指标采样失败、返回空节点集合或缺失必需指标；
- 节点 CPU 超过 80% 或 Heap 超过 65%；
- search queue 连续 3 个采样点非零（约 6 秒）；
- search rejected、breaker trip 或 search backpressure cancellation 计数增长；
- 观测窗口结束后 search active/queue 仍未归零；
- 相比前一档，吞吐增长低于 15%，同时 P95 或 P99 增长超过 30%。

阶梯阶段故意不压过容量，用于找吞吐/尾延迟拐点；`50301` 端到端 P99 由后面的
独立过载阶段验证，不能用阶梯阶段的零拒绝替代。

轻重混合场景另要求轻请求全部成功，且 P95 不得超过其单独基线的 2 倍；该比例也是
真实运行前需结合业务 SLO 复核的候选门槛。

CPU/Heap 线低于 OpenSearch Search Backpressure 默认的 90%/70% node-duress 阈值，
用于在 OpenSearch 真正开始保护前停止应用压测。指标来自 OpenSearch 官方
[`_nodes/stats`](https://docs.opensearch.org/latest/api-reference/nodes-apis/nodes-stats/)
和 [Search Backpressure Stats API](https://docs.opensearch.org/latest/tuning-your-cluster/availability-and-recovery/search-backpressure/)。

## 阶梯并发测试

脚本会为每个档位生成：

- 逐请求 JSONL；
- 逐采样 OpenSearch JSONL；
- 带有吞吐、P50/P95/P99、错误率、Client 侧峰值在途数、`50301` 和集群指标的汇总；
- `safe_to_continue`、停止原因以及与前一档的拐点比较。

`request_metrics.wall_seconds` 从 OpenSearch 指标预检成功后、正式提交查询前开始；
同步预检耗时不计入吞吐量或相邻档容量拐点比较。`run.started_at` 保留完整监控窗口，
`run.workload_started_at` 记录工作负载计时起点。

第一档示例：

```bash
.venv/bin/python -m benchmarks.capacity.replay_queries \
  --plan benchmarks/capacity/query_plan.json \
  --policy benchmarks/capacity/capacity_policy.json \
  --base-url http://127.0.0.1:8000 \
  --opensearch-url https://127.0.0.1:9200 \
  --concurrency 4 \
  --timeout 300 \
  --output-dir /tmp/patent-capacity \
  --service-version <candidate-version> \
  --service-commit <candidate-commit> \
  --read-target <verified-physical-index> \
  --bulkhead-capacity <global-capacity> \
  --heavy-bulkhead-capacity <heavy-capacity-covering-current-level> \
  --opensearch-pool-maxsize <validated-pool-size> \
  --worker-count 1
```

只有该次汇总的 `decision.safe_to_continue=true` 且人工核对通过后，才能运行 6 路。
6 路开始后需用 `--previous-summary` 指向已通过的 4 路汇总；8 和 10 路以此类推。
脚本会拒绝使用未通过、并发不低于当前档，或查询计划、策略、服务提交、
读目标、全局/重请求舱壁容量、Client 容量和 worker 数不一致的前置汇总。

如果操作方明确接受上一档测试窗口开始前就存在的 Heap 高基线，可以额外传入
`--previous-heap-guardrail-override-reason <原因>`。该入口只接受上一档唯一停止原因为
`opensearch_heap_guardrail` 的情况，放行原因会写入新汇总；请求失败、舱壁拒绝、队列、
熔断、backpressure 取消或容量拐点均不能通过该参数覆盖。

`client_peak_in_flight` 只是发压端观测，不得冒充应用进程内的在途数。应用侧证据
来自舱壁日志中的 `name`、`in_flight`、`peak_in_flight` 和 `rejected_total`。按汇总中的
`started_at`/`finished_at` 导出对应日志窗口后执行：
其中顶层 `max_in_flight` 是所有层日志的最大单层值，`bulkheads.global` 和
`bulkheads.heavy_search` 分别给出两层证据；`peak_in_flight` 和 `rejected_total` 是
进程自启动以来的累计值，不能单独当作本次运行增量。本次拒绝数以日志窗口内的
`rejected_event_count` 为准。

```bash
.venv/bin/python -m benchmarks.capacity.summarize_bulkhead_log \
  --log /tmp/patent-capacity/service-window.log \
  --output /tmp/patent-capacity/bulkhead-summary.json
```

## 轻重混合负载

混合工具会先单独测试详情、引证和法律状态，再让 broad/BQP 搜索达到指定
并发后重复轻请求，并确认请求时间真正重叠。`--patent-id` 必须是测试前已验证
三个轻量接口都能成功的固定标识符。

```bash
.venv/bin/python -m benchmarks.capacity.mixed_workload \
  --plan benchmarks/capacity/query_plan.json \
  --policy benchmarks/capacity/capacity_policy.json \
  --base-url http://127.0.0.1:8000 \
  --opensearch-url https://127.0.0.1:9200 \
  --patent-id <verified-patent-id> \
  --heavy-concurrency <heavy-capacity> \
  --light-concurrency <reserved-light-capacity> \
  --output-dir /tmp/patent-mixed \
  --service-version <candidate-version> \
  --service-commit <candidate-commit> \
  --read-target <verified-physical-index> \
  --bulkhead-capacity <global-capacity> \
  --heavy-bulkhead-capacity <heavy-capacity> \
  --opensearch-pool-maxsize <validated-pool-size> \
  --worker-count 1
```

`heavy-capacity` 必须严格小于 `global-capacity`；混合工具会在发压前拒绝轻请求并发
高于二者之差的配置，否则测试的是全局过载拒绝而不是预留容量。重查询先获取子许可，再获取
全局许可；轻请求只获取全局许可，因此两个层次不能叠加超过全局上限。如果重叠期间
轻请求仍收到 `50301`，汇总会输出 `requires_workload_isolation=true`。

混合工具的重请求与轻请求客户端并发之和最多为 64；基线轻请求、混合轻请求和
混合重请求合计最多为 256 个服务请求。超出范围会在构造请求列表和线程池之前
直接拒绝；扩大范围需要单独受控评审。客户端、指标超时和采样间隔必须为正数。

## 独立过载与拒绝 P99

阶梯和混合场景都不会故意越过合法容量，因此不能证明真实 `50301` 的端到端
延迟。最终候选配置必须单独运行过载工具。它分两段执行：

1. 用重搜索占满重请求子容量，再发送额外重搜索，验证拒绝来自重请求子舱壁；
2. 用重搜索加详情请求占满全局容量，再发送额外详情请求，验证拒绝来自全局舱壁。

每个过载请求都必须在占位请求仍运行时返回 HTTP 503、`code=50301`、
`retryable=true` 和 `Retry-After: 1`；响应头 `X-Request-ID` 与错误体 `request_id`
还必须同时非空且完全一致。两段拒绝 P99 都不得超过策略中的 100ms。

```bash
.venv/bin/python -m benchmarks.capacity.overload_rejections \
  --plan benchmarks/capacity/query_plan.json \
  --policy benchmarks/capacity/capacity_policy.json \
  --base-url http://127.0.0.1:8000 \
  --opensearch-url https://127.0.0.1:9200 \
  --patent-id <verified-patent-id> \
  --rejection-requests 8 \
  --output-dir /tmp/patent-overload \
  --service-version <candidate-version> \
  --service-commit <candidate-commit> \
  --read-target <verified-physical-index> \
  --bulkhead-capacity <global-capacity> \
  --heavy-bulkhead-capacity <heavy-capacity> \
  --deployed-acquire-timeout-seconds <value-from-deployment-config> \
  --opensearch-pool-maxsize <validated-pool-size> \
  --worker-count 1
```

`--deployed-acquire-timeout-seconds` 会写入汇总并参与 SLO 判定，但最终仍须按运行时间窗
导出服务日志，使用 `summarize_bulkhead_log` 核对两层实际 capacity、
`acquire_timeout_seconds`、拒绝事件、峰值在途数和最终归零。缺少这份服务端证据时，
HTTP 汇总不能单独批准发布。

为避免参数笔误耗尽测试机或冲击服务入口，`--rejection-requests` 只允许 1–32，
全局容量与拒绝请求之和不得超过 64 个客户端线程/连接。需要扩大该范围时必须另建
受控测试流程并重新评审，不能通过当前命令直接绕过。

命令退出码为 0 和 `HTTP_REJECTION_STAGE_PASSED=true` 只表示这一段 HTTP 拒绝
契约与延迟检查通过；它不表示 Issue #34 已完成，也不替代服务端日志、最终阶梯、
轻重混合和人工发布评审。

## 确定容量和等待时间

- 首个命中硬停止条件或出现吞吐/尾延迟拐点的档位不能作为部署值；
- 候选部署值来自拐点之前的安全档位，并需确认正常代表性流量不会频繁命中
  `50301`；
- 如果并发 10 仍未出现拐点，只能说明安全范围至少到 10。扩大 Client 槽位和
  继续更高档次属于新的受控测试；
- `0.01s` 是无排队策略的内部获取候选值。只有独立过载阶段两层真实拒绝的端到端
  P99 都不超过策略中的 100ms，且服务端日志确认实际配置一致，才能作为生产配置；
- 结果只对测试当时的服务进程数、配置、别名目标、分片布局和集群背景负载有效。
  换索引、节点布局或进程数后必须重跑。

全局舱壁仍然只是最后的过载保险，不替代 #45 的按调用方身份公平限流。
