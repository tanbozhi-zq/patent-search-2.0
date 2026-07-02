# Stage 6 Boolean Query Parser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the stage 6 boolean query parser so `q` supports field queries, ranges, phrases, `AND` / `OR` / `NOT`, parentheses, and query syntax errors that return `40001` before OpenSearch is called.

**Architecture:** Replace the stage 5 regex-only query builder with a focused pipeline: `tokenize(q) -> parse(tokens) -> AST -> build_query_clause(ast) -> OpenSearch DSL`. `SearchService` remains orchestration only; tokenizer, parser, and DSL builder own syntax validation and raise `QuerySyntaxError`.

**Tech Stack:** Python 3, FastAPI, Pydantic, pytest, OpenSearch DSL dictionaries, existing repository and response mapper.

## Global Constraints

- Keep the vendor-like API response structure already used by stage 5.
- Do not implement wildcard, proximity, fuzzy, boost, vector similarity, patent detail, citation, SaaS integration, production gateway, or Nginx work in stage 6.
- `NOT` is required stage 6 syntax and must map to OpenSearch `bool.must_not`.
- Invalid query syntax must raise `QuerySyntaxError`, return API code `40001`, and must not call real OpenSearch.
- Date validation must use real calendar parsing, not only a `YYYY-MM-DD` regex.
- The `type` query field must map to existing OpenSearch fields `Type`, `PatentTypeCode`, and `Kind`; do not use nonexistent field `PatentType`.
- Preserve stage 5 behavior for plain keyword, `title`, `ab`, `ipc`, `ad`, pagination, sorting, and `ds=cn/all`.
- Commit after each task.

---

## File Structure

- Create `app/query/tokens.py`: token type enum and token dataclass.
- Create `app/query/tokenizer.py`: lexical scanner from `q` string to token list.
- Create `app/query/ast.py`: AST dataclasses for field, range, phrase, boolean, and word nodes.
- Create `app/query/parser.py`: recursive descent parser with precedence `parentheses > NOT > AND > OR`.
- Modify `app/query/dsl_builder.py`: use parser output to build OpenSearch DSL and preserve stage 5 request-level filters/sort.
- Create `app/mappings/query_field_mapping.py`: query field to OpenSearch field mapping helpers.
- Create `app/mappings/legal_status_mapping.py`: legal status mapping helper.
- Modify `app/core/exceptions.py`: add `QuerySyntaxError`.
- Modify `app/api/search.py`: catch `QuerySyntaxError` and return `40001`.
- Modify `scripts/smoke_search.py`: add stage 6 smoke cases.
- Modify `docs/query_syntax.md`: document stage 6 supported syntax.
- Modify `docs/field_mapping.md`: document stage 6 query field mappings.
- Test with `tests/test_query_tokenizer.py`, `tests/test_query_parser.py`, `tests/test_query_dsl_builder_stage6.py`, `tests/test_legal_status_mapping.py`, and existing stage 5 tests.

---

### Task 1: QuerySyntaxError and Tokenizer

**Files:**
- Create: `app/query/tokens.py`
- Create: `app/query/tokenizer.py`
- Modify: `app/core/exceptions.py`
- Test: `tests/test_query_tokenizer.py`

**Interfaces:**
- Produces: `class QuerySyntaxError(ValueError)`
- Produces: `TokenType` enum values: `WORD`, `PHRASE`, `FIELD`, `AND`, `OR`, `NOT`, `LPAREN`, `RPAREN`, `LBRACKET`, `RBRACKET`, `COLON`, `TO`, `EOF`
- Produces: `Token(type: TokenType, value: str, position: int)`
- Produces: `tokenize(q: str) -> list[Token]`
- Consumes: no stage 6 parser code yet.

- [ ] **Step 1: Write failing tokenizer tests**

Create `tests/test_query_tokenizer.py`:

```python
import pytest

from app.core.exceptions import QuerySyntaxError
from app.query.tokenizer import tokenize
from app.query.tokens import TokenType


def token_pairs(q: str):
    return [(token.type, token.value) for token in tokenize(q)]


def test_tokenizes_field_parentheses_and_boolean_words():
    assert token_pairs('ipc:H02M AND tscd:("均衡" OR "平衡")') == [
        (TokenType.FIELD, "ipc"),
        (TokenType.COLON, ":"),
        (TokenType.WORD, "H02M"),
        (TokenType.AND, "AND"),
        (TokenType.FIELD, "tscd"),
        (TokenType.COLON, ":"),
        (TokenType.LPAREN, "("),
        (TokenType.PHRASE, "均衡"),
        (TokenType.OR, "OR"),
        (TokenType.PHRASE, "平衡"),
        (TokenType.RPAREN, ")"),
        (TokenType.EOF, ""),
    ]


def test_tokenizes_range_expression():
    assert token_pairs("ad:[2020-01-01 TO 2020-12-31]") == [
        (TokenType.FIELD, "ad"),
        (TokenType.COLON, ":"),
        (TokenType.LBRACKET, "["),
        (TokenType.WORD, "2020-01-01"),
        (TokenType.TO, "TO"),
        (TokenType.WORD, "2020-12-31"),
        (TokenType.RBRACKET, "]"),
        (TokenType.EOF, ""),
    ]


def test_rejects_unclosed_quote():
    with pytest.raises(QuerySyntaxError, match="引号未闭合"):
        tokenize('tscd:("均衡)')


def test_rejects_empty_query():
    with pytest.raises(QuerySyntaxError, match="查询式不能为空"):
        tokenize("   ")
```

