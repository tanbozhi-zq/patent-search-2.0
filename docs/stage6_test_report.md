# 阶段六测试报告

## 测试范围

- 完整布尔查询解析器
- 字段查询和范围查询
- `AND` / `OR` / `NOT`
- 括号优先级
- 非法查询返回 `40001`
- 阶段五回归
- 真实 OpenSearch 冒烟

## 自动化测试

- 命令：`.venv/bin/python -m pytest -q`
- 结果：`62 passed in 0.03s`（62/62 通过）

## 冒烟测试

### 健康检查

- 命令：`.venv/bin/python scripts/smoke_health.py http://127.0.0.1:8000`
- 结果：`health ok`（HTTP 200，`/health` 不鉴权）

### 搜索冒烟

- 命令：`.venv/bin/python scripts/smoke_search.py http://127.0.0.1:8000`（未传 `API_TOKEN`）
- 结果：每个查询均返回 HTTP `401`，`search fail q=<q> status=401`：

```text
search fail q=tscd:(均衡) status=401
search fail q=ipc:H02M AND tscd:(均衡) status=401
search fail q=applicant:(华为技术有限公司) status=401
search fail q=currentAssignee:(华为技术有限公司) status=401
search fail q=legalStatus:(有效专利) status=401
search fail q=documentYear:[2020 TO 2024] status=401
search fail q=ipc:H02M AND NOT tscd:(均衡) status=401
```

说明：本地 `.env` 未配置 `API_TOKEN`，且 `ENABLE_AUTH` 默认开启，因此搜索接口在鉴权层即返回 `401`，符合预期。该 401 不属于任务失败：脚本逻辑正确，真实 `200` 响应需要在 `.env` 配置有效 `API_TOKEN` 且 OpenSearch 可达的 opt-in 环境下复现。

## 结论

- 通过条件：自动化测试通过（62/62），非法输入不访问 OpenSearch（`40001` 单元测试覆盖）。
- 真实 OpenSearch 冒烟：当前环境为 opt-in 未配置态，鉴权层返回 `401`，脚本与鉴权行为均正确；待配置 `API_TOKEN` 与可达 OpenSearch 后预期返回 `200`。
- 未覆盖范围：阶段七详情、引证、SaaS 联调、生产网关。
