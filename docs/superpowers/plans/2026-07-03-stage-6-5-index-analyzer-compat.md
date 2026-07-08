# Stage 6.5 Index Analyzer Compat Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `index_analyzer_mode` so the search service can keep stage 6 normal matching while defaulting to a compat mode that uses phrase queries on fields affected by the current `patent_index` analyzer defect.

**Architecture:** Keep the stage 6 tokenizer, parser, AST, service, repository, and response mapper unchanged. Add request-level mode selection in `SearchRequest`, split query fields into normal fields and analyzer-risk fields in `query_field_mapping.py`, and update the DSL builder leaf field construction so `compat` mode combines normal `multi_match` with phrase `multi_match` for risky fields.

**Tech Stack:** Python 3, FastAPI, Pydantic, pytest, OpenSearch DSL dictionaries, existing stage 6 parser and result mapper.

## Global Constraints

- Do not rebuild OpenSearch indexes.
- Do not modify `patent_index` mapping.
- Do not change data import or ETL logic.
- Do not implement patent detail or citation APIs in stage 6.5.
- `index_analyzer_mode` accepts only `compat` and `normal`.
- `index_analyzer_mode` defaults to `compat`.
- `normal` must preserve stage 6 DSL behavior.
- `compat` must add phrase queries only for confirmed analyzer-risk fields.
- Stage 6 legal syntax, illegal syntax handling, pagination, sorting, auth, and response shape must not regress.
- Commit after each task.

---

## File Structure

- Modify `app/schemas/search.py`: add `index_analyzer_mode`.
- Modify `app/mappings/query_field_mapping.py`: add normal/risky field grouping and helper functions.
- Modify `app/query/dsl_builder.py`: pass request mode into leaf query construction and generate compat phrase DSL.
- Modify `tests/test_search_api.py`: request validation and API-level default mode coverage.
- Modify `tests/test_search_dsl_builder.py`: stage 5/stage 6 regression expectations under `normal`.
- Create `tests/test_query_dsl_builder_stage6_5.py`: compat DSL behavior.
- Modify `scripts/smoke_search.py` or add `scripts/smoke_analyzer_compat.py`: live normal/compat comparison.
- Modify `docs/query_syntax.md`: document `index_analyzer_mode`.
- Modify `docs/api_spec.md`: document request parameter.
- Create `docs/stage6_5_test_report.md`: final evidence after development/testing.

---

### Task 1: SearchRequest Mode Parameter

**Files:**
- Modify: `app/schemas/search.py`
- Modify: `tests/test_search_api.py`

**Interfaces:**
- Produces: `SearchRequest.index_analyzer_mode: str`
- Allowed values: `"compat"` and `"normal"`
- Default: `"compat"`
- Consumed by later task: `build_search_dsl(request: SearchRequest)`

- [ ] **Step 1: Write failing schema tests**

Append to `tests/test_search_api.py`:

```python
def test_search_request_defaults_to_index_analyzer_compat_mode():
    request = SearchRequest(q="阀门")

    assert request.index_analyzer_mode == "compat"


def test_search_request_accepts_normal_index_analyzer_mode():
    request = SearchRequest(q="阀门", index_analyzer_mode="normal")

    assert request.index_analyzer_mode == "normal"


def test_search_request_rejects_invalid_index_analyzer_mode():
    with pytest.raises(ValidationError):
        SearchRequest(q="阀门", index_analyzer_mode="broken")
```