- [ ] **Step 2: Run tokenizer tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_query_tokenizer.py -q
```

Expected: FAIL because `app.query.tokenizer` and `app.query.tokens` do not exist.

- [ ] **Step 3: Implement tokenizer**

Add to `app/core/exceptions.py`:

```python
from fastapi import HTTPException


class QuerySyntaxError(ValueError):
    pass


def service_error(status_code: int, code: int, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "success": False,
            "code": code,
            "message": message,
            "data": None,
        },
    )
```

Create `app/query/tokens.py`:

```python
from dataclasses import dataclass
from enum import Enum


class TokenType(str, Enum):
    WORD = "WORD"
    PHRASE = "PHRASE"
    FIELD = "FIELD"
    AND = "AND"
    OR = "OR"
    NOT = "NOT"
    TO = "TO"
    LPAREN = "LPAREN"
    RPAREN = "RPAREN"
    LBRACKET = "LBRACKET"
    RBRACKET = "RBRACKET"
    COLON = "COLON"
    EOF = "EOF"


@dataclass(frozen=True)
class Token:
    type: TokenType
    value: str
    position: int
```

Create `app/query/tokenizer.py`:

```python
from app.core.exceptions import QuerySyntaxError
from app.query.tokens import Token, TokenType


RESERVED = {
    "AND": TokenType.AND,
    "OR": TokenType.OR,
    "NOT": TokenType.NOT,
    "TO": TokenType.TO,
}


def tokenize(q: str) -> list[Token]:
    if not q or not q.strip():
        raise QuerySyntaxError("q 查询语法错误：查询式不能为空")

    tokens: list[Token] = []
    i = 0
    length = len(q)

    while i < length:
        char = q[i]

        if char.isspace():
            i += 1
            continue

        if char == '"':
            start = i
            i += 1
            value_chars: list[str] = []
            while i < length and q[i] != '"':
                value_chars.append(q[i])
                i += 1
            if i >= length:
                raise QuerySyntaxError("q 查询语法错误：引号未闭合")
            tokens.append(Token(TokenType.PHRASE, "".join(value_chars), start))
            i += 1
            continue

        single = {
            "(": TokenType.LPAREN,
            ")": TokenType.RPAREN,
            "[": TokenType.LBRACKET,
            "]": TokenType.RBRACKET,
            ":": TokenType.COLON,
        }.get(char)
        if single:
            tokens.append(Token(single, char, i))
            i += 1
            continue

        if _is_word_char(char):
            start = i
            chars: list[str] = []
            while i < length and _is_word_char(q[i]):
                chars.append(q[i])
                i += 1
            value = "".join(chars)
            token_type = RESERVED.get(value.upper())
            if token_type is not None:
                tokens.append(Token(token_type, value.upper(), start))
                continue

            j = i
            while j < length and q[j].isspace():
                j += 1
            if j < length and q[j] == ":":
                tokens.append(Token(TokenType.FIELD, value, start))
            else:
                tokens.append(Token(TokenType.WORD, value, start))
            continue

        raise QuerySyntaxError(f"q 查询语法错误：无法识别的符号 {char}")

    tokens.append(Token(TokenType.EOF, "", length))
    return tokens


def _is_word_char(char: str) -> bool:
    return char not in {'"', "(", ")", "[", "]", ":", " "} and not char.isspace()
```

- [ ] **Step 4: Run tokenizer tests and full regression**

Run:

```bash
.venv/bin/python -m pytest tests/test_query_tokenizer.py -q
.venv/bin/python -m pytest -q
```

Expected: tokenizer tests PASS; full suite PASS.

- [ ] **Step 5: Commit**

```bash
git add app/core/exceptions.py app/query/tokens.py app/query/tokenizer.py tests/test_query_tokenizer.py
git commit -m "feat: add query tokenizer"
```

---

### Task 2: AST and Parser

**Files:**
- Create: `app/query/ast.py`
- Create: `app/query/parser.py`
- Test: `tests/test_query_parser.py`

**Interfaces:**
- Consumes: `tokenize(q: str) -> list[Token]`
- Produces: `parse_query(q: str) -> QueryNode`
- Produces AST dataclasses: `WordNode`, `PhraseNode`, `FieldQuery`, `RangeQuery`, `AndNode`, `OrNode`, `NotNode`

- [ ] **Step 1: Write failing parser tests**

Create `tests/test_query_parser.py`:

```python
import pytest

