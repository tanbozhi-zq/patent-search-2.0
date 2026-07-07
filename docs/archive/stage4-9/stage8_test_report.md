# 阶段 8 测试报告

## 测试角色

本次按 `agents.md` 中“测试人员”职责执行：依据项目文档验证接口、查询语法、字段映射、异常场景、外采服务对比边界和 SaaS 联调风险，并输出测试结论或问题清单。

## 测试范围

阶段 8 本次开发覆盖：

1. 参数校验错误统一为扁平 `{success, code, message, data}` 结构。
2. `service_error` 不再通过外层 `detail` 暴露给调用方。
3. search OpenSearch 仓库异常统一包装为 HTTP 502 / `50001`。
4. search 记录补充 snake_case 兼容别名。
5. 文档固定 `page_size` 最大值 100，`highlight=1` 仅兼容接收。

## 自动化测试

执行命令：

```bash
.venv/bin/python -m pytest -q
```

执行结果：

```text
114 passed in 0.07s
```

重点覆盖：

| 测试文件 | 结论 |
|---|---|
| `tests/test_error_handlers.py` | 通过，参数错误返回 HTTP 400，普通参数 `40002`，分页参数 `40003` |
| `tests/test_search_api.py` | 通过，查询语法错误返回 `40001`，OpenSearch 异常返回 `50001` |
| `tests/test_search_service.py` | 通过，仓库异常包装为 `OpenSearchQueryError` |
| `tests/test_search_result_mapper.py` | 通过，search 记录包含 snake_case 兼容别名 |
| `tests/test_detail_api.py` | 通过，详情错误响应为扁平结构 |
| `tests/test_citations_api.py` | 通过，引证错误响应为扁平结构 |
| `tests/test_security.py` | 通过，鉴权错误结构保持 `40101` |

## 参数错误矩阵

使用 `TestClient` 对主路由 `POST /api/patent/search` 执行阶段 8 参数错误矩阵，并覆盖 `get_search_service` 与 `require_api_key`，避免访问真实 OpenSearch。

| Case | HTTP | code | 是否含外层 `detail` |
|---|---:|---:|---|
| missing q | 400 | `40002` | 否 |
| empty q | 400 | `40002` | 否 |
| q too long | 400 | `40002` | 否 |
| invalid ds | 400 | `40002` | 否 |
| invalid sort | 400 | `40002` | 否 |
| invalid page | 400 | `40003` | 否 |
| invalid page_size low | 400 | `40003` | 否 |
| invalid page_size high | 400 | `40003` | 否 |
| invalid highlight | 400 | `40002` | 否 |
| invalid analyzer mode | 400 | `40002` | 否 |

结论：参数错误均返回扁平 `{success, code, message, data}` 结构，不再返回 FastAPI 默认 `422 detail` 数组，也没有外层 `detail` 包装。

## Smoke 测试

执行命令：

```bash
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
.venv/bin/python scripts/smoke_health.py http://127.0.0.1:8000
.venv/bin/python scripts/smoke_search.py http://127.0.0.1:8000 "$API_TOKEN"
.venv/bin/python scripts/smoke_detail_citations.py http://127.0.0.1:8000 cn-95474fd9e61ba99b "$API_TOKEN"
```

执行结果：

```text
health ok
search ok q=tscd:(均衡) total=413318 records=1
search ok q=ipc:H02M AND tscd:(均衡) total=5122 records=1
search ok q=applicant:(华为技术有限公司) total=45186249 records=1
search ok q=currentAssignee:(华为技术有限公司) total=45187007 records=1
search ok q=legalStatus:(有效专利) total=27284721 records=1
search ok q=documentYear:[2020 TO 2024] total=28854473 records=1
search ok q=ipc:H02M AND NOT tscd:(均衡) total=202370 records=1
search ok q=NOT title:(外观) total=65649968 records=1
detail status=200
detail_description status=200
citations status=200
```

说明：首次验证时发现 8000 端口仍运行旧进程，真实请求仍返回 FastAPI 默认 `detail` 包装且 search 记录缺少阶段 8 snake_case 别名；重启当前工作区服务后复测通过。当前可用服务地址为 `http://127.0.0.1:8000`，调试前端地址为 `http://127.0.0.1:8000/test/`。

## 真实 OpenSearch 链路

使用 `.env` 中真实 OpenSearch 配置和 `API_TOKEN` 验证：

