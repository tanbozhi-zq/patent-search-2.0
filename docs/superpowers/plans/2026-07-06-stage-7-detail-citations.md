# Stage 7 Detail and Citations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement patent detail and citations APIs that read from `patent_index` and return responses compatible with both this service's HTTP API style and the SaaS PatentHub tool contract.

**Architecture:** Follow the existing `api -> service -> repository -> mapper` pattern. Add repository lookup by stable identifier, detail/citation mappers for dual-compatible response fields, services for error translation, and FastAPI routes with existing API-key protection.

**Tech Stack:** Python 3, FastAPI, Pydantic/FastAPI query parsing, pytest, OpenSearch DSL dictionaries, existing repository and security helpers.

---

## Reference Documents

- `docs/superpowers/specs/2026-07-06-stage-7-detail-citations-design.md`
- `docs/saas_patent_contract_audit.md`
- `docs/api_spec.md`
- `docs/field_mapping.md`

## Global Constraints

- Do not modify SaaS source copy under `patent_harness_base_副本/`.
- Do not commit SaaS source copy.
- Do not modify OpenSearch index mapping.
- Do not rebuild OpenSearch indexes.
- Do not implement SaaS联调 in stage 7.
- Do not implement `enterprise_patent_portrait`.
- Do not implement a separate `patent_get_legal_history` endpoint in stage 7.
- Do not copy PatentHub's 60-minute session-bound ID behavior.
- Successful `detail` and `citations` responses return business objects directly.
- Error responses use `{success, code, message, data}` via `service_error`.
- Commit after each task.

## File Structure

- Modify `app/core/exceptions.py`: add stage 7 domain exceptions.
- Modify `app/repositories/opensearch_repo.py`: add stable identifier lookup.
- Create `app/mappings/detail_mapper.py`: map OpenSearch hit to dual-compatible detail response.
- Create `app/mappings/citation_mapper.py`: map OpenSearch hit to dual-compatible citations response.
- Create `app/services/detail_service.py`: detail business service.
- Create `app/services/citation_service.py`: citations business service.
- Modify `app/api/detail.py`: implement detail route.
- Modify `app/api/citations.py`: implement citations route.
- Create `tests/test_detail_mapper.py`.
- Create `tests/test_citation_mapper.py`.
- Create `tests/test_detail_service.py`.
- Create `tests/test_citation_service.py`.
- Create `tests/test_detail_api.py`.
- Create `tests/test_citations_api.py`.
- Modify `tests/test_opensearch_repo.py`.
- Modify `docs/api_spec.md`.
- Modify `docs/field_mapping.md`.
- Create `docs/stage7_test_report.md` after implementation/testing.
- Create `scripts/smoke_detail_citations.py`.

---

### Task 1: Repository Stable Patent Lookup

**Files:**
- Modify: `app/repositories/opensearch_repo.py`
- Modify: `tests/test_opensearch_repo.py`

**Interface:**
- `OpenSearchRepository.get_patent_by_identifier(identifier: str) -> dict | None`
- Returns the first OpenSearch hit, not only `_source`, so mappers may access `_score` later if needed.

- [ ] **Step 1: Write failing repository tests**

Append to `tests/test_opensearch_repo.py`:

```python
class FakeSearchClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def search(self, index, body):
        self.calls.append({"index": index, "body": body})
        return self.responses.pop(0)


def test_repository_get_patent_by_identifier_uses_patent_id_first():
    settings = Settings(opensearch_index="patent_index")
    repository = OpenSearchRepository(settings=settings)
    repository.client = FakeSearchClient(
        [
            {
                "hits": {
                    "hits": [
                        {
                            "_id": "1",
                            "_source": {
                                "patent_id": "cn-1",
                                "PublicationNumber": "CN1A",
                            },
                        }
                    ]
                }
            }
        ]
    )

    hit = repository.get_patent_by_identifier("cn-1")

    assert hit["_source"]["patent_id"] == "cn-1"
    assert repository.client.calls[0]["index"] == "patent_index"
    assert repository.client.calls[0]["body"]["size"] == 1
    assert repository.client.calls[0]["body"]["query"]["bool"]["should"][0] == {
        "term": {"patent_id": "cn-1"}
    }


def test_repository_get_patent_by_identifier_falls_back_to_publication_number():
    settings = Settings(opensearch_index="patent_index")
    repository = OpenSearchRepository(settings=settings)
    repository.client = FakeSearchClient(
        [
            {"hits": {"hits": []}},
            {
                "hits": {
                    "hits": [
                        {
                            "_id": "2",
                            "_source": {
                                "patent_id": "cn-2",
                                "PublicationNumber": "CN2A",
                            },
                        }
                    ]
                }
            },
        ]
    )

    hit = repository.get_patent_by_identifier("CN2A")

    assert hit["_source"]["patent_id"] == "cn-2"
    assert repository.client.calls[0]["body"]["query"]["bool"]["should"][0] == {
        "term": {"patent_id": "CN2A"}
    }
    assert repository.client.calls[1]["body"]["query"]["bool"]["should"][0] == {
        "term": {"PublicationNumber": "CN2A"}
    }


def test_repository_get_patent_by_identifier_falls_back_to_application_number():
    settings = Settings(opensearch_index="patent_index")
    repository = OpenSearchRepository(settings=settings)
    repository.client = FakeSearchClient(
        [
            {"hits": {"hits": []}},
            {"hits": {"hits": []}},
            {
                "hits": {
                    "hits": [
                        {
                            "_id": "3",
                            "_source": {
                                "patent_id": "cn-3",
                                "ApplicationNumber": "CN2024000001",
                            },
                        }
                    ]
                }
            },
        ]
    )

    hit = repository.get_patent_by_identifier("CN2024000001")

    assert hit["_source"]["patent_id"] == "cn-3"
    assert repository.client.calls[2]["body"]["query"]["bool"]["should"][0] == {
        "term": {"ApplicationNumber": "CN2024000001"}
    }


def test_repository_get_patent_by_identifier_returns_none_when_not_found():
    settings = Settings(opensearch_index="patent_index")
    repository = OpenSearchRepository(settings=settings)
    repository.client = FakeSearchClient(
        [
            {"hits": {"hits": []}},
            {"hits": {"hits": []}},
            {"hits": {"hits": []}},
        ]
    )

    hit = repository.get_patent_by_identifier("missing")

    assert hit is None
    assert len(repository.client.calls) == 3
```