from app.core.exceptions import QuerySyntaxError
from app.query.ast import AndNode, FieldQuery, NotNode, OrNode, PhraseNode, RangeQuery, WordNode
from app.query.parser import parse_query


def test_parses_simple_field_query():
    ast = parse_query("title:(阀门)")

    assert ast == FieldQuery(field="title", value=WordNode(value="阀门"))


def test_parses_phrase_or_inside_field_query():
    ast = parse_query('tscd:("均衡" OR "平衡")')

    assert ast == FieldQuery(
        field="tscd",
        value=OrNode(left=PhraseNode("均衡"), right=PhraseNode("平衡")),
    )


def test_and_has_higher_precedence_than_or():
    ast = parse_query("title:(A) OR title:(B) AND ipc:H02M")

    assert isinstance(ast, OrNode)
    assert isinstance(ast.right, AndNode)


def test_parses_not_before_field_query():
    ast = parse_query("ipc:H02M AND NOT tscd:(均衡)")

    assert isinstance(ast, AndNode)
    assert isinstance(ast.right, NotNode)
    assert ast.right.child == FieldQuery(field="tscd", value=WordNode(value="均衡"))


def test_parses_date_range():
    assert parse_query("ad:[2020-01-01 TO 2020-12-31]") == RangeQuery(
        field="ad",
        start="2020-01-01",
        end="2020-12-31",
    )


@pytest.mark.parametrize(
    "q,error",
    [
        ("ipc:H02M AND AND tscd:(均衡)", "AND 后缺少查询条件"),
        ("AND tscd:(均衡)", "AND 位置错误"),
        ("tscd:(均衡) OR", "OR 后缺少查询条件"),
        ("tscd:()", "字段 tscd 的值不能为空"),
        ("ipc:", "字段 ipc 的值不能为空"),
        ("ad:[2020-01-01 2020-12-31]", "范围表达式缺少 TO"),
        ("NOT", "NOT 后缺少查询条件"),
        ("tscd:(均衡) NOT", "NOT 位置错误"),
    ],
)
def test_rejects_invalid_syntax(q, error):
    with pytest.raises(QuerySyntaxError, match=error):
        parse_query(q)
```

- [ ] **Step 2: Run parser tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_query_parser.py -q
```

Expected: FAIL because `app.query.ast` and `app.query.parser` do not exist.

- [ ] **Step 3: Implement AST dataclasses**

Create `app/query/ast.py`:

```python
from dataclasses import dataclass
from typing import Union


@dataclass(frozen=True)
class WordNode:
    value: str


@dataclass(frozen=True)
class PhraseNode:
    value: str


@dataclass(frozen=True)
class FieldQuery:
    field: str
    value: "QueryNode"


@dataclass(frozen=True)
class RangeQuery:
    field: str
    start: str
    end: str


@dataclass(frozen=True)
class AndNode:
    left: "QueryNode"
    right: "QueryNode"


@dataclass(frozen=True)
class OrNode:
    left: "QueryNode"
    right: "QueryNode"


@dataclass(frozen=True)
class NotNode:
    child: "QueryNode"


QueryNode = Union[WordNode, PhraseNode, FieldQuery, RangeQuery, AndNode, OrNode, NotNode]
```

- [ ] **Step 4: Implement recursive descent parser**

Create `app/query/parser.py`:

