# 阶段 7 代码审查报告

## 审查范围

- `app/api/detail.py`
- `app/api/citations.py`
- `app/services/detail_service.py`
- `app/services/citation_service.py`
- `app/repositories/opensearch_repo.py`
- `app/mappings/detail_mapper.py`
- `app/mappings/citation_mapper.py`
- `app/core/exceptions.py`
- `app/main.py`
- `scripts/smoke_detail_citations.py`
- `tests/test_detail_api.py`
- `tests/test_citations_api.py`
- `tests/test_detail_mapper.py`
- `tests/test_citation_mapper.py`
- `tests/test_detail_service.py`
- `tests/test_citation_service.py`
- `tests/test_opensearch_repo.py`

## 审查结论

**阶段 7 实现符合设计目标，代码结构清晰，自动化测试全部通过，真实 OpenSearch smoke 验证通过，未发现阻塞性问题。**

---

## 详细审查

### 1. API 层

**文件：** `app/api/detail.py`、`app/api/citations.py`

- 正确注册路由：
  - `GET /api/patent/detail/{patent_id}`
  - `GET /api/patent/detail/{patent_id}?include_description=true`
  - `GET /api/patent/citations/{patent_id}`
- 使用 `require_api_key` 鉴权依赖。
- 异常分类处理：
  - `InvalidPatentIdentifierError` → 400，code `40002`
  - `PatentNotFoundError` → 404，code `40401`
  - `OpenSearchQueryError` → 502，code `50001`
- 成功响应为业务对象直出，不强制包裹 `success/code/message/data`，符合 `docs/api_spec.md`。

**结论：** 通过。

---

### 2. 服务层

**文件：** `app/services/detail_service.py`、`app/services/citation_service.py`

- `DetailService` 和 `CitationService` 职责单一，分别调用 `repository.get_patent_by_identifier` 和对应 mapper。
- 对空/空白 `patent_id` 抛出 `InvalidPatentIdentifierError`。
- 对 OpenSearch 异常统一包装为 `OpenSearchQueryError`。
- 支持通过参数注入 repository，便于测试。

**结论：** 通过。

---

### 3. 数据访问层

**文件：** `app/repositories/opensearch_repo.py`

新增方法 `get_patent_by_identifier`：

```python
def get_patent_by_identifier(self, identifier: str) -> Optional[dict]:
    for field in ("patent_id", "PublicationNumber", "ApplicationNumber"):
        raw = self.search(...)
        hit = self._first_hit(raw)
        if hit is not None:
            return hit
    return None
```

- 按 `patent_id` → `PublicationNumber` → `ApplicationNumber` 顺序回退查找，鲁棒性较好。
- 查询使用 `term` + `match_phrase` 组合，兼顾精确匹配和短语匹配。
- 未修改 OpenSearch mapping，仅执行只读查询。

**结论：** 通过。

---

### 4. 字段映射层

**文件：** `app/mappings/detail_mapper.py`

- 同时返回 camelCase 和 snake_case 别名，覆盖验收清单中所有字段：
  - `id` / `patent_id`
  - `applicationNumber` / `application_number`
  - `documentNumber` / `document_number`
  - `applicationDate` / `application_date`
  - `documentDate` / `document_date`
  - `legalStatus` / `legal_status`
  - `currentStatus` / `current_status`
  - `currentAssignee` / `current_assignee`
  - `mainIpc` / `main_ipc`
  - `claims`
- `include_description=false` 时不返回 `description`。
- `include_description=true` 时返回 `description`。
- 空值处理统一：字符串缺失返回 `""`，数组缺失返回 `[]`。

**文件：** `app/mappings/citation_mapper.py`

- 返回字段包含：
  - `patent_id`
  - `cited_by`
  - `patent_references`
  - `non_patent_references`
  - `referencesCited`
  - `referencesCitedRaw`
  - `referencesCitedText`
  - `relatedDocuments`
- `cited_by` 和 `patent_references` 对原始对象做了标准化摘要。
- `non_patent_references` 合并了 raw 和 text，去重。

**结论：** 通过。

---

### 5. 异常定义

**文件：** `app/core/exceptions.py`

- 新增 `InvalidPatentIdentifierError`、`PatentNotFoundError`、`OpenSearchQueryError`。
- `service_error` 辅助函数生成统一错误结构。

**结论：** 通过。

---

### 6. 主应用注册

**文件：** `app/main.py`

- 正确 include detail 和 citations router。
- 静态测试页面 `/test/` 保留。

**结论：** 通过。

---

### 7. 冒烟脚本

**文件：** `scripts/smoke_detail_citations.py`

- 分别调用 detail 默认、detail 含 description、citations 三个接口。
- 输出 status 和返回字段 keys。
- 与 api_spec 风格一致。

**结论：** 通过。

---

### 8. 测试覆盖

- `tests/test_detail_api.py`：验证 detail API 响应、40401、50001。
- `tests/test_citations_api.py`：验证 citations API 响应、40401、50001。
- `tests/test_detail_mapper.py`：验证字段映射、description 控制、空值处理。
- `tests/test_citation_mapper.py`：验证 SaaS 工具字段和原始兼容字段。
- `tests/test_detail_service.py` / `test_citation_service.py`：验证服务层异常处理。
- `tests/test_opensearch_repo.py`：验证按 identifier 查询及回退逻辑。

---

## 测试结果

### 自动化测试

```bash
python3 -m pytest -q
```

结果：

```text
103 passed in 0.06s
```

### 真实 OpenSearch Smoke

启动服务后执行：

```bash
python3 scripts/smoke_detail_citations.py http://127.0.0.1:8000 <patent_id> test-token
```

结果：

```json
{"name": "detail", "status": 200, "keys": [...]}
{"name": "detail_description", "status": 200, "keys": [...]}
{"name": "citations", "status": 200, "keys": [...]}
```

退出码：`0`。

补充验证：

- 默认 detail 不含 `description`：✓
- `include_description=true` 含 `description`：✓
- `GET /api/patent/detail/not-found-id` 返回 404 / code `40401`：✓
- citations 返回全部 8 个验收字段：✓

---

## 非阻塞性观察

### 1. 测试前端未支持 detail/citations

当前 `frontend/index.html` 仅支持 search 测试。阶段 7 新增 detail/citations 接口后，手动测试这两个接口需要使用 curl 或浏览器直接访问 URL。

**建议：** 可在测试页增加 detail/citations 测试区域，方便手动验证。

### 2. 部分专利 `description` 内容为空

真实 OpenSmoke 抽样显示，部分专利的 `Instructions` 字段在 OpenSearch 中为空字符串。这属于数据层问题，不影响 API 契约（契约只要求 `include_description=true` 时存在 `description` 键）。

---

## 是否建议进入测试验收

**是。**

阶段 7 代码审查通过，自动化测试通过，真实 OpenSearch detail/citations smoke 通过，字段契约满足，错误响应规范，未修改 SaaS 副本源码，未修改 OpenSearch mapping，符合阶段边界约束。