- [ ] **Step 2: Run repository tests and verify failure**

```bash
.venv/bin/python -m pytest tests/test_opensearch_repo.py -q
```

Expected: FAIL because `get_patent_by_identifier` is not defined.

- [ ] **Step 3: Implement repository lookup**

In `app/repositories/opensearch_repo.py`, add methods inside `OpenSearchRepository`:

```python
    def get_patent_by_identifier(self, identifier: str) -> Optional[dict]:
        for field in ("patent_id", "PublicationNumber", "ApplicationNumber"):
            raw = self.search(
                {
                    "size": 1,
                    "query": self._identifier_query(field, identifier),
                }
            )
            hit = self._first_hit(raw)
            if hit is not None:
                return hit
        return None

    def _identifier_query(self, field: str, identifier: str) -> dict:
        return {
            "bool": {
                "should": [
                    {"term": {field: identifier}},
                    {"match_phrase": {field: identifier}},
                ],
                "minimum_should_match": 1,
            }
        }

    def _first_hit(self, raw: dict) -> Optional[dict]:
        hits = raw.get("hits", {}).get("hits", [])
        if not hits:
            return None
        return hits[0]
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/test_opensearch_repo.py -q
.venv/bin/python -m pytest -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/repositories/opensearch_repo.py tests/test_opensearch_repo.py
git commit -m "feat: add patent identifier lookup"
```

---

### Task 2: Detail Response Mapper

**Files:**
- Create: `app/mappings/detail_mapper.py`
- Create: `tests/test_detail_mapper.py`

**Interface:**
- `map_detail_response(hit: dict, include_description: bool = False) -> dict`

- [ ] **Step 1: Write failing mapper tests**

Create `tests/test_detail_mapper.py`:

```python
from app.mappings.detail_mapper import map_detail_response


def _hit():
    return {
        "_source": {
            "patent_id": "cn-1",
            "ApplicationNumber": "CN202411108082.1",
            "PublicationNumber": "CN119188170B",
            "Title": "一种轴承座壳体的加工工艺",
            "Abstract": "本发明公开了一种加工工艺。",
            "Applicant": "某某公司",
            "FirstApplicant": "某某公司",
            "Assignee": "某某公司",
            "Inventor": "张三;李四",
            "FirstInventor": "张三",
            "ApplicantAddress": "北京市",
            "Agency": "某某代理机构",
            "Agent": "王五",
            "IPC": "B23P15/00",
            "IPCList": ["B23P15/00", "B23Q3/00"],
            "ApplicationDate": "2024-08-13",
            "PublicationDate": "2026-06-12",
            "LatestLegalStatus": "授权",
            "LegalStatus": "授权公告",
            "Type": "发明专利",
            "MainClaim": "一种轴承座壳体的加工工艺，其特征在于...",
            "Requirement": "1. 一种轴承座壳体的加工工艺...",
            "Instructions": "说明书正文",
            "Family": [{"id": "family-1"}],
            "Drawings": [{"url": "drawing.png"}],
            "AbstractFigureUrl": "figure.png",
            "PDFList": ["a.pdf"],
        }
    }


def test_map_detail_response_returns_camel_and_snake_case_fields():
    mapped = map_detail_response(_hit())

    assert mapped["id"] == "cn-1"
    assert mapped["patent_id"] == "cn-1"
    assert mapped["applicationNumber"] == "CN202411108082.1"
    assert mapped["application_number"] == "CN202411108082.1"
    assert mapped["documentNumber"] == "CN119188170B"
    assert mapped["document_number"] == "CN119188170B"
    assert mapped["title"] == "一种轴承座壳体的加工工艺"
    assert mapped["ti"] == "一种轴承座壳体的加工工艺"
    assert mapped["abstract"] == "本发明公开了一种加工工艺。"
    assert mapped["summary"] == "本发明公开了一种加工工艺。"
    assert mapped["currentAssignee"] == "某某公司"
    assert mapped["current_assignee"] == "某某公司"
    assert mapped["mainIpc"] == "B23P15/00"
    assert mapped["main_ipc"] == "B23P15/00"
    assert mapped["legalStatus"] == "授权"
    assert mapped["legal_status"] == "授权"
    assert mapped["claims"] == "1. 一种轴承座壳体的加工工艺..."
    assert mapped["mainClaim"] == "一种轴承座壳体的加工工艺，其特征在于..."
    assert mapped["main_claim"] == "一种轴承座壳体的加工工艺，其特征在于..."


def test_map_detail_response_omits_description_by_default():
    mapped = map_detail_response(_hit())

    assert "description" not in mapped


def test_map_detail_response_includes_description_when_requested():
    mapped = map_detail_response(_hit(), include_description=True)

    assert mapped["description"] == "说明书正文"


def test_map_detail_response_uses_empty_values_for_missing_fields():
    mapped = map_detail_response({"_source": {"patent_id": "cn-empty"}})

    assert mapped["id"] == "cn-empty"
    assert mapped["applicationNumber"] == ""
    assert mapped["application_number"] == ""
    assert mapped["ipcMainList"] == []
    assert mapped["family"] == []
    assert mapped["drawings"] == []
    assert mapped["claims"] == ""
```