```python
from app.core.exceptions import QuerySyntaxError
from app.query.ast import AndNode, FieldQuery, NotNode, OrNode, PhraseNode, QueryNode, RangeQuery, WordNode
from app.query.tokenizer import tokenize
from app.query.tokens import Token, TokenType


class Parser:
    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.index = 0

    def parse(self) -> QueryNode:
        node = self._parse_or()
        current = self._current()
        if current.type is not TokenType.EOF:
            if current.type is TokenType.NOT:
                raise QuerySyntaxError("q 查询语法错误：NOT 位置错误")
            raise QuerySyntaxError(f"q 查询语法错误：无法解析 {current.value}")
        return node

    def _parse_or(self) -> QueryNode:
        node = self._parse_and()
        while self._match(TokenType.OR):
            if self._current().type in {TokenType.EOF, TokenType.RPAREN}:
                raise QuerySyntaxError("q 查询语法错误：OR 后缺少查询条件")
            node = OrNode(left=node, right=self._parse_and())
        return node

    def _parse_and(self) -> QueryNode:
        if self._current().type is TokenType.AND:
            raise QuerySyntaxError("q 查询语法错误：AND 位置错误")
        node = self._parse_not()
        while self._match(TokenType.AND):
            if self._current().type in {TokenType.AND, TokenType.OR, TokenType.EOF, TokenType.RPAREN}:
                raise QuerySyntaxError("q 查询语法错误：AND 后缺少查询条件")
            node = AndNode(left=node, right=self._parse_not())
        return node

    def _parse_not(self) -> QueryNode:
        if self._match(TokenType.NOT):
            if self._current().type in {TokenType.AND, TokenType.OR, TokenType.EOF, TokenType.RPAREN}:
                raise QuerySyntaxError("q 查询语法错误：NOT 后缺少查询条件")
            return NotNode(child=self._parse_not())
        return self._parse_primary()

    def _parse_primary(self) -> QueryNode:
        current = self._current()
        if current.type is TokenType.LPAREN:
            self._advance()
            node = self._parse_or()
            self._consume(TokenType.RPAREN, "q 查询语法错误：缺少右括号")
            return node
        if current.type is TokenType.FIELD:
            return self._parse_field_or_range()
        if current.type is TokenType.PHRASE:
            self._advance()
            if current.value == "":
                raise QuerySyntaxError("q 查询语法错误：查询式不能为空")
            return PhraseNode(current.value)
        if current.type is TokenType.WORD:
            self._advance()
            return WordNode(current.value)
        raise QuerySyntaxError(f"q 查询语法错误：{current.value or current.type.value} 位置错误")

    def _parse_field_or_range(self) -> QueryNode:
        field = self._advance().value
        self._consume(TokenType.COLON, f"q 查询语法错误：字段 {field} 的值不能为空")

        if self._match(TokenType.LBRACKET):
            start = self._consume(TokenType.WORD, "q 查询语法错误：范围表达式缺少起始值").value
            self._consume(TokenType.TO, "q 查询语法错误：范围表达式缺少 TO")
            end = self._consume(TokenType.WORD, "q 查询语法错误：范围表达式缺少结束值").value
            self._consume(TokenType.RBRACKET, "q 查询语法错误：范围表达式缺少右方括号")
            return RangeQuery(field=field, start=start, end=end)

        if self._match(TokenType.LPAREN):
            if self._current().type is TokenType.RPAREN:
                raise QuerySyntaxError(f"q 查询语法错误：字段 {field} 的值不能为空")
            value = self._parse_or()
            self._consume(TokenType.RPAREN, "q 查询语法错误：缺少右括号")
            return FieldQuery(field=field, value=value)

        current = self._current()
        if current.type is TokenType.WORD:
            self._advance()
            return FieldQuery(field=field, value=WordNode(current.value))
        if current.type is TokenType.PHRASE:
            self._advance()
            return FieldQuery(field=field, value=PhraseNode(current.value))
        raise QuerySyntaxError(f"q 查询语法错误：字段 {field} 的值不能为空")

    def _match(self, token_type: TokenType) -> bool:
        if self._current().type is token_type:
            self._advance()
            return True
        return False

    def _consume(self, token_type: TokenType, message: str) -> Token:
        current = self._current()
        if current.type is not token_type:
            raise QuerySyntaxError(message)
        return self._advance()

    def _advance(self) -> Token:
        current = self._current()
        self.index += 1
        return current

    def _current(self) -> Token:
        return self.tokens[self.index]


def parse_query(q: str) -> QueryNode:
    return Parser(tokenize(q)).parse()
```

- [ ] **Step 5: Run parser tests and full regression**

Run:

```bash
.venv/bin/python -m pytest tests/test_query_parser.py -q
.venv/bin/python -m pytest -q
```

Expected: parser tests PASS; stage 5 tests still PASS because `build_search_dsl` has not been replaced yet.

- [ ] **Step 6: Commit**

```bash
git add app/query/ast.py app/query/parser.py tests/test_query_parser.py
git commit -m "feat: add boolean query parser"
```

---

### Task 3: Query Field and Legal Status Mappings

**Files:**
- Create: `app/mappings/query_field_mapping.py`
- Create: `app/mappings/legal_status_mapping.py`
- Test: `tests/test_legal_status_mapping.py`

**Interfaces:**
- Produces: `TEXT_FIELD_MAPPING: dict[str, list[str]]`
- Produces: `FIELD_ALIASES: set[str]`
- Produces: `build_legal_status_clause(value: str) -> dict`
- Consumes: no DSL builder change yet.

- [ ] **Step 1: Write failing mapping tests**

Create `tests/test_legal_status_mapping.py`:

```python
from app.mappings.legal_status_mapping import build_legal_status_clause
from app.mappings.query_field_mapping import TEXT_FIELD_MAPPING


def test_text_field_mapping_contains_stage_six_fields():
    assert TEXT_FIELD_MAPPING["tscd"] == ["Title", "Abstract", "MainClaim", "Requirement", "Instructions"]
    assert TEXT_FIELD_MAPPING["applicant"] == ["Applicant", "ApplicantNormalized", "FirstApplicant"]
    assert TEXT_FIELD_MAPPING["currentAssignee"] == ["Assignee", "AssigneeNormalized"]
    assert TEXT_FIELD_MAPPING["type"] == ["Type", "PatentTypeCode", "Kind"]


def test_effective_patent_legal_status_mapping():
    clause = build_legal_status_clause("有效专利")

    should = clause["bool"]["should"]
    assert {"match": {"LatestLegalStatus": "授权"}} in should
    assert {"match": {"LatestLegalStatus": "有效"}} in should
    assert clause["bool"]["minimum_should_match"] == 1


def test_unknown_legal_status_falls_back_to_text_match():
    assert build_legal_status_clause("部分无效") == {
        "multi_match": {
            "query": "部分无效",
            "fields": ["LatestLegalStatus", "LegalStatus"],
        }
    }
```

