"""验证应用生命周期复用仓储、关闭资源并把请求截止时间传入同步依赖。"""

import importlib

import pytest
from fastapi.testclient import TestClient
from urllib3.util import Timeout

from app.api.search import get_search_service
from app.core.config import Settings
from app.core.exceptions import (
    SearchDependencyTimeoutError,
    SearchDependencyUnavailableError,
)
from app.core.security import require_api_key
from app.repositories.opensearch_repo import OpenSearchRepository


main_module = importlib.import_module("app.main")
app = main_module.app


class LifecycleRepository:
    instances = []

    def __init__(self, settings=None):
        self.search_calls = 0
        self.close_calls = 0
        self.__class__.instances.append(self)

    def search(self, body):
        self.search_calls += 1
        return {"hits": {"total": {"value": 0}, "hits": []}}

    def close(self):
        self.close_calls += 1


def test_lifespan_reuses_one_repository_and_closes_it_once(monkeypatch):
    LifecycleRepository.instances = []
    monkeypatch.setattr(main_module, "OpenSearchRepository", LifecycleRepository)
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        with TestClient(app) as client:
            first = client.post("/api/patent/search", json={"q": "阀门"})
            repository = app.state.opensearch_repository
            bulkhead = app.state.search_request_bulkhead
            heavy_bulkhead = app.state.heavy_search_request_bulkhead
            second = client.post("/api/patent/search", json={"q": "泵"})

            assert first.status_code == 200
            assert second.status_code == 200
            assert repository is LifecycleRepository.instances[0]
            assert repository.search_calls == 2
            assert repository.close_calls == 0
            assert bulkhead.capacity == main_module.settings.patent_search_bulkhead_capacity
            assert (
                heavy_bulkhead.capacity
                == main_module.settings.patent_search_heavy_bulkhead_capacity
            )
            assert bulkhead.in_flight == 0
            assert heavy_bulkhead.in_flight == 0
    finally:
        app.dependency_overrides.clear()

    assert len(LifecycleRepository.instances) == 1
    assert LifecycleRepository.instances[0].close_calls == 1
    assert not hasattr(app.state, "opensearch_repository")
    assert not hasattr(app.state, "search_request_bulkhead")
    assert not hasattr(app.state, "heavy_search_request_bulkhead")


class RecordingClient:
    def __init__(self):
        self.calls = []
        self.close_calls = 0

    def search(self, index, body, params=None):
        self.calls.append({"index": index, "body": body, "params": params})
        return {"hits": {"total": {"value": 0}, "hits": []}}

    def close(self):
        self.close_calls += 1


def test_http_middleware_propagates_one_deadline_into_sync_repository(monkeypatch):
    client = RecordingClient()

    def repository_factory(settings):
        return OpenSearchRepository(
            settings=Settings(
                _env_file=None,
                opensearch_timeout_seconds=30,
                patent_search_deadline_seconds=10,
            ),
            client=client,
        )

    monkeypatch.setattr(main_module.settings, "patent_search_deadline_seconds", 2.0)
    monkeypatch.setattr(main_module, "OpenSearchRepository", repository_factory)
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        with TestClient(app) as http_client:
            response = http_client.post("/api/patent/search", json={"q": "阀门"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert len(client.calls) == 1
    request_timeout = client.calls[0]["params"]["request_timeout"]
    assert isinstance(request_timeout, Timeout)
    assert 0 < request_timeout.total <= 2.0
    assert client.close_calls == 1


class FailingSearchService:
    def __init__(self, error):
        self.error = error

    def search(self, request):
        raise self.error


@pytest.mark.parametrize(
    ("error", "status_code", "code", "retry_after"),
    [
        (SearchDependencyUnavailableError("node-secret"), 503, 50302, "5"),
        (SearchDependencyTimeoutError("node-secret"), 504, 50401, None),
    ],
)
def test_api_exposes_stable_transient_dependency_errors(error, status_code, code, retry_after):
    app.dependency_overrides[get_search_service] = lambda: FailingSearchService(error)
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/api/patent/search", json={"q": "阀门"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == status_code
    assert response.json()["code"] == code
    assert response.json()["retryable"] is True
    assert "node-secret" not in response.text
    assert response.headers.get("Retry-After") == retry_after


def test_unknown_programming_failure_reaches_global_50002():
    app.dependency_overrides[get_search_service] = lambda: FailingSearchService(
        RuntimeError("program-secret")
    )
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/api/patent/search", json={"q": "阀门"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert response.json()["code"] == 50002
    assert response.json()["retryable"] is False
    assert "program-secret" not in response.text
