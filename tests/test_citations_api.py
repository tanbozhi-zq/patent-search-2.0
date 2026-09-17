"""验证引证 HTTP 端点的成功响应、未命中和依赖错误映射。"""

from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.api.citations import get_citation_service
from app.core.exceptions import OpenSearchQueryError, PatentNotFoundError
from app.core.security import require_api_key
from app.main import app


class FakeCitationService:
    def __init__(self, result=None, error=None):
        self.result = result or {
            "patent_id": "cn-1",
            "cited_by": [],
            "patent_references": [],
            "non_patent_references": [],
            "referencesCited": [],
            "referencesCitedRaw": "",
            "referencesCitedText": "",
            "relatedDocuments": [],
        }
        self.error = error
        self.calls = []

    def get_citations(self, patent_id):
        self.calls.append(patent_id)
        if self.error:
            raise self.error
        return self.result


@contextmanager
def _client_with_service(service):
    app.dependency_overrides[get_citation_service] = lambda: service
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def test_citations_api_returns_citations():
    citation = {
        "id": "x",
        "title": "引用专利",
        "applicant": "申请人",
        "application_date": "2024-01-01",
        "application_number": "CN202400000001",
        "type": "发明",
        "legal_status": "有效",
        "main_ipc": "H01M",
    }
    service = FakeCitationService(
        result={
            "patent_id": "cn-1",
            "cited_by": [citation],
            "patent_references": [],
            "non_patent_references": [],
            "referencesCited": [],
            "referencesCitedRaw": "",
            "referencesCitedText": "",
            "relatedDocuments": [],
        }
    )
    with _client_with_service(service) as client:
        response = client.get("/api/patent/citations/cn-1")

    assert response.status_code == 200
    assert response.json()["patent_id"] == "cn-1"
    assert response.json()["cited_by"] == [citation]
    assert service.calls == ["cn-1"]


def test_citations_api_returns_40401_when_not_found():
    with _client_with_service(
        FakeCitationService(error=PatentNotFoundError("patent not found"))
    ) as client:
        response = client.get("/api/patent/citations/missing")

    assert response.status_code == 404
    assert response.json()["code"] == 40401


def test_citations_api_returns_50001_on_opensearch_error():
    with _client_with_service(
        FakeCitationService(error=OpenSearchQueryError("OpenSearch 查询异常"))
    ) as client:
        response = client.get("/api/patent/citations/cn-1")

    assert response.status_code == 502
    assert response.json()["code"] == 50001