- [ ] **Step 2: Run mapping tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_legal_status_mapping.py -q
```

Expected: FAIL because mapping modules do not exist.

- [ ] **Step 3: Implement mappings**

Create `app/mappings/query_field_mapping.py`:

```python
TEXT_FIELD_MAPPING = {
    "title": ["Title", "TitleCN", "TitleEN"],
    "ab": ["Abstract", "AbstractCN", "AbstractEN"],
    "tscd": ["Title", "Abstract", "MainClaim", "Requirement", "Instructions"],
    "applicant": ["Applicant", "ApplicantNormalized", "FirstApplicant"],
    "currentAssignee": ["Assignee", "AssigneeNormalized"],
    "type": ["Type", "PatentTypeCode", "Kind"],
}

IPC_FIELDS = ["IPC", "IPCList", "IPCSmallCategory", "IPCLargeGroup", "IPCSmallGroup"]
RANGE_FIELDS = {"ad", "documentYear"}
LEGAL_STATUS_FIELD = "legalStatus"
SUPPORTED_FIELDS = set(TEXT_FIELD_MAPPING) | {"ipc", LEGAL_STATUS_FIELD} | RANGE_FIELDS
```

Create `app/mappings/legal_status_mapping.py`:

```python
LEGAL_STATUS_KEYWORDS = {
    "有效专利": ["授权", "有效"],
    "在审": ["公开", "实质审查"],
    "失效": ["终止", "届满", "撤回", "驳回"],
}


def build_legal_status_clause(value: str) -> dict:
    keywords = LEGAL_STATUS_KEYWORDS.get(value)
    if keywords:
        return {
            "bool": {
                "should": [{"match": {"LatestLegalStatus": keyword}} for keyword in keywords],
                "minimum_should_match": 1,
            }
        }

    return {
        "multi_match": {
            "query": value,
            "fields": ["LatestLegalStatus", "LegalStatus"],
        }
    }
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
git add app/mappings/query_field_mapping.py app/mappings/legal_status_mapping.py tests/test_legal_status_mapping.py
git commit -m "feat: add stage 6 query mappings"
```

---

### Task 4: AST-Based DSL Builder

**Files:**
- Modify: `app/query/dsl_builder.py`
- Test: `tests/test_search_dsl_builder.py`
- Test: `tests/test_query_dsl_builder_stage6.py`

**Interfaces:**
- Consumes: `parse_query(q: str) -> QueryNode`
- Consumes: mapping modules from Task 3
- Produces: `build_search_dsl(request: SearchRequest) -> dict`
- Produces internal `_build_node_clause(node: QueryNode) -> dict`

- [ ] **Step 1: Update DSL tests for stage 6**

Modify `tests/test_search_dsl_builder.py` so it imports `QuerySyntaxError` and no longer expects `tscd` to fail:

```python
import pytest

from app.core.exceptions import QuerySyntaxError
from app.query.dsl_builder import build_search_dsl
from app.schemas.search import SearchRequest
```

Replace `test_rejects_stage_six_syntax` with:

```python
def test_tscd_query_searches_title_abstract_claim_and_instructions():
    dsl = build_search_dsl(SearchRequest(q="tscd:(均衡)"))

    multi_match = dsl["query"]["bool"]["must"][0]["multi_match"]
    assert multi_match["query"] == "均衡"
    assert multi_match["fields"] == ["Title", "Abstract", "MainClaim", "Requirement", "Instructions"]
```

Create `tests/test_query_dsl_builder_stage6.py`:

```python
import pytest

from app.core.exceptions import QuerySyntaxError
from app.query.dsl_builder import build_search_dsl
from app.schemas.search import SearchRequest


def query_clause(q: str) -> dict:
    return build_search_dsl(SearchRequest(q=q))["query"]["bool"]["must"][0]


def test_and_query_maps_to_bool_must():
    clause = query_clause("ipc:H02M AND tscd:(均衡)")

    must = clause["bool"]["must"]
    assert len(must) == 2
    assert must[0]["bool"]["minimum_should_match"] == 1
    assert must[1]["multi_match"]["fields"] == ["Title", "Abstract", "MainClaim", "Requirement", "Instructions"]


def test_or_query_maps_to_bool_should():
    clause = query_clause("title:(均衡) OR title:(平衡)")

    assert clause["bool"]["minimum_should_match"] == 1
    assert [item["multi_match"]["query"] for item in clause["bool"]["should"]] == ["均衡", "平衡"]


def test_not_query_maps_to_bool_must_not():
    clause = query_clause("ipc:H02M AND NOT tscd:(均衡)")

    assert "must_not" in clause["bool"]["must"][1]["bool"]
    assert clause["bool"]["must"][1]["bool"]["must_not"][0]["multi_match"]["query"] == "均衡"


