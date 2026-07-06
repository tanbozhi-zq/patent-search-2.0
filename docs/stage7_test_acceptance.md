# 阶段 7 测试验收单

## 测试目标

验证专利详情与引证接口可以读取真实 OpenSearch 数据，并同时满足本项目 HTTP API 风格与 SaaS PatentHub 工具层字段契约。

## 自动化测试

必须通过：

```bash
.venv/bin/python -m pytest -q
```

重点测试文件：

1. `tests/test_detail_mapper.py`
2. `tests/test_citation_mapper.py`
3. `tests/test_detail_service.py`
4. `tests/test_citation_service.py`
5. `tests/test_detail_api.py`
6. `tests/test_citations_api.py`
7. `tests/test_opensearch_repo.py`
8. 既有搜索、查询解析、鉴权测试

## API 验收

| Case | Request | Expected |
|---|---|---|
| detail without description | `GET /api/patent/detail/{patent_id}` | HTTP 200，且不返回 `description` |
| detail with description | `GET /api/patent/detail/{patent_id}?include_description=true` | HTTP 200，且返回 `description` |
| citations | `GET /api/patent/citations/{patent_id}` | HTTP 200，且返回 SaaS 工具字段和原始兼容字段 |
| missing patent | `GET /api/patent/detail/not-found-id` | HTTP 404，code `40401` |
| auth missing | 开启鉴权且不传 `X-API-Key` | HTTP 401，code `40101` |

## 详情字段验收

详情接口必须同时包含：

```text
id
patent_id
applicationNumber
application_number
documentNumber
document_number
applicationDate
application_date
documentDate
document_date
legalStatus
legal_status
currentStatus
current_status
currentAssignee
current_assignee
mainIpc
main_ipc
claims
```

`include_description=false` 时不返回：

```text
description
```

`include_description=true` 时必须返回：

```text
description
```

## 引证字段验收

引证接口必须同时包含：

```text
patent_id
cited_by
patent_references
non_patent_references
referencesCited
referencesCitedRaw
referencesCitedText
relatedDocuments
```

## 真实 OpenSearch Smoke

测试流程：

1. 先用搜索接口获取一个真实 `patent_id`。
2. 调用 detail 默认接口。
3. 调用 detail 带 `include_description=true`。
4. 调用 citations 接口。
5. 记录 HTTP 状态码、返回字段和关键字段是否为空。

推荐命令：

```bash
.venv/bin/python scripts/smoke_detail_citations.py http://127.0.0.1:8000 "$PATENT_ID" "$API_TOKEN"
```

## 通过标准

1. 自动化测试全部通过。
2. 真实 OpenSearch smoke 全部返回 HTTP 200。
3. detail 成功响应为业务对象直出。
4. citations 成功响应为业务对象直出。
5. 错误响应符合 `{success, code, message, data}`。
6. 未修改 SaaS 副本源码。
7. 未修改 OpenSearch mapping。
8. 未进入 SaaS 联调阶段。
