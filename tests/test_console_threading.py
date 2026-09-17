"""验证同步控制台工作在线程中执行而不阻塞事件循环和健康检查。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic

import pytest
from fastapi.testclient import TestClient

from app.api.console import (
    get_citation_service,
    get_detail_service,
    get_legal_history_service,
    get_search_service,
)
from app.core.security import require_console_access
from app.main import app


class BlockingConsoleService:
    def __init__(self, method_name, result):
        self.method_name = method_name
        self.result = result
        self.entered = Event()
        self.release = Event()

    def __getattr__(self, name):
        if name != self.method_name:
            raise AttributeError(name)

        def call(*_args, **_kwargs):
            self.entered.set()
            if not self.release.wait(timeout=2):
                raise TimeoutError("test did not release the blocking Console service")
            return self.result

        return call


CONSOLE_THREAD_CASES = [
    pytest.param(
        get_search_service,
        "search",
        "POST",
        "/console-api/search",
        {"q": "慢查询"},
        {
            "total": 0,
            "page": 1,
            "page_size": 50,
            "total_pages": 0,
            "accessible_pages": 0,
            "next_page": None,
            "took_ms": None,
            "records": [],
        },
        id="search",
    ),
    pytest.param(
        get_search_service,
        "target_rank",
        "POST",
        "/console-api/test/target-rank",
        {"q": "慢查询", "target_identifier": "CN100B"},
        {
            "status": "target_not_found",
            "in_results": False,
            "rank": None,
            "tied_count": 0,
            "sort_value": None,
            "target": None,
        },
        id="target-rank",
    ),
    pytest.param(
        get_detail_service,
        "get_detail",
        "GET",
        "/console-api/detail/patent-1",
        None,
        {"id": "patent-1"},
        id="detail",
    ),
    pytest.param(
        get_citation_service,
        "get_citations",
        "GET",
        "/console-api/citations/patent-1",
        None,
        {
            "patent_id": "patent-1",
            "cited_by": [],
            "patent_references": [],
            "non_patent_references": [],
            "referencesCited": [],
            "referencesCitedRaw": "",
            "referencesCitedText": "",
            "relatedDocuments": [],
        },
        id="citations",
    ),
    pytest.param(
        get_legal_history_service,
        "get_legal_history",
        "GET",
        "/console-api/legal-history/patent-1",
        None,
        {"patent_id": "patent-1", "transaction_count": 0, "transactions": []},
        id="legal-history",
    ),
]


@pytest.mark.parametrize(
    ("dependency", "method_name", "http_method", "path", "payload", "result"),
    CONSOLE_THREAD_CASES,
)
def test_every_console_service_call_runs_off_the_event_loop(
    dependency,
    method_name,
    http_method,
    path,
    payload,
    result,
):
    service = BlockingConsoleService(method_name, result)
    app.dependency_overrides[dependency] = lambda: service
    app.dependency_overrides[require_console_access] = lambda: None
    try:
        with TestClient(app) as client:
            with ThreadPoolExecutor(max_workers=2) as executor:
                request = executor.submit(client.request, http_method, path, json=payload)
                assert service.entered.wait(timeout=1)

                started = monotonic()
                health = client.get("/health")
                health_seconds = monotonic() - started

                assert health.status_code == 200
                assert health_seconds < 0.5
                assert not request.done()

                service.release.set()
                assert request.result(timeout=1).status_code == 200
    finally:
        service.release.set()
        app.dependency_overrides.clear()


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
def test_console_semantic_modes_pass_the_shared_search_request_unchanged(mode):
    class RecordingSearchService:
        def __init__(self):
            self.payload = None

        def search(self, request):
            self.payload = request.model_dump()
            return {
                "total": 0,
                "page": request.page,
                "page_size": request.page_size,
                "total_pages": 0,
                "accessible_pages": 0,
                "next_page": None,
                "took_ms": None,
                "records": [],
                "search_context": {
                    "mode": mode,
                    "vector_fields": request.vector_fields,
                    "top_k": request.top_k,
                    "ranking_profile": (
                        "patent-knn-cosine-v1"
                        if mode == "vector"
                        else "patent-hybrid-rrf-v1-1"
                    ),
                    "sort": request.sort,
                },
            }

    service = RecordingSearchService()
    payload = {
        "mode": mode,
        "semantic_text": "流体控制阀",
        "vector_fields": ["abstract"],
        "top_k": 40,
        "ds": "cn",
        "sort": "!documentDate",
        "page": 2,
        "page_size": 10,
        "highlight": 0,
    }
    if mode == "hybrid":
        payload["q"] = "ipc:F16K"

    app.dependency_overrides[get_search_service] = lambda: service
    app.dependency_overrides[require_console_access] = lambda: None
    try:
        with TestClient(app) as client:
            response = client.post("/console-api/search", json=payload)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert service.payload == payload
    assert response.json()["search_context"]["mode"] == mode