- [ ] **Step 2: Run schema tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_search_api.py::test_search_request_defaults_to_index_analyzer_compat_mode tests/test_search_api.py::test_search_request_accepts_normal_index_analyzer_mode tests/test_search_api.py::test_search_request_rejects_invalid_index_analyzer_mode -q
```

Expected: FAIL because `SearchRequest` has no `index_analyzer_mode`.

- [ ] **Step 3: Implement request parameter**

Modify `app/schemas/search.py`:

```python
from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    q: str = Field(min_length=1, max_length=1000)
    ds: str = Field(default="cn", pattern="^(cn|all)$")
    sort: str = Field(default="relation", pattern="^(relation|!applicationDate)$")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)
    highlight: int = Field(default=0, ge=0, le=1)
    index_analyzer_mode: str = Field(default="compat", pattern="^(compat|normal)$")

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size
```

- [ ] **Step 4: Run schema tests and full regression**

Run:

```bash
.venv/bin/python -m pytest tests/test_search_api.py -q
.venv/bin/python -m pytest -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/schemas/search.py tests/test_search_api.py
git commit -m "feat: add index analyzer mode parameter"
```

---

### Task 2: Field Analyzer Strategy Mapping

**Files:**
- Modify: `app/mappings/query_field_mapping.py`
- Test: `tests/test_legal_status_mapping.py`

**Interfaces:**
- Produces: `NORMAL_ANALYZER_FIELDS_BY_QUERY_FIELD: dict[str, list[str]]`
- Produces: `RISKY_ANALYZER_FIELDS_BY_QUERY_FIELD: dict[str, list[str]]`
- Produces: `get_normal_analyzer_fields(query_field: str) -> list[str]`
- Produces: `get_risky_analyzer_fields(query_field: str) -> list[str]`
- Consumed by later task: `dsl_builder.py`

- [ ] **Step 1: Write failing mapping tests**

Append to `tests/test_legal_status_mapping.py`:

```python
from app.mappings.query_field_mapping import get_normal_analyzer_fields, get_risky_analyzer_fields


def test_stage_6_5_splits_tscd_normal_and_risky_fields():
    assert get_normal_analyzer_fields("tscd") == ["Title", "Abstract"]
    assert get_risky_analyzer_fields("tscd") == ["MainClaim", "Requirement", "Instructions"]


def test_stage_6_5_splits_title_and_abstract_cn_fields():
    assert get_normal_analyzer_fields("title") == ["Title", "TitleEN"]
    assert get_risky_analyzer_fields("title") == ["TitleCN"]
    assert get_normal_analyzer_fields("ab") == ["Abstract", "AbstractEN"]
    assert get_risky_analyzer_fields("ab") == ["AbstractCN"]


def test_stage_6_5_splits_type_fields():
    assert get_normal_analyzer_fields("type") == ["PatentTypeCode", "Kind"]
    assert get_risky_analyzer_fields("type") == ["Type"]


def test_stage_6_5_keeps_applicant_fields_normal():
    assert get_normal_analyzer_fields("applicant") == ["Applicant", "ApplicantNormalized", "FirstApplicant"]
    assert get_risky_analyzer_fields("applicant") == []
```

- [ ] **Step 2: Run mapping tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_legal_status_mapping.py -q
```

Expected: FAIL because helper functions do not exist.

- [ ] **Step 3: Implement field grouping**

Modify `app/mappings/query_field_mapping.py`:

```python
TEXT_FIELD_MAPPING = {
    "title": ["Title", "TitleCN", "TitleEN"],
    "ab": ["Abstract", "AbstractCN", "AbstractEN"],
    "tscd": ["Title", "Abstract", "MainClaim", "Requirement", "Instructions"],
    "applicant": ["Applicant", "ApplicantNormalized", "FirstApplicant"],
    "currentAssignee": ["Assignee", "AssigneeNormalized"],
    "type": ["Type", "PatentTypeCode", "Kind"],
}

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

IPC_FIELDS = ["IPC", "IPCList", "IPCSmallCategory", "IPCLargeGroup", "IPCSmallGroup"]
RANGE_FIELDS = {"ad", "documentYear"}
LEGAL_STATUS_FIELD = "legalStatus"
SUPPORTED_FIELDS = set(TEXT_FIELD_MAPPING) | {"ipc", LEGAL_STATUS_FIELD} | RANGE_FIELDS


def get_normal_analyzer_fields(query_field: str) -> list[str]:
    return NORMAL_ANALYZER_FIELDS_BY_QUERY_FIELD.get(query_field, TEXT_FIELD_MAPPING.get(query_field, []))


def get_risky_analyzer_fields(query_field: str) -> list[str]:
    return RISKY_ANALYZER_FIELDS_BY_QUERY_FIELD.get(query_field, [])
```

- [ ] **Step 4: Run mapping tests and full regression**

Run:

```bash
.venv/bin/python -m pytest tests/test_legal_status_mapping.py -q
.venv/bin/python -m pytest -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/mappings/query_field_mapping.py tests/test_legal_status_mapping.py
git commit -m "feat: classify analyzer compat fields"
```

---

### Task 3: Compat DSL Builder

**Files:**
- Modify: `app/query/dsl_builder.py`
- Modify: `tests/test_search_dsl_builder.py`
- Create: `tests/test_query_dsl_builder_stage6_5.py`