- [ ] **Step 2: Run mapper tests and verify failure**

```bash
.venv/bin/python -m pytest tests/test_detail_mapper.py -q
```

Expected: FAIL because `app.mappings.detail_mapper` does not exist.

- [ ] **Step 3: Implement mapper**

Create `app/mappings/detail_mapper.py`:

```python
def map_detail_response(hit: dict, include_description: bool = False) -> dict:
    source = hit.get("_source", {})
    patent_id = _string(
        source.get("patent_id")
        or source.get("PublicationNumber")
        or source.get("ApplicationNumber")
    )
    title = _string(source.get("Title"))
    abstract = _string(source.get("Abstract"))
    application_number = _string(source.get("ApplicationNumber"))
    document_number = _string(source.get("PublicationNumber"))
    application_date = _string(source.get("ApplicationDate"))
    document_date = _string(source.get("PublicationDate"))
    legal_status = _string(source.get("LatestLegalStatus") or source.get("LegalStatus"))
    current_status = _string(source.get("LatestLegalStatus"))
    current_assignee = _string(source.get("Assignee") or source.get("Applicant"))
    main_ipc = _string(source.get("IPC"))
    main_claim = _string(source.get("MainClaim"))

    response = {
        "id": patent_id,
        "patent_id": patent_id,
        "title": title,
        "ti": title,
        "abstract": abstract,
        "ab": abstract,
        "summary": abstract,
        "applicationNumber": application_number,
        "application_number": application_number,
        "documentNumber": document_number,
        "document_number": document_number,
        "applicationDate": application_date,
        "application_date": application_date,
        "documentDate": document_date,
        "document_date": document_date,
        "type": _string(source.get("Type")),
        "legalStatus": legal_status,
        "legal_status": legal_status,
        "currentStatus": current_status,
        "current_status": current_status,
        "applicant": _string(source.get("Applicant")),
        "firstApplicant": _string(source.get("FirstApplicant")),
        "first_applicant": _string(source.get("FirstApplicant")),
        "currentAssignee": current_assignee,
        "current_assignee": current_assignee,
        "assignee": _string(source.get("Assignee")),
        "inventor": _string(source.get("Inventor")),
        "firstInventor": _string(source.get("FirstInventor")),
        "first_inventor": _string(source.get("FirstInventor")),
        "applicantAddress": _string(source.get("ApplicantAddress")),
        "applicant_address": _string(source.get("ApplicantAddress")),
        "agency": _string(source.get("Agency")),
        "agent": _string(source.get("Agent")),
        "ipc": main_ipc,
        "mainIpc": main_ipc,
        "main_ipc": main_ipc,
        "ipcMainList": _array(source.get("IPCList")),
        "ipc_main_list": _array(source.get("IPCList")),
        "loc": _string(source.get("LOC")),
        "priorityNumber": _string(source.get("PriorityNumber")),
        "priority_number": _string(source.get("PriorityNumber")),
        "fullPriorityNumber": _string(source.get("FullPriorityNumber")),
        "full_priority_number": _string(source.get("FullPriorityNumber")),
        "pctDate": _string(source.get("PCTDate")),
        "pct_date": _string(source.get("PCTDate")),
        "pctApplicationData": _string(source.get("PCTApplicationData")),
        "pct_application_data": _string(source.get("PCTApplicationData")),
        "pctPublicationData": _string(source.get("PCTPublicationData")),
        "pct_publication_data": _string(source.get("PCTPublicationData")),
        "imagePath": _string(source.get("AbstractFigureUrl") or source.get("ImagePath")),
        "image_path": _string(source.get("AbstractFigureUrl") or source.get("ImagePath")),
        "pdfList": _array(source.get("PDFList") or source.get("pdfList")),
        "pdf_list": _array(source.get("PDFList") or source.get("pdfList")),
        "family": _first_array(source, ("Family", "SimpleFamily", "ExtendedFamily", "DocDBFamily")),
        "drawings": _first_array(source, ("Drawings", "DescriptionImages")),
        "legalStatusHistory": _array(source.get("LegalStatusHistory") or source.get("LegalStatus")),
        "legal_status_history": _array(source.get("LegalStatusHistory") or source.get("LegalStatus")),
        "mainClaim": main_claim,
        "main_claim": main_claim,
        "claims": _string(source.get("Requirement")),
    }

    if include_description:
        response["description"] = _string(source.get("Instructions"))

    return response


def _string(value) -> str:
    if value is None:
        return ""
    return str(value)


def _array(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _first_array(source: dict, fields: tuple[str, ...]) -> list:
    for field in fields:
        value = source.get(field)
        if value:
            return _array(value)
    return []
```