def test_applicant_current_assignee_and_type_fields():
    assert query_clause("applicant:(华为技术有限公司)")["multi_match"]["fields"] == [
        "Applicant",
        "ApplicantNormalized",
        "FirstApplicant",
    ]
    assert query_clause("currentAssignee:(华为技术有限公司)")["multi_match"]["fields"] == [
        "Assignee",
        "AssigneeNormalized",
    ]
    assert query_clause("type:(发明专利)")["multi_match"]["fields"] == ["Type", "PatentTypeCode", "Kind"]


def test_document_year_maps_to_publication_date_range():
    assert query_clause("documentYear:[2020 TO 2024]") == {
        "range": {
            "PublicationDate": {
                "gte": "2020-01-01",
                "lte": "2024-12-31",
            }
        }
    }


@pytest.mark.parametrize(
    "q,error",
    [
        ("foo:(均衡)", "不支持字段 foo"),
        ("ad:[2020-13-01 TO 2020-12-31]", "日期格式非法"),
        ("ad:[2021-01-01 TO 2020-12-31]", "范围起始值不能晚于结束值"),
        ("documentYear:[2024 TO 2020]", "范围起始值不能晚于结束值"),
    ],
)
def test_rejects_invalid_dsl_inputs(q, error):
    with pytest.raises(QuerySyntaxError, match=error):
        build_search_dsl(SearchRequest(q=q))
```

- [ ] **Step 2: Run DSL tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_search_dsl_builder.py tests/test_query_dsl_builder_stage6.py -q
```

Expected: FAIL because `dsl_builder.py` still uses regex and old exception.

- [ ] **Step 3: Replace DSL builder internals**

Modify `app/query/dsl_builder.py`:

```python
from datetime import date

from app.core.exceptions import QuerySyntaxError
from app.mappings.legal_status_mapping import build_legal_status_clause
from app.mappings.query_field_mapping import IPC_FIELDS, LEGAL_STATUS_FIELD, SUPPORTED_FIELDS, TEXT_FIELD_MAPPING
from app.query.ast import AndNode, FieldQuery, NotNode, OrNode, PhraseNode, QueryNode, RangeQuery, WordNode
from app.query.parser import parse_query
from app.schemas.search import SearchRequest


def build_search_dsl(request: SearchRequest) -> dict:
    must = [_build_node_clause(parse_query(request.q))]
    filters = []

    if request.ds == "cn":
        filters.append({"term": {"Country": "CN"}})

    return {
        "from": request.offset,
        "size": request.page_size,
        "track_total_hits": True,
        "query": {
            "bool": {
                "must": must,
                "filter": filters,
            }
        },
        "sort": _build_sort(request.sort),
    }


def _build_node_clause(node: QueryNode) -> dict:
    if isinstance(node, WordNode):
        return _multi_match(node.value, ["Title", "Abstract"])
    if isinstance(node, PhraseNode):
        return _multi_match(node.value, ["Title", "Abstract"])
    if isinstance(node, FieldQuery):
        return _build_field_clause(node)
    if isinstance(node, RangeQuery):
        return _build_range_clause(node)
    if isinstance(node, AndNode):
        return {"bool": {"must": [_build_node_clause(node.left), _build_node_clause(node.right)]}}
    if isinstance(node, OrNode):
        return {
            "bool": {
                "should": [_build_node_clause(node.left), _build_node_clause(node.right)],
                "minimum_should_match": 1,
            }
        }
    if isinstance(node, NotNode):
        return {"bool": {"must_not": [_build_node_clause(node.child)]}}
    raise QuerySyntaxError("q 查询语法错误：无法解析查询式")


def _build_field_clause(node: FieldQuery) -> dict:
    field = node.field
    if field not in SUPPORTED_FIELDS:
        raise QuerySyntaxError(f"q 查询语法错误：不支持字段 {field}")

    value = _node_value(node.value)
    if not value and not isinstance(node.value, (AndNode, OrNode, NotNode)):
        raise QuerySyntaxError(f"q 查询语法错误：字段 {field} 的值不能为空")

    if field in TEXT_FIELD_MAPPING:
        return _build_field_value_clause(node.value, TEXT_FIELD_MAPPING[field])
    if field == "ipc":
        return _build_ipc_clause(value)
    if field == LEGAL_STATUS_FIELD:
        return build_legal_status_clause(value)

    raise QuerySyntaxError(f"q 查询语法错误：不支持字段 {field}")


def _build_field_value_clause(value_node: QueryNode, fields: list[str]) -> dict:
    if isinstance(value_node, (WordNode, PhraseNode)):
        return _multi_match(value_node.value, fields)
    if isinstance(value_node, AndNode):
        return {
            "bool": {
                "must": [
                    _build_field_value_clause(value_node.left, fields),
                    _build_field_value_clause(value_node.right, fields),
                ]
            }
        }
    if isinstance(value_node, OrNode):
        return {
            "bool": {
                "should": [
                    _build_field_value_clause(value_node.left, fields),
                    _build_field_value_clause(value_node.right, fields),
                ],
                "minimum_should_match": 1,
            }
        }
    if isinstance(value_node, NotNode):
        return {"bool": {"must_not": [_build_field_value_clause(value_node.child, fields)]}}
    raise QuerySyntaxError("q 查询语法错误：字段值不支持该表达式")


def _build_range_clause(node: RangeQuery) -> dict:
    if node.field == "ad":
        start = _parse_date(node.start)
        end = _parse_date(node.end)
        if start > end:
            raise QuerySyntaxError("q 查询语法错误：范围起始值不能晚于结束值")
        return {"range": {"ApplicationDate": {"gte": node.start, "lte": node.end}}}

    if node.field == "documentYear":
        start_year = _parse_year(node.start)
        end_year = _parse_year(node.end)
        if start_year > end_year:
            raise QuerySyntaxError("q 查询语法错误：范围起始值不能晚于结束值")
        return {
            "range": {
                "PublicationDate": {
                    "gte": f"{start_year}-01-01",
                    "lte": f"{end_year}-12-31",
                }
            }
        }

    raise QuerySyntaxError(f"q 查询语法错误：不支持字段 {node.field}")


def _build_ipc_clause(code: str) -> dict:
    if not code:
        raise QuerySyntaxError("q 查询语法错误：字段 ipc 的值不能为空")
    should = [
        {"term": {"IPC": code}},
        {"match": {"IPCList": code}},
        {"term": {"IPCSmallCategory": code}},
        {"term": {"IPCLargeGroup": code}},
        {"term": {"IPCSmallGroup": code}},
    ]
    return {"bool": {"should": should, "minimum_should_match": 1}}


def _node_value(node: QueryNode) -> str:
    if isinstance(node, (WordNode, PhraseNode)):
        return node.value.strip()
    return ""


def _multi_match(query: str, fields: list[str]) -> dict:
    return {"multi_match": {"query": query, "fields": fields}}


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise QuerySyntaxError("q 查询语法错误：日期格式非法") from exc


def _parse_year(value: str) -> int:
    if len(value) != 4 or not value.isdigit():
        raise QuerySyntaxError("q 查询语法错误：日期格式非法")
    return int(value)


def _build_sort(sort: str) -> list:
    if sort == "!applicationDate":
        return [{"ApplicationDate": {"order": "desc"}}]
    return ["_score"]
```

