"""验证详情 HTTP 端点的说明书开关、稀疏字段和错误响应契约。"""

from contextlib import contextmanager

from fastapi.testclient import TestClient

from app.api.detail import get_detail_service
from app.core.exceptions import OpenSearchQueryError, PatentNotFoundError
from app.core.security import require_api_key
from app.main import app


class FakeDetailService:
    def __init__(self, result=None, error=None):
        self.result = result or {"id": "cn-1", "title": "标题"}
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


@contextmanager
def _client_with_service(service):
    app.dependency_overrides[get_detail_service] = lambda: service
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def test_detail_api_returns_detail_with_description_flag():
    service = FakeDetailService(result={"id": "cn-1", "description": "说明书"})
    with _client_with_service(service) as client:
        response = client.get("/api/patent/detail/cn-1?include_description=true")

    assert response.status_code == 200
    assert response.json()["id"] == "cn-1"
    assert response.json()["description"] == "说明书"
    assert service.calls == [{"patent_id": "cn-1", "include_description": True}]


def test_detail_api_does_not_add_absent_optional_fields():
    result = {"id": "cn-1", "title": "标题"}
    with _client_with_service(FakeDetailService(result=result)) as client:
        response = client.get("/api/patent/detail/cn-1")

    assert response.status_code == 200
    assert response.json() == result


def test_detail_api_returns_40401_when_not_found():
    with _client_with_service(
        FakeDetailService(error=PatentNotFoundError("patent not found"))
    ) as client:
        response = client.get("/api/patent/detail/missing")

    assert response.status_code == 404
    assert response.json()["code"] == 40401
    assert response.json()["message"] == "专利不存在"


def test_detail_api_returns_50001_on_opensearch_error():
    with _client_with_service(
        FakeDetailService(error=OpenSearchQueryError("OpenSearch 查询异常"))
    ) as client:
        response = client.get("/api/patent/detail/cn-1")

    assert response.status_code == 502
    assert response.json()["code"] == 50001
