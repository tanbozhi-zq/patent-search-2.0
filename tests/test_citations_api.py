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
    app.dependency_overrides[require_api_key] = lambda: None
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