- [ ] **Step 4: Run mapper and full tests**

```bash
.venv/bin/python -m pytest tests/test_detail_mapper.py -q
.venv/bin/python -m pytest -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/mappings/detail_mapper.py tests/test_detail_mapper.py
git commit -m "feat: map patent detail response"
```

---

### Task 3: Citations Response Mapper

**Files:**
- Create: `app/mappings/citation_mapper.py`
- Create: `tests/test_citation_mapper.py`

**Interface:**
- `map_citations_response(hit: dict) -> dict`

- [ ] **Step 1: Write failing mapper tests**

Create `tests/test_citation_mapper.py`:

```python
from app.mappings.citation_mapper import map_citations_response


def test_map_citations_response_returns_tool_and_raw_compat_fields():
    hit = {
        "_source": {
            "patent_id": "cn-1",
            "ReferencesCited": [
                {
                    "id": "cn-ref",
                    "title": "引用专利",
                    "applicant": "引用申请人",
                    "applicationDate": "2020-01-01",
                    "applicationNumber": "CN2020000001",
                    "type": "发明专利",
                    "legalStatus": "有效",
                    "mainIpc": "G06F",
                }
            ],
            "ReferencesCitedRaw": "非专利文献 A",
            "ReferencesCitedText": "非专利文献 B",
            "RelatedDocuments": [
                {
                    "id": "cn-cited-by",
                    "title": "被引专利",
                    "applicant": "被引申请人",
                    "applicationDate": "2021-01-01",
                    "applicationNumber": "CN2021000001",
                    "type": "实用新型",
                    "legalStatus": "授权",
                    "mainIpc": "H02M",
                }
            ],
        }
    }

    mapped = map_citations_response(hit)

    assert mapped["patent_id"] == "cn-1"
    assert mapped["referencesCited"] == hit["_source"]["ReferencesCited"]
    assert mapped["referencesCitedRaw"] == "非专利文献 A"
    assert mapped["referencesCitedText"] == "非专利文献 B"
    assert mapped["relatedDocuments"] == hit["_source"]["RelatedDocuments"]
    assert mapped["patent_references"][0] == {
        "id": "cn-ref",
        "title": "引用专利",
        "applicant": "引用申请人",
        "application_date": "2020-01-01",
        "application_number": "CN2020000001",
        "type": "发明专利",
        "legal_status": "有效",
        "main_ipc": "G06F",
    }
    assert mapped["cited_by"][0]["id"] == "cn-cited-by"
    assert mapped["non_patent_references"] == ["非专利文献 A", "非专利文献 B"]


def test_map_citations_response_handles_missing_fields():
    mapped = map_citations_response({"_source": {"patent_id": "cn-empty"}})

    assert mapped == {
        "patent_id": "cn-empty",
        "cited_by": [],
        "patent_references": [],
        "non_patent_references": [],
        "referencesCited": [],
        "referencesCitedRaw": "",
        "referencesCitedText": "",
        "relatedDocuments": [],
    }
```

- [ ] **Step 2: Run citation mapper tests and verify failure**

```bash
.venv/bin/python -m pytest tests/test_citation_mapper.py -q
```

Expected: FAIL because `app.mappings.citation_mapper` does not exist.

- [ ] **Step 3: Implement citation mapper**

Create `app/mappings/citation_mapper.py`:

```python
def map_citations_response(hit: dict) -> dict:
    source = hit.get("_source", {})
    references_cited = _array(source.get("ReferencesCited"))
    related_documents = _array(source.get("RelatedDocuments"))
    raw = _string(source.get("ReferencesCitedRaw"))
    text = _string(source.get("ReferencesCitedText"))

    return {
        "patent_id": _string(source.get("patent_id")),
        "cited_by": [_summarize_patent(item) for item in related_documents if isinstance(item, dict)],
        "patent_references": [
            _summarize_patent(item) for item in references_cited if isinstance(item, dict)
        ],
        "non_patent_references": _non_patent_references(raw, text),
        "referencesCited": references_cited,
        "referencesCitedRaw": raw,
        "referencesCitedText": text,
        "relatedDocuments": related_documents,
    }


def _summarize_patent(item: dict) -> dict:
    return {
        "id": _string(item.get("id") or item.get("patent_id")),
        "title": _string(item.get("title") or item.get("Title")),
        "applicant": _string(item.get("applicant") or item.get("Applicant")),
        "application_date": _string(item.get("applicationDate") or item.get("ApplicationDate")),
        "application_number": _string(item.get("applicationNumber") or item.get("ApplicationNumber")),
        "type": _string(item.get("type") or item.get("Type")),
        "legal_status": _string(item.get("legalStatus") or item.get("LatestLegalStatus") or item.get("LegalStatus")),
        "main_ipc": _string(item.get("mainIpc") or item.get("IPC")),
    }


def _non_patent_references(raw: str, text: str) -> list[str]:
    values = []
    if raw:
        values.append(raw)
    if text and text != raw:
        values.append(text)
    return values


def _string(value) -> str:
    if value is None:
        return ""
    return str(value)


def _array(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]
```

- [ ] **Step 4: Run mapper and full tests**

```bash
.venv/bin/python -m pytest tests/test_citation_mapper.py -q
.venv/bin/python -m pytest -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/mappings/citation_mapper.py tests/test_citation_mapper.py
git commit -m "feat: map patent citation response"
```