**Interfaces:**
- Consumes: `SearchRequest.index_analyzer_mode`
- Consumes: `get_normal_analyzer_fields(query_field: str) -> list[str]`
- Consumes: `get_risky_analyzer_fields(query_field: str) -> list[str]`
- Produces: `compat` field DSL with normal `multi_match` plus phrase `multi_match`.
- Preserves: `normal` stage 6 DSL.

- [ ] **Step 1: Update stage 6 regression tests to request normal mode**

Modify helper in `tests/test_query_dsl_builder_stage6.py`:

```python
def query_clause(q: str) -> dict:
    return build_search_dsl(SearchRequest(q=q, index_analyzer_mode="normal"))["query"]["bool"]["must"][0]
```

Modify tests in `tests/test_search_dsl_builder.py` to use `index_analyzer_mode="normal"` for stage 6 old-shape DSL assertions:

```python
dsl = build_search_dsl(SearchRequest(q="阀门", index_analyzer_mode="normal"))
dsl = build_search_dsl(SearchRequest(q="title:(阀门)", index_analyzer_mode="normal"))
dsl = build_search_dsl(SearchRequest(q="tscd:(均衡)", index_analyzer_mode="normal"))
```

- [ ] **Step 2: Write failing compat DSL tests**

Create `tests/test_query_dsl_builder_stage6_5.py`:

```python
from app.query.dsl_builder import build_search_dsl
from app.schemas.search import SearchRequest


def query_clause(q: str, mode: str = "compat") -> dict:
    return build_search_dsl(SearchRequest(q=q, index_analyzer_mode=mode))["query"]["bool"]["must"][0]


def test_default_mode_is_compat_for_tscd_risky_fields():
    clause = build_search_dsl(SearchRequest(q="tscd:(口腔数字印模仪图像采集方法)"))["query"]["bool"]["must"][0]

    should = clause["bool"]["should"]
    assert {
        "multi_match": {
            "query": "口腔数字印模仪图像采集方法",
            "fields": ["Title", "Abstract"],
        }
    } in should
    assert {
        "multi_match": {
            "query": "口腔数字印模仪图像采集方法",
            "fields": ["MainClaim", "Requirement", "Instructions"],
            "type": "phrase",
        }
    } in should
    assert clause["bool"]["minimum_should_match"] == 1


def test_normal_mode_preserves_stage_6_tscd_multi_match():
    clause = query_clause("tscd:(口腔数字印模仪图像采集方法)", mode="normal")

    assert clause == {
        "multi_match": {
            "query": "口腔数字印模仪图像采集方法",
            "fields": ["Title", "Abstract", "MainClaim", "Requirement", "Instructions"],
        }
    }


def test_title_and_abstract_compat_use_cn_phrase_fields():
    title_clause = query_clause("title:(口腔数字印模仪)")
    abstract_clause = query_clause("ab:(口腔数字印模仪)")

    assert {
        "multi_match": {
            "query": "口腔数字印模仪",
            "fields": ["Title", "TitleEN"],
        }
    } in title_clause["bool"]["should"]
    assert {
        "multi_match": {
            "query": "口腔数字印模仪",
            "fields": ["TitleCN"],
            "type": "phrase",
        }
    } in title_clause["bool"]["should"]
    assert {
        "multi_match": {
            "query": "口腔数字印模仪",
            "fields": ["AbstractCN"],
            "type": "phrase",
        }
    } in abstract_clause["bool"]["should"]


def test_type_compat_uses_type_phrase_and_code_matches():
    clause = query_clause("type:(发明专利)")

    assert {
        "multi_match": {
            "query": "发明专利",
            "fields": ["PatentTypeCode", "Kind"],
        }
    } in clause["bool"]["should"]
    assert {
        "multi_match": {
            "query": "发明专利",
            "fields": ["Type"],
            "type": "phrase",
        }
    } in clause["bool"]["should"]


def test_boolean_nodes_apply_compat_to_leaf_fields():
    clause = query_clause("ipc:H02M AND tscd:(口腔数字印模仪)")

    right = clause["bool"]["must"][1]
    assert right["bool"]["minimum_should_match"] == 1
    assert {
        "multi_match": {
            "query": "口腔数字印模仪",
            "fields": ["MainClaim", "Requirement", "Instructions"],
            "type": "phrase",
        }
    } in right["bool"]["should"]


def test_applicant_compat_remains_plain_multi_match():
    clause = query_clause("applicant:(华为技术有限公司)")

    assert clause == {
        "multi_match": {
            "query": "华为技术有限公司",
            "fields": ["Applicant", "ApplicantNormalized", "FirstApplicant"],
        }
    }
```

