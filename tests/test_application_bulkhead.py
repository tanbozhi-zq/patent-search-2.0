"""验证应用舱壁的容量、取消释放、重轻请求隔离与健康端点可用性。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import logging
from threading import Event, Lock
from time import monotonic

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import (
    acquire_heavy_search_request_slot,
    acquire_search_request_slot,
    get_detail_service,
    get_search_service,
)
from app.core.bulkhead import ApplicationBulkhead
from app.core.exceptions import (
    ErrorCode,
    OpenSearchQueryError,
    SearchDependencyTimeoutError,
    ServiceError,
)
from app.core.request_context import bind_request_id, reset_request_id
from app.core.security import require_api_key, require_console_access
from app.main import app
from app.services.search_service import SearchService


EMPTY_SEARCH_RESPONSE = {
    "total": 0,
    "page": 1,
    "page_size": 50,
    "total_pages": 0,
    "accessible_pages": 0,
    "next_page": None,
    "took_ms": None,
    "records": [],
}

HEAVY_OPENSEARCH_PATHS = {
    "/api/patent/search",
    "/console-api/search",
    "/console-api/test/target-rank",
}
LIGHT_OPENSEARCH_PATHS = {
    "/api/patent/detail/{patent_id}",
    "/api/patent/citations/{patent_id}",
    "/api/patent/legal-history/{patent_id}",
    "/console-api/detail/{patent_id}",
    "/console-api/citations/{patent_id}",
    "/console-api/legal-history/{patent_id}",
}


def test_routes_use_their_workload_bulkhead_but_health_does_not():
    routes = {
        route.path: route
        for route in app.routes
        if isinstance(route, APIRoute)
    }

    assert HEAVY_OPENSEARCH_PATHS | LIGHT_OPENSEARCH_PATHS <= routes.keys()
    for path in HEAVY_OPENSEARCH_PATHS:
        dependency_calls = {
            dependency.call for dependency in routes[path].dependant.dependencies
        }
        assert acquire_heavy_search_request_slot in dependency_calls
        assert acquire_search_request_slot not in dependency_calls

    for path in LIGHT_OPENSEARCH_PATHS:
        dependency_calls = {
            dependency.call for dependency in routes[path].dependant.dependencies
        }
        assert acquire_search_request_slot in dependency_calls
        assert acquire_heavy_search_request_slot not in dependency_calls

    health_dependencies = {
        dependency.call for dependency in routes["/health"].dependant.dependencies
    }
    assert acquire_search_request_slot not in health_dependencies
    assert acquire_heavy_search_request_slot not in health_dependencies


def test_bulkhead_rejects_over_capacity_quickly_and_recovers(caplog):
    async def scenario():
        bulkhead = ApplicationBulkhead(capacity=2, acquire_timeout_seconds=0.01)
        release = asyncio.Event()
        both_admitted = asyncio.Event()

        async def holder():
            async with bulkhead.slot():
                if bulkhead.in_flight == 2:
                    both_admitted.set()
                await release.wait()

        holders = [asyncio.create_task(holder()) for _ in range(2)]
        await asyncio.wait_for(both_admitted.wait(), timeout=1)

        started = monotonic()
        with pytest.raises(ServiceError) as rejected:
            async with bulkhead.slot():
                pytest.fail("over-capacity request must not be admitted")
        rejection_seconds = monotonic() - started

        assert rejected.value.code is ErrorCode.SERVICE_BUSY
        assert rejection_seconds < 0.2
        assert bulkhead.in_flight == 2
        assert bulkhead.peak_in_flight == 2
        assert bulkhead.rejected_count == 1

        release.set()
        await asyncio.gather(*holders)
        assert bulkhead.in_flight == 0

        async with bulkhead.slot():
            assert bulkhead.in_flight == 1
        assert bulkhead.in_flight == 0

    caplog.set_level(logging.INFO, logger="app.core.bulkhead")
    token = bind_request_id("bulkhead-request-123")
    try:
        asyncio.run(scenario())
    finally:
        reset_request_id(token)

    assert "event=in_flight" in caplog.text
    assert "event=rejected" in caplog.text
    assert "peak_in_flight=2" in caplog.text
    assert "rejected_total=1" in caplog.text
    assert "acquire_timeout_seconds=0.01" in caplog.text
    rejected_record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "rejected"
    )
    assert rejected_record.request_id == "bulkhead-request-123"
    assert rejected_record.structured_name == "global"
    assert rejected_record.in_flight == 2
    assert rejected_record.capacity == 2
    assert rejected_record.rejected_total == 1


def test_bulkhead_releases_after_exception_and_cancellation():
    async def scenario():
        bulkhead = ApplicationBulkhead(capacity=1, acquire_timeout_seconds=0.01)

        with pytest.raises(RuntimeError, match="boom"):
            async with bulkhead.slot():
                raise RuntimeError("boom")
        assert bulkhead.in_flight == 0

        entered = asyncio.Event()

        async def cancellable_request():
            async with bulkhead.slot():
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(cancellable_request())
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert bulkhead.in_flight == 0
        async with bulkhead.slot():
            assert bulkhead.in_flight == 1

    asyncio.run(scenario())


def test_cancelled_http_request_releases_the_dependency_slot():
    async def scenario():
        test_app = FastAPI()
        bulkhead = ApplicationBulkhead(capacity=1, acquire_timeout_seconds=0.01)
        test_app.state.search_request_bulkhead = bulkhead
        entered = asyncio.Event()

        @test_app.get(
            "/blocked",
            dependencies=[Depends(acquire_search_request_slot)],
        )
        async def blocked_request():
            entered.set()
            await asyncio.Event().wait()

        transport = ASGITransport(app=test_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            request_task = asyncio.create_task(client.get("/blocked"))
            await asyncio.wait_for(entered.wait(), timeout=1)
            assert bulkhead.in_flight == 1

            request_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request_task

        assert bulkhead.in_flight == 0
        async with bulkhead.slot():
            assert bulkhead.in_flight == 1

    asyncio.run(scenario())


class BlockingSearchService:
    def __init__(self, expected_active: int):
        self.expected_active = expected_active
        self.release = Event()
        self.all_active = Event()
        self._lock = Lock()
        self.calls = 0
        self.active = 0
        self.max_active = 0

    def search(self, request):
        with self._lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active == self.expected_active:
                self.all_active.set()
        try:
            if not self.release.wait(timeout=5):
                raise RuntimeError("test release timed out")
            return {**EMPTY_SEARCH_RESPONSE, "page": request.page, "page_size": request.page_size}
        finally:
            with self._lock:
                self.active -= 1


def test_heavy_overload_fast_rejects_without_entering_service_and_keeps_health_live():
    service = BlockingSearchService(expected_active=3)
    app.dependency_overrides[get_search_service] = lambda: service
    app.dependency_overrides[require_api_key] = lambda: None
    app.dependency_overrides[require_console_access] = lambda: None
    try:
        with TestClient(app) as client:
            app.state.search_request_bulkhead = ApplicationBulkhead(
                capacity=4,
                acquire_timeout_seconds=0.01,
            )
            app.state.heavy_search_request_bulkhead = ApplicationBulkhead(
                capacity=3,
                acquire_timeout_seconds=0.01,
                name="heavy_search",
            )
            with ThreadPoolExecutor(max_workers=12) as executor:
                admitted = [
                    executor.submit(client.post, "/api/patent/search", json={"q": "阀门"})
                    for _ in range(3)
                ]
                assert service.all_active.wait(timeout=2)

                health_started = monotonic()
                health = client.get("/health")
                assert health.status_code == 200
                assert monotonic() - health_started < 0.5

                overload_started = monotonic()
                rejected = [
                    executor.submit(client.post, "/console-api/search", json={"q": "阀门"})
                    for _ in range(8)
                ]
                rejected_responses = [future.result(timeout=1) for future in rejected]
                overload_seconds = monotonic() - overload_started

                assert overload_seconds < 0.5
                assert {response.status_code for response in rejected_responses} == {503}
                assert {response.json()["code"] for response in rejected_responses} == {50301}
                assert {response.json()["retryable"] for response in rejected_responses} == {True}
                assert {response.headers["Retry-After"] for response in rejected_responses} == {"1"}
                assert all(
                    response.json()["request_id"] == response.headers["X-Request-ID"]
                    for response in rejected_responses
                )
                assert service.calls == 3
                assert service.max_active == 3

                service.release.set()
                assert {future.result(timeout=2).status_code for future in admitted} == {200}

            assert app.state.search_request_bulkhead.in_flight == 0
            recovered = client.post("/api/patent/search", json={"q": "泵"})
            assert recovered.status_code == 200
            assert service.calls == 4
    finally:
        service.release.set()
        app.dependency_overrides.clear()


def test_admitted_console_search_runs_off_event_loop_and_keeps_health_live():
    service = BlockingSearchService(expected_active=1)
    app.dependency_overrides[get_search_service] = lambda: service
    app.dependency_overrides[require_console_access] = lambda: None
    try:
        with TestClient(app) as client:
            app.state.search_request_bulkhead = ApplicationBulkhead(
                capacity=2,
                acquire_timeout_seconds=0.01,
            )
            app.state.heavy_search_request_bulkhead = ApplicationBulkhead(
                capacity=1,
                acquire_timeout_seconds=0.01,
                name="heavy_search",
            )
            with ThreadPoolExecutor(max_workers=2) as executor:
                admitted = executor.submit(
                    client.post,
                    "/console-api/search",
                    json={"q": "慢查询"},
                )
                assert service.all_active.wait(timeout=2)

                health_started = monotonic()
                health = client.get("/health")
                health_seconds = monotonic() - health_started

                assert health.status_code == 200
                assert health_seconds < 0.5
                assert not admitted.done()

                service.release.set()
                assert admitted.result(timeout=2).status_code == 200
    finally:
        service.release.set()
        app.dependency_overrides.clear()


class ImmediateDetailService:
    def __init__(self):
        self.calls = 0

    def get_detail(self, patent_id, include_description=False):
        self.calls += 1
        return {"id": patent_id}


def test_saturated_heavy_searches_leave_one_global_slot_for_light_requests():
    search_service = BlockingSearchService(expected_active=3)
    detail_service = ImmediateDetailService()
    app.dependency_overrides[get_search_service] = lambda: search_service
    app.dependency_overrides[get_detail_service] = lambda: detail_service
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        with TestClient(app) as client:
            global_bulkhead = ApplicationBulkhead(
                capacity=4,
                acquire_timeout_seconds=0.01,
                name="global",
            )
            heavy_bulkhead = ApplicationBulkhead(
                capacity=3,
                acquire_timeout_seconds=0.01,
                name="heavy_search",
            )
            app.state.search_request_bulkhead = global_bulkhead
            app.state.heavy_search_request_bulkhead = heavy_bulkhead

            with ThreadPoolExecutor(max_workers=5) as executor:
                heavy_requests = [
                    executor.submit(
                        client.post,
                        "/api/patent/search",
                        json={"q": "长查询"},
                    )
                    for _ in range(3)
                ]
                assert search_service.all_active.wait(timeout=2)

                detail = client.get("/api/patent/detail/patent-1")
                assert detail.status_code == 200
                assert detail.json()["id"] == "patent-1"
                assert detail_service.calls == 1
                assert global_bulkhead.peak_in_flight == 4
                assert heavy_bulkhead.peak_in_flight == 3

                rejected_search = client.post(
                    "/api/patent/search",
                    json={"q": "另一条长查询"},
                )
                assert rejected_search.status_code == 503
                assert rejected_search.json()["code"] == 50301
                assert search_service.calls == 3

                search_service.release.set()
                assert {
                    request.result(timeout=2).status_code for request in heavy_requests
                } == {200}

            assert global_bulkhead.in_flight == 0
            assert heavy_bulkhead.in_flight == 0
    finally:
        search_service.release.set()
        app.dependency_overrides.clear()


def test_cancelled_console_worker_request_holds_slot_until_worker_finishes():
    async def scenario():
        service = BlockingSearchService(expected_active=1)
        global_bulkhead = ApplicationBulkhead(
            capacity=1, acquire_timeout_seconds=0.01
        )
        heavy_bulkhead = ApplicationBulkhead(
            capacity=1,
            acquire_timeout_seconds=0.01,
            name="heavy_search",
        )
        app.dependency_overrides[get_search_service] = lambda: service
        app.dependency_overrides[require_console_access] = lambda: None
        app.state.search_request_bulkhead = global_bulkhead
        app.state.heavy_search_request_bulkhead = heavy_bulkhead
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                request_task = asyncio.create_task(
                    client.post("/console-api/search", json={"q": "阀门"})
                )
                assert await asyncio.to_thread(service.all_active.wait, 2)
                assert global_bulkhead.in_flight == 1
                assert heavy_bulkhead.in_flight == 1

                request_task.cancel()
                await asyncio.sleep(0.01)
                assert not request_task.done()
                assert global_bulkhead.in_flight == 1
                assert heavy_bulkhead.in_flight == 1

                service.release.set()
                with pytest.raises(asyncio.CancelledError):
                    await request_task
                assert global_bulkhead.in_flight == 0
                assert heavy_bulkhead.in_flight == 0
        finally:
            service.release.set()
            app.dependency_overrides.clear()

    asyncio.run(scenario())


class FailingThenHealthyService:
    def __init__(self, error: Exception):
        self.error = error
        self.calls = 0

    def search(self, request):
        self.calls += 1
        if self.calls == 1:
            raise self.error
        return {**EMPTY_SEARCH_RESPONSE, "page": request.page, "page_size": request.page_size}


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (SearchDependencyTimeoutError("private timeout"), 50401),
        (OpenSearchQueryError("private known error"), 50001),
        (RuntimeError("private programming error"), 50002),
    ],
)
def test_http_errors_release_the_slot_and_the_next_request_is_admitted(error, expected_code):
    service = FailingThenHealthyService(error)
    app.dependency_overrides[get_search_service] = lambda: service
    app.dependency_overrides[require_api_key] = lambda: None
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            app.state.search_request_bulkhead = ApplicationBulkhead(
                capacity=1,
                acquire_timeout_seconds=0.01,
            )
            app.state.heavy_search_request_bulkhead = ApplicationBulkhead(
                capacity=1,
                acquire_timeout_seconds=0.01,
                name="heavy_search",
            )
            failed = client.post("/api/patent/search", json={"q": "阀门"})
            assert failed.json()["code"] == expected_code
            assert app.state.search_request_bulkhead.in_flight == 0

            recovered = client.post("/api/patent/search", json={"q": "泵"})
            assert recovered.status_code == 200
            assert app.state.search_request_bulkhead.in_flight == 0
    finally:
        app.dependency_overrides.clear()


class MultiStageRepository:
    def __init__(self):
        self.calls = []
        self.target = {
            "_score": 4.5,
            "_source": {
                "patent_id": "patent-1",
                "PublicationNumber": "CN100B",
                "Title": "目标专利",
            },
        }

    def find_target(self, identifier):
        self.calls.append("find_target")
        return "patent_id", self.target, 1

    def find_in_query(self, query, identity):
        self.calls.append("find_in_query")
        return self.target

    def count_with_min_score(self, query, min_score):
        self.calls.append("count_with_min_score")
        return 2 if len(self.calls) == 3 else 4


class RecordingBulkhead:
    def __init__(self):
        self.acquisitions = 0

    @asynccontextmanager
    async def slot(self):
        self.acquisitions += 1
        yield


def test_multi_stage_target_rank_acquires_each_application_slot_once():
    repository = MultiStageRepository()
    global_bulkhead = RecordingBulkhead()
    heavy_bulkhead = RecordingBulkhead()
    app.dependency_overrides[get_search_service] = lambda: SearchService(repository)
    app.dependency_overrides[require_console_access] = lambda: None
    try:
        with TestClient(app) as client:
            app.state.search_request_bulkhead = global_bulkhead
            app.state.heavy_search_request_bulkhead = heavy_bulkhead
            response = client.post(
                "/console-api/test/target-rank",
                json={"q": "阀门", "target_identifier": "CN100B"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert repository.calls == [
        "find_target",
        "find_in_query",
        "count_with_min_score",
        "count_with_min_score",
    ]
    assert global_bulkhead.acquisitions == 1
    assert heavy_bulkhead.acquisitions == 1