---

### Task 4: Detail and Citation Services

**Files:**
- Modify: `app/core/exceptions.py`
- Create: `app/services/detail_service.py`
- Create: `app/services/citation_service.py`
- Create: `tests/test_detail_service.py`
- Create: `tests/test_citation_service.py`

**Interfaces:**
- `DetailService.get_detail(patent_id: str, include_description: bool = False) -> dict`
- `CitationService.get_citations(patent_id: str) -> dict`

- [ ] **Step 1: Write failing service tests**

Create `tests/test_detail_service.py`:

```python
import pytest

from app.core.exceptions import InvalidPatentIdentifierError, OpenSearchQueryError, PatentNotFoundError
from app.services.detail_service import DetailService


class FakeRepository:
    def __init__(self, hit=None, error=None):
        self.hit = hit
        self.error = error
        self.identifier = None

    def get_patent_by_identifier(self, identifier):
        self.identifier = identifier
        if self.error:
            raise self.error
        return self.hit


def test_detail_service_returns_mapped_detail():
    repository = FakeRepository(
        {
            "_source": {
                "patent_id": "cn-1",
                "Title": "标题",
                "Requirement": "权利要求",
            }
        }
    )
    service = DetailService(repository=repository)

    result = service.get_detail(" cn-1 ")

    assert repository.identifier == "cn-1"
    assert result["patent_id"] == "cn-1"
    assert result["title"] == "标题"
    assert result["claims"] == "权利要求"


def test_detail_service_raises_not_found():
    service = DetailService(repository=FakeRepository(hit=None))

    with pytest.raises(PatentNotFoundError):
        service.get_detail("missing")


def test_detail_service_rejects_empty_patent_id():
    service = DetailService(repository=FakeRepository())

    with pytest.raises(InvalidPatentIdentifierError):
        service.get_detail(" ")


def test_detail_service_wraps_repository_error():
    service = DetailService(repository=FakeRepository(error=RuntimeError("boom")))

    with pytest.raises(OpenSearchQueryError):
        service.get_detail("cn-1")
```

Create `tests/test_citation_service.py`:

```python
import pytest

from app.core.exceptions import InvalidPatentIdentifierError, OpenSearchQueryError, PatentNotFoundError
from app.services.citation_service import CitationService


class FakeRepository:
    def __init__(self, hit=None, error=None):
        self.hit = hit
        self.error = error
        self.identifier = None

    def get_patent_by_identifier(self, identifier):
        self.identifier = identifier
        if self.error:
            raise self.error
        return self.hit


def test_citation_service_returns_mapped_citations():
    repository = FakeRepository(
        {
            "_source": {
                "patent_id": "cn-1",
                "ReferencesCitedRaw": "非专利文献",
            }
        }
    )
    service = CitationService(repository=repository)

    result = service.get_citations(" cn-1 ")

    assert repository.identifier == "cn-1"
    assert result["patent_id"] == "cn-1"
    assert result["non_patent_references"] == ["非专利文献"]


def test_citation_service_raises_not_found():
    service = CitationService(repository=FakeRepository(hit=None))

    with pytest.raises(PatentNotFoundError):
        service.get_citations("missing")


def test_citation_service_rejects_empty_patent_id():
    service = CitationService(repository=FakeRepository())

    with pytest.raises(InvalidPatentIdentifierError):
        service.get_citations(" ")


def test_citation_service_wraps_repository_error():
    service = CitationService(repository=FakeRepository(error=RuntimeError("boom")))

    with pytest.raises(OpenSearchQueryError):
        service.get_citations("cn-1")
```

- [ ] **Step 2: Run service tests and verify failure**

```bash
.venv/bin/python -m pytest tests/test_detail_service.py tests/test_citation_service.py -q
```

Expected: FAIL because exceptions and services do not exist.

- [ ] **Step 3: Add exceptions**

Modify `app/core/exceptions.py`:

```python
from fastapi import HTTPException


class QuerySyntaxError(ValueError):
    pass


class InvalidPatentIdentifierError(ValueError):
    pass


class PatentNotFoundError(LookupError):
    pass


class OpenSearchQueryError(RuntimeError):
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

- [ ] **Step 4: Implement DetailService**

Create `app/services/detail_service.py`:

```python
from typing import Optional

from app.core.exceptions import InvalidPatentIdentifierError, OpenSearchQueryError, PatentNotFoundError
from app.mappings.detail_mapper import map_detail_response
from app.repositories.opensearch_repo import OpenSearchRepository


class DetailService:
    def __init__(self, repository: Optional[OpenSearchRepository] = None):
        self.repository = repository or OpenSearchRepository()

    def get_detail(self, patent_id: str, include_description: bool = False) -> dict:
        identifier = _clean_identifier(patent_id)
        try:
            hit = self.repository.get_patent_by_identifier(identifier)
        except Exception as exc:
            raise OpenSearchQueryError("OpenSearch 查询异常") from exc

        if hit is None:
            raise PatentNotFoundError("patent not found")

        return map_detail_response(hit, include_description=include_description)


def _clean_identifier(patent_id: str) -> str:
    identifier = (patent_id or "").strip()
    if not identifier:
        raise InvalidPatentIdentifierError("patent_id 参数非法")
    return identifier
```

- [ ] **Step 5: Implement CitationService**

Create `app/services/citation_service.py`:

```python
from typing import Optional