- [ ] **Step 4: Run DSL tests and full regression**

Run:

```bash
.venv/bin/python -m pytest tests/test_search_dsl_builder.py tests/test_query_dsl_builder_stage6.py -q
.venv/bin/python -m pytest -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/query/dsl_builder.py tests/test_search_dsl_builder.py tests/test_query_dsl_builder_stage6.py
git commit -m "feat: build dsl from boolean query ast"
```

---

### Task 5: API Error Handling and No-OpenSearch Guard

**Files:**
- Modify: `app/api/search.py`
- Modify: `tests/test_search_api.py`
- Test: `tests/test_search_service.py`

**Interfaces:**
- Consumes: `QuerySyntaxError`
- Produces API behavior: invalid query returns HTTP 400 with `{"success": false, "code": 40001, "message": "...", "data": null}`

- [ ] **Step 1: Write failing API tests**

Append to `tests/test_search_api.py`:

```python
from app.services.search_service import SearchService


class ExplodingRepository:
    def search(self, body):
        raise AssertionError("OpenSearch must not be called for invalid query syntax")


@pytest.mark.parametrize(
    "q",
    [
        "ipc:H02M AND AND tscd:(均衡)",
        "AND tscd:(均衡)",
        "tscd:(均衡) OR",
        'tscd:("均衡)',
        "tscd:()",
        "ipc:",
        "foo:(均衡)",
        "ad:[2020-01-01 2020-12-31]",
        "ad:[2020-13-01 TO 2020-12-31]",
        "ad:[2021-01-01 TO 2020-12-31]",
        "documentYear:[2024 TO 2020]",
        "NOT",
        "tscd:(均衡) NOT",
    ],
)
def test_invalid_stage_six_queries_return_40001_without_repository_call(client, q):
    app.dependency_overrides[get_search_service] = lambda: SearchService(repository=ExplodingRepository())
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        response = client().post("/api/patent/search", json={"q": q})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["detail"]["success"] is False
    assert response.json()["detail"]["code"] == 40001
    assert response.json()["detail"]["data"] is None
```

- [ ] **Step 2: Run API tests and verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_search_api.py -q
```

Expected: FAIL because `app/api/search.py` still catches `UnsupportedQuerySyntax`.

- [ ] **Step 3: Update API error handling**

Modify `app/api/search.py`:

```python
from fastapi import APIRouter, Depends, HTTPException

from app.core.exceptions import QuerySyntaxError
from app.core.security import require_api_key
from app.schemas.search import SearchRequest
from app.services.search_service import SearchService


router = APIRouter(prefix="/api/patent", tags=["patent-search"])


def get_search_service() -> SearchService:
    return SearchService()