| Case | 结果 |
|---|---|
| `POST /api/patent/search`，q=`阀门` | HTTP 200，`total=510569`，返回 1 条记录 |
| search record 字段 | `application_number`、`document_number`、`application_date`、`document_date`、`legal_status`、`main_ipc` 均存在 |
| `GET /api/patent/detail/cn-95474fd9e61ba99b` | HTTP 200，默认不含 `description` |
| `GET /api/patent/detail/cn-95474fd9e61ba99b?include_description=true` | HTTP 200，包含 `description` |
| `GET /api/patent/citations/cn-95474fd9e61ba99b` | HTTP 200，引用字段齐全 |
| `highlight=1` 搜索 | HTTP 200，仅兼容接收，不返回高亮片段 |

## 错误码结论

| 场景 | HTTP | code |
|---|---:|---:|
| 查询语法错误 | 400 | `40001` |
| 普通参数非法 | 400 | `40002` |
| `page` / `page_size` 非法 | 400 | `40003` |
| 鉴权缺失或错误 | 401 | `40101` |
| 专利不存在 | 404 | `40401` |
| OpenSearch 查询异常 | 502 | `50001` |
| 服务内部异常 | 500 | `50002` |
| 默认路由不存在 | 404 | `40400` |

## 兼容边界

1. search 成功响应继续返回 `records`，不改成 PatentHub 工具层 `patents`。
2. search 记录新增 `application_number`、`document_number`、`application_date`、`document_date`、`legal_status`、`main_ipc`。
3. `page_size` 最大值固定为 100。
4. `sort` 当前仅支持 `relation`、`!applicationDate`。
5. `highlight=1` 当前仅兼容接收，不返回高亮片段。
6. OpenSearch 异常响应不暴露内部连接串、账号密码或 Python traceback。

## 边界检查

`patent_harness_base_副本/` 未修改；本阶段未修改 OpenSearch mapping，未重建索引。

执行命令：

```bash
git status --short patent_harness_base_副本/
git diff --stat HEAD -- patent_harness_base_副本/
```

执行结果：两条命令均无输出。

## 代码 Review 结论

未发现阶段 8 阻塞性问题。实现与文档约定一致：

1. `app/core/error_handlers.py` 将 `HTTPException` 与 `RequestValidationError` 统一转换为扁平错误结构。
2. `app/api/search.py` 使用 `service_error` 返回 `40001` / `50001`，不再暴露外层 `detail`。
3. `app/services/search_service.py` 将仓库查询异常包装为 `OpenSearchQueryError`，查询语法错误仍在访问仓库前返回。
4. `app/mappings/result_mapper.py` 保留既有 camelCase 字段并补充阶段 8 要求的 snake_case 兼容别名。
5. `docs/api_spec.md`、`docs/query_syntax.md` 和 `README.md` 已同步 `page_size=100`、`highlight=1` 仅兼容接收、错误码和兼容边界。

## 结论

阶段 8 本地自动化测试、真实 OpenSearch search/detail/citations smoke、参数错误矩阵、鉴权错误和查询语法错误验证均通过。接口错误结构与兼容边界已同步到文档，可交由项目总控确认是否进入阶段 9 外采服务对比。

---

## 项目总控复核

复核时间：2026-07-06

复核补充项：

1. 补齐未捕获服务异常兜底：HTTP 500 / code `50002`，响应不暴露内部异常消息。
2. 补齐默认路由不存在场景：HTTP 404 / code `40400`，响应不再返回外层 `detail`。
3. 将阶段 8 派工单和验收单中的开放措辞收束为已冻结决策：
   - search 记录补充 snake_case 别名。
   - `page_size` 最大值固定为 100。
   - `page` / `page_size` 非法固定返回 `40003`。
   - `highlight=1` 仅兼容接收，不返回高亮片段。

复核命令：

```bash
.venv/bin/python -m pytest -q
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8001
.venv/bin/python scripts/smoke_health.py http://127.0.0.1:8001
.venv/bin/python scripts/smoke_search.py http://127.0.0.1:8001 "$API_TOKEN"
.venv/bin/python scripts/smoke_detail_citations.py http://127.0.0.1:8001 "$PATENT_ID" "$API_TOKEN"
```

复核结果：

```text
114 passed in 0.07s
health ok
search smoke 全部通过
detail status=200
detail_description status=200
citations status=200
invalid_page_size -> HTTP 400 / code 40003
invalid_query -> HTTP 400 / code 40001
highlight=1 -> HTTP 200，无高亮片段
missing_route -> HTTP 404 / code 40400
```

总控结论：阶段 8 **验收通过**。允许进入阶段 9 外采服务对比；进入阶段 9 前应先补阶段 9 设计文档，再派发开发/测试任务。
