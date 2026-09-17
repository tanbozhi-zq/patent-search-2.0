"""验证存活、启动和就绪探针的生命周期状态、缓存、超时与隔离。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib
import logging
from threading import Event, Lock
from time import monotonic, sleep

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api.probes import router as probes_router
from app.api.dependencies import (
    acquire_heavy_search_request_slot,
    acquire_search_request_slot,
)
from app.core.probes import (
    ReadinessProbe,
    ServiceLifecycle,
    ServiceLifecycleState,
)
from app.core.request_body_limit import QUERY_BODY_PATHS


main_module = importlib.import_module("app.main")
app = main_module.app


class RecordingReadinessClient:
    def __init__(self, outcome=True):
        self.outcome = outcome
        self.calls = []
        self.close_calls = 0
        self.indices = self.Indices(self)

    class Indices:
        def __init__(self, owner):
            self.owner = owner

        def exists(self, index, params=None):
            self.owner.calls.append({"index": index, "params": params})
            if isinstance(self.owner.outcome, BaseException):
                raise self.owner.outcome
            return self.owner.outcome

    def close(self):
        self.close_calls += 1


@pytest.fixture
def readiness_client(monkeypatch):
    client = RecordingReadinessClient()
    monkeypatch.setattr(
        main_module,
        "build_readiness_client",
        lambda _settings: client,
    )
    return client


def test_live_startup_ready_and_health_compatibility(readiness_client):
    with TestClient(app) as client:
        live = client.get("/live")
        startup = client.get("/startup")
        assert readiness_client.calls == []
        ready = client.get("/ready")
        health = client.get("/health")

    assert live.status_code == 200
    assert live.json() == {"status": "live"}
    assert startup.status_code == 200
    assert startup.json() == {"status": "started"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}
    assert health.status_code == 200
    assert health.json()["data"]["status"] == "healthy"
    assert all(response.headers.get("X-Request-ID") for response in (live, startup, ready))
    assert readiness_client.calls == [
        {
            "index": main_module.settings.opensearch_index,
            "params": {"request_timeout": main_module.settings.readiness_timeout_seconds},
        }
    ]
    assert readiness_client.close_calls == 1


def test_ready_caches_success_and_coalesces_concurrent_checks(monkeypatch):
    started = Event()
    release = Event()

    class BlockingClient(RecordingReadinessClient):
        class Indices(RecordingReadinessClient.Indices):
            def exists(self, index, params=None):
                with self.owner._lock:
                    self.owner.calls.append({"index": index, "params": params})
                started.set()
                assert release.wait(timeout=2)
                return True

        def __init__(self):
            super().__init__()
            self._lock = Lock()
            self.indices = self.Indices(self)

    readiness_client = BlockingClient()
    monkeypatch.setattr(
        main_module,
        "build_readiness_client",
        lambda _settings: readiness_client,
    )

    with TestClient(app) as client:
        with ThreadPoolExecutor(max_workers=8) as executor:
            first = executor.submit(client.get, "/ready")
            assert started.wait(timeout=1)
            followers = [executor.submit(client.get, "/ready") for _ in range(7)]
            release.set()
            responses = [first.result(timeout=2)] + [
                future.result(timeout=2) for future in followers
            ]

        cached = client.get("/ready")

    assert {response.status_code for response in responses} == {200}
    assert {response.json()["status"] for response in responses} == {"ready"}
    assert cached.status_code == 200
    assert len(readiness_client.calls) == 1


def test_ready_caches_dependency_failure_without_leaking_details(monkeypatch, caplog):
    secret = "https://private-opensearch-node:9200/secret-index"
    readiness_client = RecordingReadinessClient(RuntimeError(secret))
    monkeypatch.setattr(
        main_module,
        "build_readiness_client",
        lambda _settings: readiness_client,
    )
    caplog.set_level(logging.WARNING, logger="app.core.probes")

    with TestClient(app) as client:
        first = client.get("/ready")
        second = client.get("/ready")

    assert first.status_code == 503
    assert second.status_code == 503
    assert first.json() == {"status": "not_ready"}
    assert second.json() == {"status": "not_ready"}
    assert len(readiness_client.calls) == 1
    assert secret not in first.text
    assert secret not in caplog.text


def test_runtime_verification_can_force_a_fresh_readiness_readback():
    calls = []

    def check():
        calls.append("checked")
        return True

    async def scenario():
        probe = ReadinessProbe(
            check=check,
            timeout_seconds=1,
            success_cache_seconds=30,
            failure_cache_seconds=1,
        )
        try:
            assert await probe.is_ready() is True
            assert await probe.is_ready() is True
            assert await probe.is_ready(force_refresh=True) is True
        finally:
            await probe.close()

    asyncio.run(scenario())
    assert calls == ["checked", "checked"]


def test_readiness_probe_returns_on_its_strict_timeout_without_blocking_event_loop():
    release = Event()

    def slow_check():
        assert release.wait(timeout=2)
        return True

    async def scenario():
        probe = ReadinessProbe(
            check=slow_check,
            timeout_seconds=0.01,
            success_cache_seconds=2,
            failure_cache_seconds=1,
        )
        started = monotonic()
        pending = asyncio.create_task(probe.is_ready())
        await asyncio.sleep(0)
        assert not pending.done()
        await asyncio.sleep(0)
        assert monotonic() - started < 0.1
        assert await pending is False
        await probe.close()

    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_probe_routes_bypass_query_budget_and_business_bulkheads():
    routes = {
        route.path: route
        for route in app.routes
        if isinstance(route, APIRoute)
    }

    assert {"/live", "/startup", "/ready"}.isdisjoint(QUERY_BODY_PATHS)
    for path in ("/live", "/startup", "/ready"):
        dependencies = {
            dependency.call for dependency in routes[path].dependant.dependencies
        }
        assert acquire_search_request_slot not in dependencies
        assert acquire_heavy_search_request_slot not in dependencies


def test_lifecycle_tracks_startup_failure_and_shutdown_state():
    lifecycle = ServiceLifecycle()

    assert lifecycle.state is ServiceLifecycleState.STARTING
    assert lifecycle.is_started is False
    lifecycle.mark_started()
    assert lifecycle.is_started is True
    lifecycle.mark_stopping()
    assert lifecycle.state is ServiceLifecycleState.STOPPING
    assert lifecycle.is_started is False
    lifecycle.mark_failed()
    assert lifecycle.state is ServiceLifecycleState.FAILED


@pytest.mark.parametrize(
    "transition",
    [
        lambda lifecycle: None,
        lambda lifecycle: lifecycle.mark_failed(),
        lambda lifecycle: (lifecycle.mark_started(), lifecycle.mark_stopping()),
    ],
)
def test_startup_and_ready_reject_non_started_lifecycle_states(transition):
    probe_app = FastAPI()
    probe_app.include_router(probes_router)
    lifecycle = ServiceLifecycle()
    transition(lifecycle)
    probe_app.state.probe_lifecycle = lifecycle

    with TestClient(probe_app) as client:
        live = client.get("/live")
        startup = client.get("/startup")
        ready = client.get("/ready")

    assert live.status_code == 200
    assert startup.status_code == 503
    assert startup.json() == {"status": "not_started"}
    assert ready.status_code == 503
    assert ready.json() == {"status": "not_ready"}


def test_startup_failure_is_recorded_before_the_listener_can_receive_traffic(monkeypatch):
    class FailingRepository:
        def __init__(self, **_kwargs):
            raise RuntimeError("startup failure")

    monkeypatch.setattr(main_module, "OpenSearchRepository", FailingRepository)

    with pytest.raises(RuntimeError, match="startup failure"):
        with TestClient(app):
            pass

    assert app.state.probe_lifecycle.state is ServiceLifecycleState.FAILED
