"""验证控制台专利接口将领域和依赖失败统一为安全 JSON 错误契约。"""

import pytest
from fastapi.testclient import TestClient

from app.api.console import (
    get_citation_service,
    get_detail_service,
    get_legal_history_service,
    get_search_service,
)
from app.core.exceptions import (
    InvalidPatentIdentifierError,
    OpenSearchQueryError,
    PatentNotFoundError,
    SearchDependencyTimeoutError,
    SearchDependencyUnavailableError,
)
from app.core.security import require_console_access
from app.main import app


class FailingDetailService:
    def __init__(self, error):
        self.error = error

    def get_detail(self, patent_id, include_description=False):
        raise self.error


class FailingCitationService:
    def __init__(self, error):
        self.error = error

    def get_citations(self, patent_id):
        raise self.error


class FailingLegalHistoryService:
    def __init__(self, error):
        self.error = error

    def get_legal_history(self, patent_id):
        raise self.error


class FailingSearchService:
    def __init__(self, error):
        self.error = error

    def search(self, _request):
        raise self.error


@pytest.mark.parametrize(
    ("dependency", "service", "path"),
    [
        (
            get_detail_service,
            FailingDetailService(PatentNotFoundError("patent not found")),
            "/console-api/detail/CN-NOT-FOUND",
        ),
        (
            get_citation_service,
            FailingCitationService(PatentNotFoundError("patent not found")),
            "/console-api/citations/CN-NOT-FOUND",
        ),
        (
            get_legal_history_service,
            FailingLegalHistoryService(PatentNotFoundError("patent not found")),
            "/console-api/legal-history/CN-NOT-FOUND",
        ),
    ],
)
def test_console_patent_routes_map_not_found_to_40401(dependency, service, path):
    app.dependency_overrides[dependency] = lambda: service
    app.dependency_overrides[require_console_access] = lambda: None
    try:
        with TestClient(app) as client:
            response = client.get(path)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["code"] == 40401


@pytest.mark.parametrize(
    ("dependency", "service", "path"),
    [
        (
            get_detail_service,
            FailingDetailService(InvalidPatentIdentifierError("patent_id 参数非法")),
            "/console-api/detail/CN-INVALID",
        ),
        (
            get_citation_service,
            FailingCitationService(InvalidPatentIdentifierError("patent_id 参数非法")),
            "/console-api/citations/CN-INVALID",
        ),
        (
            get_legal_history_service,
            FailingLegalHistoryService(InvalidPatentIdentifierError("patent_id 参数非法")),
            "/console-api/legal-history/CN-INVALID",
        ),
    ],
)
def test_console_patent_routes_map_invalid_identifier_to_40002(dependency, service, path):
    app.dependency_overrides[dependency] = lambda: service
    app.dependency_overrides[require_console_access] = lambda: None
    try:
        with TestClient(app) as client:
            response = client.get(path)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["code"] == 40002


@pytest.mark.parametrize(
    ("error", "status_code", "error_code"),
    [
        (OpenSearchQueryError("node-secret"), 502, 50001),
        (SearchDependencyUnavailableError("node-secret"), 503, 50302),
        (SearchDependencyTimeoutError("node-secret"), 504, 50401),
        (RuntimeError("node-secret"), 500, 50002),
    ],
)
def test_console_dependency_failures_return_json_without_internal_details(
    error,
    status_code,
    error_code,
):
    app.dependency_overrides[get_search_service] = lambda: FailingSearchService(error)
    app.dependency_overrides[require_console_access] = lambda: None
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/console-api/search", json={"q": "阀门"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == status_code
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["code"] == error_code
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert "node-secret" not in response.text
    assert "<html" not in response.text.lower()
