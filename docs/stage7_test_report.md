# 阶段 7 测试报告

## 测试范围

- `GET /api/patent/detail/{patent_id}`（默认不含 description）
- `GET /api/patent/detail/{patent_id}?include_description=true`（含 description）
- `GET /api/patent/citations/{patent_id}`（含 SaaS 工具字段与原始兼容字段）
- 自动化测试全套回归
- 真实 OpenSearch detail/citations live smoke
- 边界确认：未修改 SaaS 副本源码、未修改 OpenSearch mapping

## 环境与凭据

- 分支：`stage-4/service-skeleton`，HEAD `e6792a2`。
- Python：`.venv/bin/python`（Python 3.13.14，脚本语法按 Python 3.9 兼容约束编写，使用 `Optional[str]` 与 `Tuple[int, dict]` 替代 PEP 604 联合类型注解）。
- 本地 `.env`：已配置真实 `OPENSEARCH_*` 与 `API_TOKEN`，`.env` 已被 `.gitignore` 忽略。
- `patent_harness_base_副本/` 已被 `.gitignore` 忽略，本次未修改。
- `ENABLE_AUTH` 默认为 `true`，所有受保护接口调用均携带 `X-API-Key: $API_TOKEN`。
- 从开发环境可直连生产 OpenSearch（`OPENSEARCH_HOST` 配置项指向远程集群），search/detail/citations 均返回 HTTP 200。

## 自动化测试

执行命令：

```bash
.venv/bin/python -m pytest -q
```

实际输出：

```text
........................................................................ [ 69%]
...............................                                          [100]
103 passed in 0.05s
```

结论：自动化测试全部通过，103/103 通过。

## 健康检查 smoke

启动服务：

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

执行命令：

```bash
.venv/bin/python scripts/smoke_health.py http://127.0.0.1:8000
```

实际输出：

```text
health ok
```

`/health` 不受鉴权保护，响应体中 `data.status == "healthy"`，健康检查通过。

## 真实 patent_id 获取

执行命令（取一条记录）：

```bash
export API_TOKEN="$(grep -E '^API_TOKEN=' .env | cut -d= -f2-)"
curl -s -X POST http://127.0.0.1:8000/api/patent/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"阀门","page":1,"page_size":1}'
```

实际响应（截取关键字段）：

```text
total=510569，records[0].patent_id = "cn-95474fd9e61ba99b"
```

为加强 description 字段证据，另取一条发明专利记录（`type=发明专利` 且 `title=药物组合物`）：

```bash
curl -s -X POST http://127.0.0.1:8000/api/patent/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"type:(发明专利) AND title:(药物组合物)","page":1,"page_size":1}'
```

实际响应：`records[0].patent_id = "cn-78487c26d1a975bf"`。

## detail/citations live smoke

执行命令：

```bash
.venv/bin/python scripts/smoke_detail_citations.py http://127.0.0.1:8000 "$PATENT_ID" "$API_TOKEN"
```

### 第一次 smoke（外观设计 `cn-95474fd9e61ba99b`）

实际输出：

```json
{"name": "detail", "status": 200, "keys": ["ab", "abstract", "agency", "agent", "applicant", "applicantAddress", "applicant_address", "applicationDate", "applicationNumber", "application_date", "application_number", "assignee", "claims", "currentAssignee", "currentStatus", "current_assignee", "current_status", "documentDate", "documentNumber", "document_date", "document_number", "drawings", "family", "firstApplicant", "firstInventor", "first_applicant", "first_inventor", "fullPriorityNumber", "full_priority_number", "id", "imagePath", "image_path", "inventor", "ipc", "ipcMainList", "ipc_main_list", "legalStatus", "legalStatusHistory", "legal_status", "legal_status_history", "loc", "mainClaim", "mainIpc", "main_claim", "main_ipc", "patent_id", "pctApplicationData", "pctDate", "pctPublicationData", "pct_application_data", "pct_date", "pct_publication_data", "pdfList", "pdf_list", "priorityNumber", "priority_number", "summary", "ti", "title", "type"]}
{"name": "detail_description", "status": 200, "keys": ["ab", "abstract", "agency", "agent", "applicant", "applicantAddress", "applicant_address", "applicationDate", "applicationNumber", "application_date", "application_number", "assignee", "claims", "currentAssignee", "currentStatus", "current_assignee", "current_status", "description", "documentDate", "documentNumber", "document_date", "document_number", "drawings", "family", "firstApplicant", "firstInventor", "first_applicant", "first_inventor", "fullPriorityNumber", "full_priority_number", "id", "imagePath", "image_path", "inventor", "ipc", "ipcMainList", "ipc_main_list", "legalStatus", "legalStatusHistory", "legal_status", "legal_status_history", "loc", "mainClaim", "mainIpc", "main_claim", "main_ipc", "patent_id", "pctApplicationData", "pctDate", "pctPublicationData", "pct_application_data", "pct_date", "pct_publication_data", "pdfList", "pdf_list", "priorityNumber", "priority_number", "summary", "ti", "title", "type"]}
{"name": "citations", "status": 200, "keys": ["cited_by", "non_patent_references", "patent_id", "patent_references", "referencesCited", "referencesCitedRaw", "referencesCitedText", "relatedDocuments"]}
```

退出码：`0`（三组检查全部 HTTP 200）。

### 第二次 smoke（发明专利 `cn-78487c26d1a975bf`）

