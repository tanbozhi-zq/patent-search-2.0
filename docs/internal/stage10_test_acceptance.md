# 阶段 10 测试验收单

## 测试目标

验证 SaaS Agent 可以通过适配层调用自研 search/detail/citations，并可通过配置回退外采。

## 自动化测试

必须通过：

```bash
.venv/bin/python -m pytest -q
```

## 自研服务 Smoke

必须通过：

```bash
.venv/bin/python scripts/smoke_health.py http://127.0.0.1:8000
.venv/bin/python scripts/smoke_search.py http://127.0.0.1:8000 "$API_TOKEN"
.venv/bin/python scripts/smoke_detail_citations.py http://127.0.0.1:8000 "$PATENT_ID" "$API_TOKEN"
```

## SaaS 工具联调验收

必须验证：

1. `patent_search` 调用自研 search 成功。
2. `patent_search` 输出顶层 `patents`。
3. `patent_search` 每条记录包含 `id`、`title`、`summary`、`application_number`、`document_number`、`application_date`、`document_date`、`legal_status`、`main_ipc`。
4. `patent_get_detail` 可使用 search 返回的 `id`。
5. `patent_get_detail` 默认不含 `description`。
6. `patent_get_detail(include_description=true)` 包含 `description`。
7. `patent_get_citations` 返回 `cited_by`、`patent_references`、`non_patent_references`。
8. 查询语法错误可被工具层识别为 `{error, code}`。
9. 鉴权错误可被工具层识别为 `{error, code}`。
10. 配置开关可回退外采。

## 灰度验收

至少确认：

1. 灰度范围为测试环境、单 Agent 或单测试账号。
2. 自研和外采切换有明确配置。
3. 失败时可在不改代码的情况下回退外采。
4. 联调日志可定位请求 ID、查询式、错误码和响应摘要。

## 通过标准

1. 自动化测试通过。
2. 自研服务 smoke 通过。
3. SaaS 工具联调 smoke 通过。
4. 错误响应可被 Agent 稳定消费。
5. 回退外采开关验证通过。
6. `docs/stage10_integration_report.md` 已输出。
7. 总控确认是否进入阶段 11。

