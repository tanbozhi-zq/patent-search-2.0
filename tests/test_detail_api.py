from fastapi.testclient import TestClient

from app.api.detail import get_detail_service
from app.core.exceptions import OpenSearchQueryError, PatentNotFoundError
from app.core.security import require_api_key
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
    app.dependency_overrides[require_api_key] = lambda: None
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