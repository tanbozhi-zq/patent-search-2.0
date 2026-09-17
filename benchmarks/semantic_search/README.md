# 语义检索固定性能矩阵

该工具在隔离的 loopback OpenSearch 3.3 上创建临时索引和临时 Search
Pipeline，再启动当前 Git commit 的 FastAPI 应用，通过真实
`POST /api/patent/search` 执行固定矩阵。它不直接调用 DSL Builder 或
OpenSearch `_search`来代替服务链路。

## 矩阵

- mode：`vector`、`hybrid`；
- 字段数：单字段、两字段；
- sort：相关性、申请日降序；
- `top_k`：20、100；
- 固定 `ds=cn,page=1,page_size=20`；
- 3 条版本化公开 fixture 查询，每个 case 记录 1 次 warm-up 和至少 3 次正式样本。

共 16 个 case。正式轮次按 query/round 轮换 case 顺序，降低固定顺序对结果的偏置。
warm-up 不进入分位数，但原始 observation 会保留并参与成功/失败判定。

## 运行边界

只允许对 `cluster_name=issue75-semantic-fixture` 的 loopback HTTP
OpenSearch 3.3.x 运行，并必须显式传
`--allow-controlled-writes`。工具拒绝覆盖现有输出，也拒绝覆盖任何同名
Search Pipeline。工作树必须是 clean commit，且运行期间 HEAD/源码不能变化。
所有受控 HTTP 客户端忽略代理环境；FastAPI 使用预检为空闲的 loopback 端口和
单 worker，并固定查询预算。结束时删除本次临时索引和本次新建的 pipeline，
并做 404 回读。

查询向量使用本地确定性 fixture adapter，以去除外部 Ark 波动；因此结果
不包含 Ark 网络/排队延迟。fixture 映射为 1024 维（与脚本 `VECTOR_DIMENSIONS` 一致） `cosinesimil`，但引擎是
Lucene HNSW，不是生产 DiskANN/RaBitQ。该串行小样本只是方向性工程证据，
不是 SLA、生产容量或相关性验收，也不代替受控 OpenSearch 正确性集成测试。

## 复现

```bash
.venv/bin/python -m benchmarks.semantic_search.semantic_search_performance \
  --opensearch-url http://127.0.0.1:19200 \
  --rounds 3 \
  --documents 600 \
  --output benchmarks/semantic_search/results/<run>.json \
  --allow-controlled-writes
```

输出使用独占创建和 `0600` 权限，记录精确 commit、dirty 状态、OpenSearch
版本/build hash、机器/Python 环境、查询集和源码指纹、每个样本的 HTTP
状态/稳定 code/wall time/OpenSearch `took`，以及每个 case 的
min/P50/P95/P99/max。任一 warm-up 或正式样本失败、或临时资源清理未通过时返回非零。

结果不保存 `q`、`semantic_text`、向量、精确字段组合、专利记录/ID、
URL、provider endpoint、凭据、request ID、响应原文或异常原文。