- [ ] **Step 3: Run compat DSL tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_query_dsl_builder_stage6_5.py tests/test_query_dsl_builder_stage6.py tests/test_search_dsl_builder.py -q
```

Expected: FAIL because compat DSL is not implemented.

- [ ] **Step 4: Implement mode-aware DSL builder**

Modify `app/query/dsl_builder.py` imports:

```python
from app.mappings.query_field_mapping import (
    LEGAL_STATUS_FIELD,
    SUPPORTED_FIELDS,
    TEXT_FIELD_MAPPING,
    get_normal_analyzer_fields,
    get_risky_analyzer_fields,
)
```

Modify function signatures and callers:

```python
def build_search_dsl(request: SearchRequest) -> dict:
    must = [_build_node_clause(parse_query(request.q), request.index_analyzer_mode)]
    ...


def _build_node_clause(node: QueryNode, index_analyzer_mode: str) -> dict:
    if isinstance(node, WordNode):
        return _multi_match(node.value, ["Title", "Abstract"])
    if isinstance(node, PhraseNode):
        return _multi_match(node.value, ["Title", "Abstract"])
    if isinstance(node, FieldQuery):
        return _build_field_clause(node, index_analyzer_mode)
    if isinstance(node, RangeQuery):
        return _build_range_clause(node)
    if isinstance(node, AndNode):
        return {
            "bool": {
                "must": [
                    _build_node_clause(node.left, index_analyzer_mode),
                    _build_node_clause(node.right, index_analyzer_mode),
                ]
            }
        }
    if isinstance(node, OrNode):
        return {
            "bool": {
                "should": [
                    _build_node_clause(node.left, index_analyzer_mode),
                    _build_node_clause(node.right, index_analyzer_mode),
                ],
                "minimum_should_match": 1,
            }
        }
    if isinstance(node, NotNode):
        return {"bool": {"must_not": [_build_node_clause(node.child, index_analyzer_mode)]}}
    raise QuerySyntaxError("q 查询语法错误：无法解析查询式")
```

Modify field functions:

```python
def _build_field_clause(node: FieldQuery, index_analyzer_mode: str) -> dict:
    field = node.field
    if field not in SUPPORTED_FIELDS:
        raise QuerySyntaxError(f"q 查询语法错误：不支持字段 {field}")

    value = _node_value(node.value)
    if not value and not isinstance(node.value, (AndNode, OrNode, NotNode)):
        raise QuerySyntaxError(f"q 查询语法错误：字段 {field} 的值不能为空")

    if field in TEXT_FIELD_MAPPING:
        return _build_field_value_clause(node.value, field, index_analyzer_mode)
    if field == "ipc":
        return _build_ipc_clause(value)
    if field == LEGAL_STATUS_FIELD:
        return build_legal_status_clause(value)

    raise QuerySyntaxError(f"q 查询语法错误：不支持字段 {field}")


def _build_field_value_clause(value_node: QueryNode, query_field: str, index_analyzer_mode: str) -> dict:
    if isinstance(value_node, (WordNode, PhraseNode)):
        if index_analyzer_mode == "compat":
            return _compat_multi_match(value_node.value, query_field)
        return _multi_match(value_node.value, TEXT_FIELD_MAPPING[query_field])
    if isinstance(value_node, AndNode):
        return {
            "bool": {
                "must": [
                    _build_field_value_clause(value_node.left, query_field, index_analyzer_mode),
                    _build_field_value_clause(value_node.right, query_field, index_analyzer_mode),
                ]
            }
        }
    if isinstance(value_node, OrNode):
        return {
            "bool": {
                "should": [
                    _build_field_value_clause(value_node.left, query_field, index_analyzer_mode),
                    _build_field_value_clause(value_node.right, query_field, index_analyzer_mode),
                ],
                "minimum_should_match": 1,
            }
        }
    if isinstance(value_node, NotNode):
        return {"bool": {"must_not": [_build_field_value_clause(value_node.child, query_field, index_analyzer_mode)]}}
    raise QuerySyntaxError("q 查询语法错误：字段值不支持该表达式")
