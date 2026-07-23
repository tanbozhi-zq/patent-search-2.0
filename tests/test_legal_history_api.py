from fastapi.testclient import TestClient

from app.api.legal_history import get_legal_history_service
from app.core.exceptions import OpenSearchQueryError, PatentNotFoundError
from app.core.security import require_api_key
from app.main import app


class FakeLegalHistoryService:
    def __init__(self, result=None, error=None):
        self.result = result or {"patent_id": "cn-1", "transaction_count": 0, "transactions": []}
        self.error = error
        self.calls = []

    def get_legal_history(self, patent_id):
        self.calls.append(patent_id)
        if self.error:
            raise self.error
        return self.result


def _client_with_service(service):
    app.dependency_overrides[get_legal_history_service] = lambda: service
    app.dependency_overrides[require_api_key] = lambda: None
    return TestClient(app)


def teardown_function():
    app.dependency_overrides.clear()


def test_legal_history_api_returns_base_response():
    service = FakeLegalHistoryService()
    client = _client_with_service(service)

    response = client.get("/api/patent/legal-history/cn-1")

    assert response.status_code == 200
    assert response.json() == {"patent_id": "cn-1", "transaction_count": 0, "transactions": []}
    assert service.calls == ["cn-1"]


def test_legal_history_api_returns_40401_when_not_found():
    client = _client_with_service(FakeLegalHistoryService(error=PatentNotFoundError("patent not found")))

    response = client.get("/api/patent/legal-history/missing")

    assert response.status_code == 404
    assert response.json()["code"] == 40401


def test_legal_history_api_returns_50001_on_opensearch_error():
    client = _client_with_service(FakeLegalHistoryService(error=OpenSearchQueryError("OpenSearch 查询异常")))

    response = client.get("/api/patent/legal-history/cn-1")

    assert response.status_code == 502
    assert response.json()["code"] == 50001
