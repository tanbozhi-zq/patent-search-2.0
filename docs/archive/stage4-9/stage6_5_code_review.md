# 阶段 6.5 代码审查报告

## 审查范围

- `app/schemas/search.py`
- `app/query/dsl_builder.py`
- `app/mappings/query_field_mapping.py`
- `app/api/search.py`
- `scripts/smoke_analyzer_compat.py`
- `frontend/index.html`
- `tests/test_query_dsl_builder_stage6_5.py`
- `tests/test_query_dsl_builder_stage6.py`（阶段六回归）
- `tests/test_search_dsl_builder.py`（阶段五回归）
- `docs/api_spec.md`
- `docs/query_syntax.md`
- `docs/stage6_5_dev_assignment.md`
- `docs/stage6_5_test_acceptance.md`

## 审查结论

**阶段 6.5 实现符合设计目标，代码结构清晰，未修改 OpenSearch 索引，自动化测试全部通过，真实 OpenSearch 对比显示 compat 模式有效降低误召回。未发现阻塞性问题。**

---

## 详细审查

### 1. `SearchRequest` 新增 `index_analyzer_mode`

**文件：** `app/schemas/search.py`

```python
index_analyzer_mode: str = Field(default="compat", pattern="^(compat|normal)$")
```

- 默认值 `compat` 符合阶段 6.5 派工单要求。
- 仅允许 `compat` 和 `normal` 两个值，参数校验正确。
- 与其他参数（`ds`、`sort`、`page` 等）风格一致。

**结论：** 通过。

---

### 2. 字段 analyzer 风险分组

**文件：** `app/mappings/query_field_mapping.py`

新增：

```python
NORMAL_ANALYZER_FIELDS_BY_QUERY_FIELD = {
    "title": ["Title", "TitleEN"],
    "ab": ["Abstract", "AbstractEN"],
    "tscd": ["Title", "Abstract"],
    "applicant": ["Applicant", "ApplicantNormalized", "FirstApplicant"],
    "currentAssignee": ["Assignee", "AssigneeNormalized"],
    "type": ["PatentTypeCode", "Kind"],
}

RISKY_ANALYZER_FIELDS_BY_QUERY_FIELD = {
    "title": ["TitleCN"],
    "ab": ["AbstractCN"],
    "tscd": ["MainClaim", "Requirement", "Instructions"],
    "type": ["Type"],
}
```

- 风险字段覆盖了派工单中列出的全部问题字段：`TitleCN`、`AbstractCN`、`MainClaim`、`Requirement`、`Instructions`、`Type`。
- `applicant` / `currentAssignee` 未列入风险字段，保持普通 `multi_match`，合理。
- `Type` 被正确识别为风险字段（text 类型，使用 standard analyzer）。

**结论：** 通过。

---

### 3. `compat` 模式下 phrase DSL 构造

**文件：** `app/query/dsl_builder.py`

核心逻辑：

```python
def _compat_multi_match(query: str, query_field: str) -> dict:
    normal_fields = get_normal_analyzer_fields(query_field)
    risky_fields = get_risky_analyzer_fields(query_field)

    clauses = []
    if normal_fields:
        clauses.append(_multi_match(query, normal_fields))
    if risky_fields:
        clauses.append(_phrase_multi_match(query, risky_fields))

    if not clauses:
        return _multi_match(query, TEXT_FIELD_MAPPING[query_field])
    if len(clauses) == 1:
        return clauses[0]
    return {"bool": {"should": clauses, "minimum_should_match": 1}}
```

```python
def _phrase_multi_match(query: str, fields: list[str]) -> dict:
    return {"multi_match": {"query": query, "fields": fields, "type": "phrase"}}
```

- `compat` 模式对正常字段使用普通 `multi_match`，对风险字段使用 `multi_match` + `type: "phrase"`。
- 通过 `bool.should` + `minimum_should_match: 1` 合并两类字段，保证召回不会完全丢失。
- 布尔节点（AND/OR/NOT）递归传递 `index_analyzer_mode`，叶节点字段查询均会应用 compat 逻辑。

**结论：** 通过。

---

### 4. `normal` 模式保留阶段六行为

- `normal` 模式仍使用阶段六的普通 `multi_match`，字段集合与阶段六一致。
- 现有阶段六测试已通过显式 `index_analyzer_mode="normal"` 进行回归验证。

**结论：** 通过。

---

### 5. API 文档更新

**文件：** `docs/api_spec.md`

- 已在请求参数表中增加 `index_analyzer_mode`，说明默认值为 `compat`，支持 `compat` / `normal`。
- `docs/query_syntax.md` 中也补充了阶段 6.5 已支持语法说明。

**结论：** 通过。

---

### 6. 冒烟脚本

**文件：** `scripts/smoke_analyzer_compat.py`

- 对同一批查询分别调用 `normal` 和 `compat` 模式，输出 status 和 total。
- 与 `scripts/smoke_search.py` 风格一致，便于复用。

**结论：** 通过。

---

## 测试结果

### 自动化测试

```bash
python3 -m pytest -q
```

结果：

```text
79 passed in 0.05s
```

### 真实 OpenSearch 对比

启动服务后执行：

```bash
python3 scripts/smoke_analyzer_compat.py http://127.0.0.1:8000 test-token
```

结果：

| q | normal total | compat total | 结论 |
|---|---:|---:|---|
| `tscd:(口腔数字印模仪图像采集方法)` | 44,286,246 | 19,934,751 | compat 显著降低误召回 |
| `tscd:(图像采集方法)` | 35,700,030 | 18,873,147 | compat 显著降低误召回 |
| `title:(口腔数字印模仪)` | 863,158 | 783,955 | compat 降低误召回 |
| `ab:(药物组合物)` | 6,558,178 | 6,285,148 | compat 降低误召回 |
| `type:(发明专利)` | 30,769,770 | 30,769,770 | 持平（type 的 normal 字段为 keyword，实际由 phrase 字段贡献） |

所有查询均返回 HTTP 200，服务正常。

---

### 7. 测试前端

**文件：** `frontend/index.html`

- 测试页已增加 `Analyzer 模式` 下拉框，支持 `compat` 与 `normal`。
- 请求 payload 已携带 `index_analyzer_mode`，方便手动对比两种模式。
- 默认选中 `compat`，与后端默认策略一致。

**结论：** 通过。

---

## 非阻塞性观察

### 1. `type` 的 normal 字段为 keyword 类型

`type` 的 normal 字段是 `PatentTypeCode` 和 `Kind`，均为 keyword。查询词为中文“发明专利”时，对 keyword 字段的 `multi_match` 不会命中代码值（如 `1`、`U1`），因此 `type` 的 compat 和 normal 结果相同，实际由 risky 字段 `Type` 的 phrase 查询贡献。

这不是错误，属于字段特性。后续如需要按代码值精确匹配专利类型，可单独扩展 `type` 查询语法。

---

## 是否建议进入测试验收

**是。**

阶段 6.5 代码审查通过，自动化测试通过，真实 OpenSearch 对比显示 compat 模式有效降低典型问题字段的误召回，未修改 OpenSearch 索引，符合阶段 6.5 验收标准。