```

Add helper:

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


def _phrase_multi_match(query: str, fields: list[str]) -> dict:
    return {"multi_match": {"query": query, "fields": fields, "type": "phrase"}}
```

- [ ] **Step 5: Run compat tests and full regression**

Run:

```bash
.venv/bin/python -m pytest tests/test_query_dsl_builder_stage6_5.py tests/test_query_dsl_builder_stage6.py tests/test_search_dsl_builder.py -q
.venv/bin/python -m pytest -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/query/dsl_builder.py tests/test_query_dsl_builder_stage6.py tests/test_search_dsl_builder.py tests/test_query_dsl_builder_stage6_5.py
git commit -m "feat: add analyzer compat phrase dsl"
```

---

### Task 4: API and Documentation Updates

**Files:**
- Modify: `docs/api_spec.md`
- Modify: `docs/query_syntax.md`
- Modify: `docs/stage6_5_dev_assignment.md`
- Modify: `docs/stage6_5_test_acceptance.md`

**Interfaces:**
- Documents: `index_analyzer_mode=compat|normal`
- Documents: default `compat`
- Documents: no index rebuild and no mapping modification.

- [ ] **Step 1: Write development assignment**

Create `docs/stage6_5_dev_assignment.md`:

````markdown
# 阶段 6.5 开发派工单

## 目标

在不重建 OpenSearch 索引、不修改 `patent_index` mapping 的前提下，为当前索引 analyzer 缺陷增加检索服务侧兼容模式。

## 开发内容

1. `SearchRequest` 新增 `index_analyzer_mode`，默认 `compat`。
2. 增加字段 analyzer 风险分组。
3. `compat` 模式下对问题字段生成 phrase 查询。
4. `normal` 模式保留阶段六普通 DSL。
5. 更新 API 文档和查询语法文档。

## 问题字段

- `TitleCN`
- `AbstractCN`
- `MainClaim`
- `Requirement`
- `Instructions`
- `Type`

## 验证要求

- `.venv/bin/python -m pytest -q`
- 真实 OpenSearch 对比 `normal` 与 `compat` 命中量。
````

- [ ] **Step 2: Write test acceptance doc**

Create `docs/stage6_5_test_acceptance.md`:

````markdown
# 阶段 6.5 测试验收单

## 自动化测试

必须通过：

.venv/bin/python -m pytest -q

## DSL 验收

- 不传 `index_analyzer_mode` 时默认 `compat`。
- `index_analyzer_mode=normal` 保留阶段六普通 `multi_match`。
- `index_analyzer_mode=compat` 对问题字段生成 phrase 查询。

## 真实 OpenSearch 对比

至少记录：

| q | normal total | compat total | 结论 |
|---|---:|---:|---|
| `tscd:(口腔数字印模仪图像采集方法)` |  |  |  |
| `tscd:(图像采集方法)` |  |  |  |
| `title:(口腔数字印模仪)` |  |  |  |
| `ab:(药物组合物)` |  |  |  |
| `type:(发明专利)` |  |  |  |

## 通过标准

1. 自动化测试通过。
2. 阶段六合法/非法查询不回退。
3. `compat` 对典型问题查询明显降低误召回。
4. 不修改 OpenSearch 索引。
5. 不实现阶段七详情接口。
````

- [ ] **Step 3: Update API docs**

In `docs/api_spec.md`, add request parameter:

````markdown
| `index_analyzer_mode` | string | 否 | `compat` | `compat` / `normal` | 索引 analyzer 兼容模式；当前默认 `compat` |
````

In `docs/query_syntax.md`, add:

```markdown
## 阶段 6.5 索引 analyzer 兼容参数

`index_analyzer_mode` 控制当前索引分词缺陷的查询兼容策略：

- `compat`：默认，对 `TitleCN`、`AbstractCN`、`MainClaim`、`Requirement`、`Instructions`、`Type` 等问题字段使用 phrase 查询降低误召回。
- `normal`：保留阶段六普通匹配逻辑，用于对比测试和未来索引修复后的切换。
```

- [ ] **Step 4: Run documentation checks**

Run:

```bash
git diff --check
rg -n "index_analyzer_mode|compat|normal|阶段 6.5" docs/api_spec.md docs/query_syntax.md docs/stage6_5_dev_assignment.md docs/stage6_5_test_acceptance.md
```