@router.post("/search", dependencies=[Depends(require_api_key)])
def search_patents(
    request: SearchRequest,
    service: SearchService = Depends(get_search_service),
):
    try:
        return service.search(request)
    except QuerySyntaxError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "code": 40001,
                "message": str(exc),
                "data": None,
            },
        )
```

- [ ] **Step 4: Run API tests and full regression**

Run:

```bash
.venv/bin/python -m pytest tests/test_search_api.py tests/test_search_service.py -q
.venv/bin/python -m pytest -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/api/search.py tests/test_search_api.py tests/test_search_service.py
git commit -m "feat: return 40001 for query syntax errors"
```

---

### Task 6: Docs, Smoke Cases, and Stage 6 Validation Artifacts

**Files:**
- Modify: `scripts/smoke_search.py`
- Modify: `docs/query_syntax.md`
- Modify: `docs/field_mapping.md`
- Create or modify: `docs/stage6_test_report.md`

**Interfaces:**
- Consumes: final stage 6 API behavior.
- Produces: documented syntax and smoke evidence.

- [ ] **Step 1: Update smoke cases**

Modify `scripts/smoke_search.py` to include these `q` examples in the smoke loop:

```python
STAGE_6_QUERIES = [
    "tscd:(均衡)",
    "ipc:H02M AND tscd:(均衡)",
    "applicant:(华为技术有限公司)",
    "currentAssignee:(华为技术有限公司)",
    "legalStatus:(有效专利)",
    "documentYear:[2020 TO 2024]",
    "ipc:H02M AND NOT tscd:(均衡)",
]
```

- [ ] **Step 2: Update syntax and field mapping docs**

Add to `docs/query_syntax.md`:

```markdown
## 阶段六已支持语法

- 字段查询：`title`、`ab`、`tscd`、`ipc`、`applicant`、`currentAssignee`、`legalStatus`、`type`
- 日期范围：`ad:[YYYY-MM-DD TO YYYY-MM-DD]`
- 公开年范围：`documentYear:[YYYY TO YYYY]`
- 布尔运算：`AND`、`OR`、`NOT`
- 括号分组：`(title:(均衡) OR title:(平衡)) AND ipc:H02M`
- 短语：`tscd:("均衡" OR "平衡")`
- 非法查询语法返回 `40001`，且不会访问 OpenSearch
```

Add to `docs/field_mapping.md`:

```markdown
## 阶段六查询字段映射

| q 字段 | OpenSearch 字段 |
|---|---|
| `title` | `Title`, `TitleCN`, `TitleEN` |
| `ab` | `Abstract`, `AbstractCN`, `AbstractEN` |
| `tscd` | `Title`, `Abstract`, `MainClaim`, `Requirement`, `Instructions` |
| `ipc` | `IPC`, `IPCList`, `IPCSmallCategory`, `IPCLargeGroup`, `IPCSmallGroup` |
| `applicant` | `Applicant`, `ApplicantNormalized`, `FirstApplicant` |
| `currentAssignee` | `Assignee`, `AssigneeNormalized` |
| `legalStatus` | `LatestLegalStatus`, `LegalStatus` |
| `type` | `Type`, `PatentTypeCode`, `Kind` |
| `ad` | `ApplicationDate` |
| `documentYear` | `PublicationDate` |
```

- [ ] **Step 3: Run automated tests**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: PASS.

- [ ] **Step 4: Run local API health check**

Start the API if it is not already running:

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another shell, run:

```bash
.venv/bin/python scripts/smoke_health.py
```

Expected: health smoke succeeds.

- [ ] **Step 5: Run real OpenSearch smoke**

Only run after `.env` contains valid production OpenSearch and `API_TOKEN` values:

```bash
.venv/bin/python scripts/smoke_search.py
```

Expected: all stage 6 smoke queries return HTTP 200 and at least a valid response shape with `total`, `page`, `page_size`, and `records`.

- [ ] **Step 6: Write test report**

Create `docs/stage6_test_report.md`:

```markdown
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
- 结果：记录实际通过数量

## 冒烟测试

- 命令：`.venv/bin/python scripts/smoke_search.py`
- 结果：记录每个查询的 HTTP 状态、total、records 数量

## 结论

- 通过条件：自动化测试通过，非法输入不访问 OpenSearch，真实 OpenSearch 冒烟通过
- 未覆盖范围：阶段七详情、引证、SaaS 联调、生产网关
```

- [ ] **Step 7: Commit**

```bash
git add scripts/smoke_search.py docs/query_syntax.md docs/field_mapping.md docs/stage6_test_report.md
git commit -m "docs: add stage 6 validation artifacts"
```

---

## Review Checklist

- Every stage 6 supported field has a parser and DSL test.
- `NOT title:(外观)` and `ipc:H02M AND NOT tscd:(均衡)` are positive tests.
- The invalid input list from the design returns `40001` at API level.
- Invalid syntax tests use a repository that raises if OpenSearch is called.
- `ad` uses `date.fromisoformat`.
- `documentYear` rejects reversed year range.
- Stage 5 tests still pass.
- Smoke script includes all stage 6 examples from the design.
