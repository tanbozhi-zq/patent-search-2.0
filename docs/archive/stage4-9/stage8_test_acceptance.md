# 阶段 8 测试验收单

## 测试目标

验证阶段 8 已将接口兼容与异常处理收口到联调前可用状态，重点覆盖：

1. 参数校验错误统一。
2. 查询语法错误不访问 OpenSearch。
3. OpenSearch 异常统一包装。
4. 鉴权错误结构稳定。
5. search/detail/citations 成功响应不回退。
6. SaaS PatentHub 工具契约差异已明确记录。

## 自动化测试

必须通过：

```bash
.venv/bin/python -m pytest -q
```

重点测试文件：

1. `tests/test_search_api.py`
2. `tests/test_detail_api.py`
3. `tests/test_citations_api.py`
4. `tests/test_security.py`
5. `tests/test_search_service.py`
6. `tests/test_opensearch_repo.py`
7. 阶段 8 新增的参数校验与异常处理测试

## 参数错误验收

以下请求必须返回统一错误结构：

```json
{
  "success": false,
  "code": 40002,
  "message": "...",
  "data": null
}
```

| Case | Request Body | Expected |
|---|---|---|
| missing q | `{}` | HTTP 400，code `40002` |
| empty q | `{"q": ""}` | HTTP 400，code `40002` |
| q too long | `{"q": "<1001 chars>"}` | HTTP 400，code `40002` |
| invalid ds | `{"q":"阀门","ds":"xx"}` | HTTP 400，code `40002` |
| invalid sort | `{"q":"阀门","sort":"rank"}` | HTTP 400，code `40002` 或文档固定后的 code |
| invalid page | `{"q":"阀门","page":0}` | HTTP 400，code `40003` |
| invalid page_size | `{"q":"阀门","page_size":101}` | HTTP 400，code `40003` |
| invalid highlight | `{"q":"阀门","highlight":2}` | HTTP 400，code `40002` |
| invalid analyzer mode | `{"q":"阀门","index_analyzer_mode":"broken"}` | HTTP 400，code `40002` |

验收要求：

1. 不再返回 FastAPI 默认 `422 detail` 数组结构。
2. 错误响应体必须包含 `success`、`code`、`message`、`data`。
3. 错误响应不得再额外包裹在外层 `detail` 字段中。
4. 错误消息不能暴露内部堆栈或敏感配置。

## 查询语法错误验收

以下请求必须返回 HTTP 400，业务 code `40001`，且测试中不得调用真实 OpenSearch：

```text
ipc:H02M AND AND tscd:(均衡)
AND tscd:(均衡)
tscd:(均衡) OR
tscd:("均衡)
tscd:()
ipc:
foo:(均衡)
ad:[2020-13-01 TO 2020-12-31]
documentYear:[2024 TO 2020]
NOT
```

响应体必须直接为 `{success, code, message, data}`，不得再要求调用方从 `response.detail.code` 中取业务码。

## 鉴权验收

开启 `ENABLE_AUTH=true` 时：

| Case | Expected |
|---|---|
| 不传 `X-API-Key` | HTTP 401，code `40101` |
| 传错误 `X-API-Key` | HTTP 401，code `40101` |
| 传正确 `X-API-Key` | 可访问业务接口 |

关闭 `ENABLE_AUTH=false` 时：

1. `/health` 正常返回。
2. search/detail/citations 可不传 `X-API-Key`。

## OpenSearch 异常验收

通过 mock repository 或模拟异常验证：

| Interface | Expected |
|---|---|
| `POST /api/patent/search` | HTTP 502，code `50001` |
| `GET /api/patent/detail/{patent_id}` | HTTP 502，code `50001` |
| `GET /api/patent/citations/{patent_id}` | HTTP 502，code `50001` |

响应体必须直接为 `{success, code, message, data}`，且不得包含 OpenSearch 密码、内部连接串中的账号密码或 Python traceback。

## 内部异常验收

通过测试路由或 mock 服务验证未分类异常：

| Case | Expected |
|---|---|
| 未捕获服务异常 | HTTP 500，code `50002` |

响应体必须直接为 `{success, code, message, data}`，且不得包含内部异常消息、连接串、账号密码或 Python traceback。

## 成功响应回归

以下接口成功响应结构不得回退：

1. `POST /api/patent/search` 返回 `total`、`page`、`page_size`、`records`。
2. search record 保留 `patent_id`、`applicationNumber`、`documentNumber`、`title`、`abstract`、`applicant`、`currentAssignee`、`mainIpc`、`ipcMainList`、`applicationDate`、`documentDate`、`legalStatus`、`type`。
3. `GET /api/patent/detail/{patent_id}` 默认不返回 `description`。
4. `GET /api/patent/detail/{patent_id}?include_description=true` 返回 `description`。
5. `GET /api/patent/citations/{patent_id}` 返回 `patent_id`、`cited_by`、`patent_references`、`non_patent_references`、`referencesCited`、`referencesCitedRaw`、`referencesCitedText`、`relatedDocuments`。

## 真实 OpenSearch Smoke

服务启动后至少执行：

```bash
.venv/bin/python scripts/smoke_health.py http://127.0.0.1:8000
.venv/bin/python scripts/smoke_search.py http://127.0.0.1:8000 "$API_TOKEN"
.venv/bin/python scripts/smoke_detail_citations.py http://127.0.0.1:8000 "$PATENT_ID" "$API_TOKEN"
```

Smoke 通过标准：

1. `/health` 返回 healthy。
2. search 能取得真实 `patent_id`。
3. detail 默认、detail 带 description、citations 均返回 HTTP 200。
4. 错误场景 smoke 至少覆盖一个参数错误、一个查询语法错误、一个未授权错误。

## 通过标准

1. 自动化测试全部通过。
2. 参数校验错误不再返回默认 422 结构。
3. 业务错误不再返回外层 `detail` 包装，错误响应符合 `docs/api_spec.md`。
4. 成功响应字段不回退。
5. SaaS 副本源码未修改。
6. OpenSearch mapping 未修改。
7. 阶段 8 测试报告落到 `docs/stage8_test_report.md`。
8. 总控确认可进入阶段 9 外采服务对比。