from app.core.exceptions import InvalidPatentIdentifierError, OpenSearchQueryError, PatentNotFoundError
from app.mappings.citation_mapper import map_citations_response
from app.repositories.opensearch_repo import OpenSearchRepository


class CitationService:
    def __init__(self, repository: Optional[OpenSearchRepository] = None):
        self.repository = repository or OpenSearchRepository()

    def get_citations(self, patent_id: str) -> dict:
        identifier = _clean_identifier(patent_id)
        try:
            hit = self.repository.get_patent_by_identifier(identifier)
        except Exception as exc:
            raise OpenSearchQueryError("OpenSearch 查询异常") from exc

        if hit is None:
            raise PatentNotFoundError("patent not found")

        return map_citations_response(hit)


def _clean_identifier(patent_id: str) -> str:
    identifier = (patent_id or "").strip()
    if not identifier:
        raise InvalidPatentIdentifierError("patent_id 参数非法")
    return identifier
```

- [ ] **Step 6: Run service and full tests**

```bash
.venv/bin/python -m pytest tests/test_detail_service.py tests/test_citation_service.py -q
.venv/bin/python -m pytest -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/core/exceptions.py app/services/detail_service.py app/services/citation_service.py tests/test_detail_service.py tests/test_citation_service.py
git commit -m "feat: add patent detail citation services"
```

---

### Task 5: Detail and Citations API Routes

**Files:**
- Modify: `app/api/detail.py`
- Modify: `app/api/citations.py`
- Create: `tests/test_detail_api.py`
- Create: `tests/test_citations_api.py`

- [ ] **Step 1: Write failing API tests**

Create `tests/test_detail_api.py`:

```python
from fastapi.testclient import TestClient

from app.api.detail import get_detail_service
from app.core.exceptions import OpenSearchQueryError, PatentNotFoundError
from app.main import app


class FakeDetailService:
    def __init__(self, result=None, error=None):
        self.result = result or {"patent_id": "cn-1", "title": "标题"}
        self.error = error
        self.calls = []

    def get_detail(self, patent_id, include_description=False):
        self.calls.append(
            {
                "patent_id": patent_id,
                "include_description": include_description,
            }
        )
        if self.error:
            raise self.error
        return self.result


def _client_with_service(service):
    app.dependency_overrides[get_detail_service] = lambda: service
    return TestClient(app)


def teardown_function():
    app.dependency_overrides.clear()


def test_detail_api_returns_detail_with_description_flag():
    service = FakeDetailService(result={"patent_id": "cn-1", "description": "说明书"})
    client = _client_with_service(service)

    response = client.get("/api/patent/detail/cn-1?include_description=true")

    assert response.status_code == 200
    assert response.json()["patent_id"] == "cn-1"
    assert response.json()["description"] == "说明书"
    assert service.calls == [{"patent_id": "cn-1", "include_description": True}]


def test_detail_api_returns_40401_when_not_found():
    client = _client_with_service(FakeDetailService(error=PatentNotFoundError("patent not found")))

    response = client.get("/api/patent/detail/missing")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == 40401
    assert response.json()["detail"]["message"] == "patent not found"


def test_detail_api_returns_50001_on_opensearch_error():
    client = _client_with_service(FakeDetailService(error=OpenSearchQueryError("OpenSearch 查询异常")))

    response = client.get("/api/patent/detail/cn-1")

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == 50001
```

Create `tests/test_citations_api.py`:

```python
from fastapi.testclient import TestClient

from app.api.citations import get_citation_service
from app.core.exceptions import OpenSearchQueryError, PatentNotFoundError
from app.main import app


class FakeCitationService:
    def __init__(self, result=None, error=None):
        self.result = result or {
            "patent_id": "cn-1",
            "cited_by": [],
            "patent_references": [],
            "non_patent_references": [],
        }
        self.error = error
        self.calls = []

    def get_citations(self, patent_id):
        self.calls.append(patent_id)
        if self.error:
            raise self.error
        return self.result


def _client_with_service(service):
    app.dependency_overrides[get_citation_service] = lambda: service
    return TestClient(app)


def teardown_function():
    app.dependency_overrides.clear()


def test_citations_api_returns_citations():
    service = FakeCitationService(result={"patent_id": "cn-1", "cited_by": ["x"]})
    client = _client_with_service(service)

    response = client.get("/api/patent/citations/cn-1")

    assert response.status_code == 200
    assert response.json()["patent_id"] == "cn-1"
    assert response.json()["cited_by"] == ["x"]
    assert service.calls == ["cn-1"]


def test_citations_api_returns_40401_when_not_found():
    client = _client_with_service(FakeCitationService(error=PatentNotFoundError("patent not found")))

    response = client.get("/api/patent/citations/missing")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == 40401


def test_citations_api_returns_50001_on_opensearch_error():
    client = _client_with_service(FakeCitationService(error=OpenSearchQueryError("OpenSearch 查询异常")))

    response = client.get("/api/patent/citations/cn-1")

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == 50001
```

- [ ] **Step 2: Run API tests and verify failure**

```bash
.venv/bin/python -m pytest tests/test_detail_api.py tests/test_citations_api.py -q
```

Expected: FAIL because route functions and dependency providers are not implemented.

- [ ] **Step 3: Implement detail API route**

Replace `app/api/detail.py` with:

```python
from fastapi import APIRouter, Depends