Expected: no whitespace errors; all docs mention the new mode.

- [ ] **Step 5: Commit**

```bash
git add docs/api_spec.md docs/query_syntax.md docs/stage6_5_dev_assignment.md docs/stage6_5_test_acceptance.md
git commit -m "docs: add stage 6.5 analyzer compat docs"
```

---

### Task 5: Live Comparison Smoke

**Files:**
- Create: `scripts/smoke_analyzer_compat.py`
- Create after testing: `docs/stage6_5_test_report.md`

**Interfaces:**
- Consumes: running API server.
- Consumes: `API_TOKEN` from `.env` or explicit CLI arg.
- Produces: normal/compat total comparison.

- [ ] **Step 1: Create live comparison script**

Create `scripts/smoke_analyzer_compat.py`:

```python
import sys

import httpx


QUERIES = [
    "tscd:(口腔数字印模仪图像采集方法)",
    "tscd:(图像采集方法)",
    "title:(口腔数字印模仪)",
    "ab:(药物组合物)",
    "type:(发明专利)",
]


def search(base_url: str, token: str, q: str, mode: str) -> tuple[int, int]:
    response = httpx.post(
        f"{base_url.rstrip('/')}/api/patent/search",
        json={
            "q": q,
            "index_analyzer_mode": mode,
            "page": 1,
            "page_size": 1,
            "ds": "cn",
            "sort": "relation",
            "highlight": 0,
        },
        headers={"X-API-Key": token} if token else {},
        timeout=60,
    )
    return response.status_code, response.json().get("total", -1) if response.status_code == 200 else -1


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    token = sys.argv[2] if len(sys.argv) > 2 else ""
    failures = 0

    for q in QUERIES:
        normal_status, normal_total = search(base_url, token, q, "normal")
        compat_status, compat_total = search(base_url, token, q, "compat")
        print(
            f"q={q} normal_status={normal_status} normal_total={normal_total} "
            f"compat_status={compat_status} compat_total={compat_total}"
        )
        if normal_status != 200 or compat_status != 200:
            failures += 1

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run automated tests**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: PASS.

- [ ] **Step 3: Run live comparison**

Start API server if needed:

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Run comparison using a valid token:

```bash
.venv/bin/python scripts/smoke_analyzer_compat.py http://127.0.0.1:8000 "$API_TOKEN"
```

Expected: all rows return `normal_status=200` and `compat_status=200`; typical tscd issue queries have lower `compat_total`.

- [ ] **Step 4: Write test report**

Create `docs/stage6_5_test_report.md` after the commands are actually run. Do not commit a blank or template-only report.

```markdown
# 阶段 6.5 测试报告

## 自动化测试

- 命令：`.venv/bin/python -m pytest -q`
- 结果：写入实际 pytest 输出摘要和最终通过/失败状态

## 真实 OpenSearch 对比

| q | normal total | compat total | 结论 |
|---|---:|---:|---|
| `tscd:(口腔数字印模仪图像采集方法)` |  |  |  |
| `tscd:(图像采集方法)` |  |  |  |
| `title:(口腔数字印模仪)` |  |  |  |
| `ab:(药物组合物)` |  |  |  |
| `type:(发明专利)` |  |  |  |

## 结论

- 阶段六能力是否回退：基于自动化测试和 smoke 结果写入明确结论
- `compat` 是否降低典型问题字段误召回：基于对比命中量写入明确结论
- 是否修改索引：否
- 是否建议进入阶段七：写入明确结论
```

- [ ] **Step 5: Commit**

```bash
git add scripts/smoke_analyzer_compat.py docs/stage6_5_test_report.md
git commit -m "test: add analyzer compat smoke evidence"
```

---

## Review Checklist

- `SearchRequest.index_analyzer_mode` defaults to `compat`.
- `normal` mode preserves stage 6 DSL shape for existing tests.
- `compat` mode generates phrase queries only for `TitleCN`, `AbstractCN`, `MainClaim`, `Requirement`, `Instructions`, and `Type`.
- `applicant`, `currentAssignee`, `legalStatus`, `ipc`, `ad`, and `documentYear` behavior does not change.
- Stage 6 illegal input list still returns `40001`.
- Full pytest suite passes.
- Live comparison script records normal/compat totals.
- No OpenSearch index or mapping changes are made.