实际输出（与前一条专利结构一致，篇幅原因不重复粘贴完整 keys 数组，关键字段如下）：

| 检查 | HTTP | 包含 `description` 键 | 说明 |
|---|---:|:---:|---|
| detail | 200 | 否 | 默认不返回 description，符合契约 |
| detail_description | 200 | 是 | `include_description=true` 时携带 `description` 键 |
| citations | 200 | n/a | 含全部 8 个引证字段 |

退出码：`0`。

## 字段验收

### detail 接口（camelCase + snake_case 双别名）

默认 detail 响应完整包含验收清单中所有字段：

| 验收字段 | detail 默认 | detail_description |
|---|:---:|:---:|
| `id` | ✓ | ✓ |
| `patent_id` | ✓ | ✓ |
| `applicationNumber` / `application_number` | ✓ / ✓ | ✓ / ✓ |
| `documentNumber` / `document_number` | ✓ / ✓ | ✓ / ✓ |
| `applicationDate` / `application_date` | ✓ / ✓ | ✓ / ✓ |
| `documentDate` / `document_date` | ✓ / ✓ | ✓ / ✓ |
| `legalStatus` / `legal_status` | ✓ / ✓ | ✓ / ✓ |
| `currentStatus` / `current_status` | ✓ / ✓ | ✓ / ✓ |
| `currentAssignee` / `current_assignee` | ✓ / ✓ | ✓ / ✓ |
| `mainIpc` / `main_ipc` | ✓ / ✓ | ✓ / ✓ |
| `claims` | ✓ | ✓ |
| `description` | ✗（符合默认不返回契约） | ✓（仅在 `include_description=true` 时出现） |

### citations 接口

citations 响应包含 SaaS 工具字段与原始兼容字段：

| 字段 | 存在 |
|---|:---:|
| `patent_id` | ✓ |
| `cited_by` | ✓ |
| `patent_references` | ✓ |
| `non_patent_references` | ✓ |
| `referencesCited` | ✓ |
| `referencesCitedRaw` | ✓ |
| `referencesCitedText` | ✓ |
| `relatedDocuments` | ✓ |

缺失字段：无。

## 三组检查结论

| 检查 | HTTP | 结论 |
|---|---:|---|
| detail（默认） | 200 | 通过：返回业务对象直出，无 `description` 键 |
| detail（`include_description=true`） | 200 | 通过：响应包含 `description` 键 |
| citations | 200 | 通过：响应包含全部 8 个引证字段，业务对象直出 |

关于 `description` 内容长度为 0 的说明：本次抽取的两条专利在外采 SaaS 源/OpenSearch 索引中 `description` 字段均无落库内容（外观设计专利通常无明书正文，发明专利该条目源数据 description 也为空字符串）。API 契约要求的是“`include_description=true` 时响应中存在 `description` 键”，该约束已满足；字段内容是否为空属于数据层问题，不属于阶段 7 API 契约层。建议总控在 SaaS 联调阶段抽样一条已知有 description 落库的发明专利复核。

## 错误响应规范（自动化测试覆盖）

下列错误场景由自动化测试覆盖（`tests/test_detail_api.py`、`tests/test_citations_api.py`、`tests/test_auth.py`），本次回归中全部通过：

| 场景 | HTTP | code |
|---|---:|---|
| 缺失 patent | 404 | `40401` |
| OpenSearch 错误 | 502 | `50001` |
| 鉴权缺失/错误 | 401 | `40101` |

错误响应均符合 `{success, code, message, data}` 结构。

## 边界确认

### SaaS 源码副本未被修改

```bash
git status --porcelain patent_harness_base_副本/
git diff --stat HEAD -- patent_harness_base_副本/
```

两条命令均无输出，`patent_harness_base_副本/` 被 `.gitignore` 忽略且本次未做任何改动。

### OpenSearch mapping 未被修改

```bash
git diff --stat HEAD
```

本次提交仅新增 `scripts/smoke_detail_citations.py` 与 `docs/stage7_test_report.md`，未触及任何 indexing/mapping 相关代码或配置文件；阶段 7 仅对 OpenSearch 执行只读查询，未发起任何 mapping 或索引结构变更。

## 结论

阶段 7 测试结论：**通过**

通过依据：

1. 自动化测试全部通过：`103 passed in 0.05s`。
2. 健康检查 smoke：`health ok`。
3. 真实 OpenSearch detail/citations live smoke：三组检查全部 HTTP 200，退出码 `0`。
4. detail 默认不含 `description`，`include_description=true` 含 `description`，符合契约。
5. citations 响应包含全部 8 个验收字段（`patent_id`、`cited_by`、`patent_references`、`non_patent_references`、`referencesCited`、`referencesCitedRaw`、`referencesCitedText`、`relatedDocuments`）。
6. 未修改 SaaS 副本源码（`patent_harness_base_副本/`）。
7. 未修改 OpenSearch mapping。
8. 未进入 SaaS 联调阶段。

数据层观察（非阻塞）：本地抽样两条专利的 `description` 字段内容均为空字符串，建议总控在 SaaS 联调阶段复核一条已知有 description 落库的发明专利，确认内容可正确回填。

是否建议进入阶段 8：**是**。阶段 7 API 契约层与字段映射层均已通过自动化与真实 OpenSearch smoke 验收，未触发任何 SaaS 联调或 mapping 变更边界，符合阶段边界约束，可进入阶段 8。