from app.core.exceptions import (
    InvalidPatentIdentifierError,
    OpenSearchQueryError,
    PatentNotFoundError,
    service_error,
)
from app.core.security import require_api_key
from app.services.detail_service import DetailService


router = APIRouter(prefix="/api/patent", tags=["patent-detail"])


def get_detail_service() -> DetailService:
    return DetailService()


@router.get("/detail/{patent_id}", dependencies=[Depends(require_api_key)])
def get_patent_detail(
    patent_id: str,
    include_description: bool = False,
    service: DetailService = Depends(get_detail_service),
):
    try:
        return service.get_detail(patent_id, include_description=include_description)
    except InvalidPatentIdentifierError as exc:
        raise service_error(400, 40002, str(exc))
    except PatentNotFoundError as exc:
        raise service_error(404, 40401, str(exc))
    except OpenSearchQueryError as exc:
        raise service_error(502, 50001, str(exc))
```

- [ ] **Step 4: Implement citations API route**

Replace `app/api/citations.py` with:

```python
from fastapi import APIRouter, Depends

from app.core.exceptions import (
    InvalidPatentIdentifierError,
    OpenSearchQueryError,
    PatentNotFoundError,
    service_error,
)
from app.core.security import require_api_key
from app.services.citation_service import CitationService


router = APIRouter(prefix="/api/patent", tags=["patent-citations"])


def get_citation_service() -> CitationService:
    return CitationService()


@router.get("/citations/{patent_id}", dependencies=[Depends(require_api_key)])
def get_patent_citations(
    patent_id: str,
    service: CitationService = Depends(get_citation_service),
):
    try:
        return service.get_citations(patent_id)
    except InvalidPatentIdentifierError as exc:
        raise service_error(400, 40002, str(exc))
    except PatentNotFoundError as exc:
        raise service_error(404, 40401, str(exc))
    except OpenSearchQueryError as exc:
        raise service_error(502, 50001, str(exc))
```

- [ ] **Step 5: Run API and full tests**

```bash
.venv/bin/python -m pytest tests/test_detail_api.py tests/test_citations_api.py tests/test_router_imports.py -q
.venv/bin/python -m pytest -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/api/detail.py app/api/citations.py tests/test_detail_api.py tests/test_citations_api.py
git commit -m "feat: expose patent detail citation APIs"
```

---

### Task 6: Documentation and Manual Acceptance Materials

**Files:**
- Modify: `docs/api_spec.md`
- Modify: `docs/field_mapping.md`
- Create: `docs/stage7_dev_assignment.md`
- Create: `docs/stage7_test_acceptance.md`

- [ ] **Step 1: Update API spec**

In `docs/api_spec.md`, update section 4 and section 5 to include the dual-compatible fields from:

```text
docs/superpowers/specs/2026-07-06-stage-7-detail-citations-design.md
```

Required additions:

```text
detail returns camelCase and snake_case aliases.
citations returns cited_by, patent_references, non_patent_references.
citations also returns referencesCited, referencesCitedRaw, referencesCitedText, relatedDocuments.
```

- [ ] **Step 2: Update field mapping**

In `docs/field_mapping.md`, add a stage 7 subsection documenting:

```text
applicationNumber / application_number -> ApplicationNumber
documentNumber / document_number -> PublicationNumber
legalStatus / legal_status -> LatestLegalStatus then LegalStatus
currentAssignee / current_assignee -> Assignee then Applicant
claims -> Requirement
description -> Instructions
cited_by -> RelatedDocuments normalized
patent_references -> ReferencesCited normalized
non_patent_references -> ReferencesCitedRaw / ReferencesCitedText
```

- [ ] **Step 3: Create development assignment**

Create `docs/stage7_dev_assignment.md`:

````markdown
# 阶段 7 开发派工单

## 目标

补齐专利详情与引证/相关文献接口，并同时兼容当前 HTTP API 字段风格与 SaaS PatentHub 工具层字段契约。

## 开发范围

1. `GET /api/patent/detail/{patent_id}`
2. `GET /api/patent/detail/{patent_id}?include_description=true`
3. `GET /api/patent/citations/{patent_id}`
4. OpenSearch 按稳定标识查询单篇专利。
5. 详情响应同时返回 camelCase 和 snake_case 关键字段。
6. 引证响应同时返回 SaaS 工具字段和原始兼容字段。

## 参考文档

- `docs/superpowers/specs/2026-07-06-stage-7-detail-citations-design.md`
- `docs/superpowers/plans/2026-07-06-stage-7-detail-citations.md`
- `docs/saas_patent_contract_audit.md`

## 阶段边界

阶段 7 不做：

1. 不修改 SaaS 副本源码。
2. 不做 SaaS 联调。
3. 不实现企业专利画像。
4. 不实现独立法律历史接口。
5. 不复制 PatentHub 60 分钟 session-bound ID 机制。
6. 不修改 OpenSearch mapping。

## 提交流程

开发人员必须按实施计划逐任务提交，每次提交前运行该任务相关测试。最终提交前运行：

```bash
.venv/bin/python -m pytest -q
```
````

- [ ] **Step 4: Create test acceptance doc**

Create `docs/stage7_test_acceptance.md`:

````markdown
# 阶段 7 测试验收单

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

## API 验收

| Case | Request | Expected |
|---|---|---|
| detail without description | `GET /api/patent/detail/{patent_id}` | HTTP 200, no `description` field |
| detail with description | `GET /api/patent/detail/{patent_id}?include_description=true` | HTTP 200, includes `description` |
| citations | `GET /api/patent/citations/{patent_id}` | HTTP 200, includes `cited_by`, `patent_references`, `non_patent_references` |
| missing patent | `GET /api/patent/detail/not-found-id` | HTTP 404, code `40401` |
| auth missing | no `X-API-Key` when auth enabled | HTTP 401, code `40101` |

## 字段验收

详情接口必须同时包含：

```text
applicationNumber
application_number
documentNumber
document_number
legalStatus
legal_status
currentAssignee
current_assignee
mainIpc
main_ipc
claims
```

引证接口必须同时包含：

```text
cited_by
patent_references
non_patent_references
referencesCited
referencesCitedRaw
referencesCitedText
relatedDocuments
```

## 通过标准

1. 自动化测试全部通过。
2. 真实 OpenSearch smoke 通过。
3. detail/citations 成功响应为业务对象直出。
4. 错误响应符合 `{success, code, message, data}`。
5. 未修改 SaaS 副本源码。
6. 未修改 OpenSearch mapping。
````

- [ ] **Step 5: Run doc checks**

```bash
rg -n "stage 7|阶段 7|detail|citations|cited_by|patent_references|application_number|legal_status" docs/api_spec.md docs/field_mapping.md docs/stage7_dev_assignment.md docs/stage7_test_acceptance.md
git diff --check
```

Expected: command exits 0 and all expected terms are present.

- [ ] **Step 6: Commit**

```bash
git add docs/api_spec.md docs/field_mapping.md docs/stage7_dev_assignment.md docs/stage7_test_acceptance.md
git commit -m "docs: add stage 7 detail citation docs"
```

---

### Task 7: Live Smoke and Test Report

**Files:**
- Create: `scripts/smoke_detail_citations.py`
- Create after testing: `docs/stage7_test_report.md`

- [ ] **Step 1: Create smoke script**

Create `scripts/smoke_detail_citations.py`:

```python
import json
import sys
import urllib.error
import urllib.request


def _request(url: str, token: str | None = None) -> tuple[int, dict]:
    headers = {}
    if token:
        headers["X-API-Key"] = token
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body)


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: smoke_detail_citations.py BASE_URL PATENT_ID [API_TOKEN]")
        return 2

    base_url = sys.argv[1].rstrip("/")
    patent_id = sys.argv[2]
    token = sys.argv[3] if len(sys.argv) > 3 else None

    checks = [
        ("detail", f"{base_url}/api/patent/detail/{patent_id}"),
        ("detail_description", f"{base_url}/api/patent/detail/{patent_id}?include_description=true"),
        ("citations", f"{base_url}/api/patent/citations/{patent_id}"),
    ]

    failed = False
    for name, url in checks:
        status, data = _request(url, token)
        keys = sorted(data.keys()) if isinstance(data, dict) else []
        print(json.dumps({"name": name, "status": status, "keys": keys}, ensure_ascii=False))
        if status != 200:
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run all tests**

```bash
.venv/bin/python -m pytest -q
```

Expected: PASS.

- [ ] **Step 3: Run local server**

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- [ ] **Step 4: Obtain a real patent ID**

Use an existing successful search response or run:

```bash
curl -s -X POST http://127.0.0.1:8000/api/patent/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_TOKEN" \
  -d '{"q":"阀门","page":1,"page_size":1}' | python3 -c "import sys,json; print(json.load(sys.stdin)['records'][0]['patent_id'])"
```

Expected: prints a non-empty patent ID.

- [ ] **Step 5: Run smoke script**

```bash
.venv/bin/python scripts/smoke_detail_citations.py http://127.0.0.1:8000 "$PATENT_ID" "$API_TOKEN"
```

Expected: all rows return status `200`.

- [ ] **Step 6: Write test report with real outputs**

Create `docs/stage7_test_report.md` only after commands are run. The report must contain:

1. The exact pytest command and its real pass/fail output line.
2. The real `patent_id` used for smoke testing.
3. The exact smoke command.
4. The three JSON rows printed by `scripts/smoke_detail_citations.py`.
5. Explicit pass/fail conclusions for detail, detail with description, and citations.
6. Confirmation that SaaS source copy was not modified.
7. Confirmation that OpenSearch mapping was not modified.
8. A clear recommendation on whether to enter stage 8.

Do not commit a blank template-only report.

- [ ] **Step 7: Commit**

```bash
git add scripts/smoke_detail_citations.py docs/stage7_test_report.md
git commit -m "test: add stage 7 smoke evidence"
```

---

## Review Checklist

- `patent_harness_base_副本/` remains ignored and unmodified.
- `GET /api/patent/detail/{patent_id}` is implemented.
- `GET /api/patent/detail/{patent_id}?include_description=true` returns `description`.
- Default detail call does not include `description`.
- `GET /api/patent/citations/{patent_id}` is implemented.
- Detail response includes both camelCase and snake_case aliases.
- Citations response includes `cited_by`, `patent_references`, `non_patent_references`.
- Citations response also includes `referencesCited`, `referencesCitedRaw`, `referencesCitedText`, `relatedDocuments`.
- Missing patent returns HTTP 404 and code `40401`.
- OpenSearch error returns HTTP 502 and code `50001`.
- Existing search tests still pass.
- Full pytest suite passes.
