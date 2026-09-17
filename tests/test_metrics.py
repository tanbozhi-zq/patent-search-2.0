"""验证 Prometheus 指标的低基数标签、请求配对与探针/依赖观测契约。"""

import asyncio

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from opensearchpy.exceptions import (
    ConnectionError as OpenSearchConnectionError,
    ConnectionTimeout,
)
import pytest
from prometheus_client.parser import text_string_to_metric_families

from app.api.dependencies import (
    acquire_heavy_search_request_slot,
    acquire_search_request_slot,
)
from app.core.bulkhead import ApplicationBulkhead
from app.core.config import Settings
from app.core.error_handlers import register_error_handlers
from app.core.exceptions import ErrorCode, SearchDependencyError, ServiceError, service_error
from app.core.metrics import (
    BULKHEAD_NAMES,
    HTTP_CODES,
    HTTP_DURATION_BUCKETS,
    HTTP_METHODS,
    HTTP_STATUSES,
    OPENSEARCH_OPERATIONS,
    OPENSEARCH_OUTCOMES,
    PROBE_NAMES,
    QUERY_STAGES,
    QUERY_STAGE_OUTCOMES,
    SEARCH_MODES,
    SEARCH_RANKING_PROFILES,
    SEARCH_SORT_TYPES,
    ServiceMetrics,
    UNMATCHED_ROUTE,
    VECTOR_FIELD_COUNTS,
)
from app.core.timing_contract import MAX_RUNTIME_REQUEST_DEADLINE_SECONDS
from app.core.probes import ReadinessProbe, ServiceLifecycle
from app.core.request_body_limit import QUERY_BODY_PATHS
from app.main import app as service_app
from app.repositories.opensearch_repo import OpenSearchRepository


EMPTY_SEARCH_RESPONSE = {"hits": {"total": {"value": 0}, "hits": []}}


def _samples(metrics: ServiceMetrics, sample_name: str):
    return [
        sample
        for family in text_string_to_metric_families(metrics.render().decode())
        for sample in family.samples
        if sample.name == sample_name
    ]


def _sample_value(metrics: ServiceMetrics, sample_name: str, **labels: str) -> float:
    matches = [
        sample.value
        for sample in _samples(metrics, sample_name)
        if sample.labels == labels
    ]
    assert len(matches) == 1
    return matches[0]


def _instrumented_test_app() -> tuple[FastAPI, ServiceMetrics]:
    app = FastAPI()

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    @app.get("/known-error")
    async def known_error():
        raise service_error(ErrorCode.SERVICE_BUSY)

    @app.get("/unexpected-error")
    async def unexpected_error():
        raise RuntimeError("private exception payload")

    register_error_handlers(app)
    metrics = ServiceMetrics(
        http_routes=(getattr(route, "path", "") for route in app.routes),
        started_at_seconds=1234,
    )
    app.state.service_metrics = metrics
    return app, metrics


def test_http_success_known_error_and_unknown_error_are_observed_by_template():
    app, metrics = _instrumented_test_app()

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/ok").status_code == 200
        assert client.get("/known-error").status_code == 503
        assert client.get("/unexpected-error").status_code == 500

    assert _sample_value(
        metrics,
        "patent_search_http_requests_total",
        method="GET",
        route="/ok",
        status="200",
        code="0",
    ) == 1
    assert _sample_value(
        metrics,
        "patent_search_http_requests_total",
        method="GET",
        route="/known-error",
        status="503",
        code="50301",
    ) == 1
    assert _sample_value(
        metrics,
        "patent_search_http_requests_total",
        method="GET",
        route="/unexpected-error",
        status="500",
        code="50002",
    ) == 1
    assert _sample_value(
        metrics,
        "patent_search_http_requests_in_flight",
        method="GET",
    ) == 0


def test_http_metrics_never_export_raw_path_query_or_request_id():
    app, metrics = _instrumented_test_app()
    secret_path = "CN-SECRET-PATENT-123"
    secret_query = "private-query-secret"
    secret_request_id = "private-request-id"

    with TestClient(app) as client:
        response = client.get(
            f"/missing/{secret_path}?q={secret_query}",
            headers={"X-Request-ID": secret_request_id},
        )

    assert response.status_code == 404
    exposition = metrics.render().decode()
    assert secret_path not in exposition
    assert secret_query not in exposition
    assert secret_request_id not in exposition
    assert _sample_value(
        metrics,
        "patent_search_http_requests_total",
        method="GET",
        route=UNMATCHED_ROUTE,
        status="404",
        code="40400",
    ) == 1


def test_unknown_label_values_collapse_to_fixed_fallbacks():
    metrics = ServiceMetrics(http_routes={"/safe"})

    metrics.start_http_request("PATCH")
    metrics.finish_http_request(
        method="PATCH",
        route="/raw/CN-SECRET",
        status=599,
        code=99999,
        elapsed_seconds=1,
    )

    assert _sample_value(
        metrics,
        "patent_search_http_requests_total",
        method="other",
        route=UNMATCHED_ROUTE,
        status="other",
        code="other",
    ) == 1


def test_request_without_a_started_response_only_releases_in_flight_gauge():
    metrics = ServiceMetrics(http_routes={"/cancelled"})

    metrics.start_http_request("GET")
    metrics.cancel_http_request(method="GET")

    assert _sample_value(
        metrics,
        "patent_search_http_requests_in_flight",
        method="GET",
    ) == 0
    assert _samples(metrics, "patent_search_http_requests_total") == []


class _RecordingReadinessClient:
    def __init__(self, outcome=True):
        self.calls = []
        self.outcome = outcome
        self.indices = self

    def exists(self, **kwargs):
        self.calls.append(kwargs)
        return self.outcome

    def close(self):
        pass


def test_metrics_endpoint_is_lightweight_and_bypasses_business_controls(monkeypatch):
    import app.main as main_module

    readiness_client = _RecordingReadinessClient()
    monkeypatch.setattr(
        main_module,
        "build_readiness_client",
        lambda _settings: readiness_client,
    )

    with TestClient(service_app) as client:
        first = client.get("/metrics")
        second = client.get("/metrics")

    assert first.status_code == second.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert "text/plain" in first.headers["content-type"]
    assert readiness_client.calls == []
    assert 'route="/metrics"' not in second.text

    routes = {
        route.path: route
        for route in service_app.routes
        if isinstance(route, APIRoute)
    }
    assert "/metrics" not in QUERY_BODY_PATHS
    dependency_calls = {
        dependency.call for dependency in routes["/metrics"].dependant.dependencies
    }
    assert acquire_search_request_slot not in dependency_calls
    assert acquire_heavy_search_request_slot not in dependency_calls


def test_not_ready_probe_uses_no_business_code_and_never_looks_like_overload(
    monkeypatch,
):
    import app.main as main_module

    readiness_client = _RecordingReadinessClient(outcome=False)
    monkeypatch.setattr(
        main_module,
        "build_readiness_client",
        lambda _settings: readiness_client,
    )

    with TestClient(service_app) as client:
        ready = client.get("/ready")
        metrics = client.get("/metrics").text

    assert ready.status_code == 503
    assert (
        'patent_search_http_requests_total{code="0",method="GET",'
        'route="/ready",status="503"} 1.0'
    ) in metrics
    assert 'code="50301",method="GET",route="/ready"' not in metrics


class _ScriptedClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)

    def search(self, **_kwargs):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def close(self):
        pass


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "opensearch_max_retries": 0,
        "opensearch_retry_backoff_seconds": 0,
        "patent_search_deadline_seconds": 10,
    }
    values.update(overrides)
    return Settings(**values)


def test_opensearch_success_retry_timeout_connection_and_invalid_response_metrics():
    metrics = ServiceMetrics(http_routes=set())
    retried = OpenSearchRepository(
        settings=_settings(opensearch_max_retries=1),
        client=_ScriptedClient(
            [
                OpenSearchConnectionError("N/A", "failed", OSError("private")),
                EMPTY_SEARCH_RESPONSE,
            ]
        ),
        metrics=metrics,
    )
    assert retried.search({"query": {"match_all": {}}}) == EMPTY_SEARCH_RESPONSE

    failed_cases = [
        (
            OpenSearchConnectionError("N/A", "failed", OSError("private")),
            "connection_error",
        ),
        (ConnectionTimeout("TIMEOUT", "timed out", TimeoutError()), "timeout"),
        ({"hits": {}}, "invalid_response"),
    ]
    for outcome, expected_metric_outcome in failed_cases:
        repository = OpenSearchRepository(
            settings=_settings(),
            client=_ScriptedClient([outcome]),
            metrics=metrics,
        )
        with pytest.raises(SearchDependencyError):
            repository.search({"query": {"match_all": {}}})
        assert _sample_value(
            metrics,
            "patent_search_opensearch_calls_total",
            operation="search",
            outcome=expected_metric_outcome,
        ) == 1

    assert _sample_value(
        metrics,
        "patent_search_opensearch_calls_total",
        operation="search",
        outcome="success",
    ) == 1
    assert _sample_value(
        metrics,
        "patent_search_opensearch_retries_total",
        operation="search",
        outcome="connection_error",
    ) == 1


def test_both_bulkheads_publish_capacity_in_flight_and_rejections():
    metrics = ServiceMetrics(http_routes=set())

    async def reject_once(name: str):
        bulkhead = ApplicationBulkhead(
            capacity=1,
            acquire_timeout_seconds=0.001,
            name=name,
            metrics=metrics,
        )
        async with bulkhead.slot():
            with pytest.raises(ServiceError):
                async with bulkhead.slot():
                    pytest.fail("saturated bulkhead must reject")
        assert bulkhead.in_flight == 0

    async def scenario():
        await reject_once("global")
        await reject_once("heavy_search")

    asyncio.run(scenario())

    for name in BULKHEAD_NAMES:
        assert _sample_value(
            metrics,
            "patent_search_bulkhead_capacity",
            bulkhead=name,
        ) == 1
        assert _sample_value(
            metrics,
            "patent_search_bulkhead_in_flight",
            bulkhead=name,
        ) == 0
        assert _sample_value(
            metrics,
            "patent_search_bulkhead_rejections_total",
            bulkhead=name,
        ) == 1


def test_probe_and_process_metrics_track_lifecycle_and_readiness():
    metrics = ServiceMetrics(http_routes=set(), started_at_seconds=1234)
    lifecycle = ServiceLifecycle(metrics=metrics)
    lifecycle.mark_started()

    async def scenario():
        probe = ReadinessProbe(
            check=lambda: True,
            timeout_seconds=0.1,
            success_cache_seconds=1,
            failure_cache_seconds=1,
            metrics=metrics,
        )
        assert await probe.is_ready() is True
        assert _sample_value(
            metrics,
            "patent_search_probe_status",
            probe="ready",
        ) == 1
        await probe.close()

    asyncio.run(scenario())

    assert _sample_value(
        metrics,
        "patent_search_probe_status",
        probe="live",
    ) == 1
    assert _sample_value(
        metrics,
        "patent_search_probe_status",
        probe="startup",
    ) == 1
    assert _sample_value(
        metrics,
        "patent_search_probe_status",
        probe="ready",
    ) == 0
    assert _sample_value(
        metrics,
        "patent_search_service_start_time_seconds",
    ) == 1234
    assert _sample_value(
        metrics,
        "patent_search_opensearch_calls_total",
        operation="readiness",
        outcome="success",
    ) == 1


def test_metric_contract_uses_fixed_labels_buckets_and_no_caller_series():
    metrics = ServiceMetrics(http_routes={"/safe"})
    metrics.start_http_request("GET")
    metrics.finish_http_request(
        method="GET",
        route="/safe",
        status=200,
        code=0,
        elapsed_seconds=0.1,
    )
    metrics.record_opensearch_call(
        operation="search",
        outcome="success",
        elapsed_seconds=0.1,
    )
    exposition = metrics.render().decode()

    assert "patent_search_rate_limit_rejections_total{" not in exposition
    assert set(HTTP_DURATION_BUCKETS) == {
        float(sample.labels["le"])
        for sample in _samples(
            metrics,
            "patent_search_http_request_duration_seconds_bucket",
        )
        if sample.labels["le"] != "+Inf"
    }

    for sample in _samples(metrics, "patent_search_http_requests_total"):
        assert set(sample.labels) == {"method", "route", "status", "code"}
        assert sample.labels["method"] in HTTP_METHODS | {"other"}
        assert sample.labels["route"] in metrics.http_routes
        assert sample.labels["status"] in HTTP_STATUSES | {"other"}
        assert sample.labels["code"] in HTTP_CODES | {"other"}
    for sample in _samples(metrics, "patent_search_opensearch_calls_total"):
        assert set(sample.labels) == {"operation", "outcome"}
        assert sample.labels["operation"] in OPENSEARCH_OPERATIONS | {"other"}
        assert sample.labels["outcome"] in OPENSEARCH_OUTCOMES | {"other"}
    for sample in _samples(metrics, "patent_search_probe_status"):
        assert set(sample.labels) == {"probe"}
        assert sample.labels["probe"] in PROBE_NAMES


def test_query_stage_metrics_use_only_bounded_labels_and_hide_sensitive_values():
    metrics = ServiceMetrics(http_routes={"/safe"})
    metrics.record_query_stage(
        stage="query_vector",
        mode="vector",
        vector_field_count=2,
        sort_type="date",
        ranking_profile="patent-vector-rrf-v1-2",
        outcome="success",
        elapsed_seconds=0.25,
    )
    sensitive = "CONFIDENTIAL-QUERY-ENDPOINT-FIELD-COMBINATION"
    metrics.record_query_stage(
        stage=sensitive,
        mode=sensitive,
        vector_field_count=999,
        sort_type=sensitive,
        ranking_profile=sensitive,
        outcome=sensitive,
        elapsed_seconds=0.1,
    )

    expected = {
        "stage": "query_vector",
        "mode": "vector",
        "vector_field_count": "2",
        "sort_type": "date",
        "ranking_profile": "patent-vector-rrf-v1-2",
        "outcome": "success",
    }
    assert _sample_value(
        metrics,
        "patent_search_query_stage_calls_total",
        **expected,
    ) == 1
    assert _sample_value(
        metrics,
        "patent_search_query_stage_duration_seconds_count",
        **expected,
    ) == 1
    bucket_samples = _samples(
        metrics,
        "patent_search_query_stage_duration_seconds_bucket",
    )
    assert {float(sample.labels["le"]) for sample in bucket_samples} == {
        *HTTP_DURATION_BUCKETS,
        float("inf"),
    }
    exposition = metrics.render().decode()
    assert sensitive not in exposition

    for sample in _samples(metrics, "patent_search_query_stage_calls_total"):
        assert set(sample.labels) == {
            "stage",
            "mode",
            "vector_field_count",
            "sort_type",
            "ranking_profile",
            "outcome",
        }
        assert sample.labels["stage"] in QUERY_STAGES | {"other"}
        assert sample.labels["mode"] in SEARCH_MODES | {"other"}
        assert sample.labels["vector_field_count"] in VECTOR_FIELD_COUNTS | {"other"}
        assert sample.labels["sort_type"] in SEARCH_SORT_TYPES | {"other"}
        assert sample.labels["ranking_profile"] in SEARCH_RANKING_PROFILES | {"other"}
        assert sample.labels["outcome"] in QUERY_STAGE_OUTCOMES | {"other"}


def test_duration_histograms_keep_deadline_cleanup_overshoot_in_a_finite_bucket():
    metrics = ServiceMetrics(http_routes={"/safe"})
    elapsed = MAX_RUNTIME_REQUEST_DEADLINE_SECONDS + 0.001
    metrics.start_http_request("GET")
    metrics.finish_http_request(
        method="GET",
        route="/safe",
        status=200,
        code=0,
        elapsed_seconds=elapsed,
    )
    metrics.record_opensearch_call(
        operation="search",
        outcome="success",
        elapsed_seconds=elapsed,
    )

    http_labels = {
        "method": "GET",
        "route": "/safe",
        "status": "200",
        "code": "0",
    }
    assert _sample_value(
        metrics,
        "patent_search_http_request_duration_seconds_bucket",
        **http_labels,
        le="240.0",
    ) == 0
    assert _sample_value(
        metrics,
        "patent_search_http_request_duration_seconds_bucket",
        **http_labels,
        le="300.0",
    ) == 1
    assert _sample_value(
        metrics,
        "patent_search_http_request_duration_seconds_bucket",
        **http_labels,
        le="+Inf",
    ) == 1

    dependency_labels = {"operation": "search", "outcome": "success"}
    assert _sample_value(
        metrics,
        "patent_search_opensearch_call_duration_seconds_bucket",
        **dependency_labels,
        le="240.0",
    ) == 0
    assert _sample_value(
        metrics,
        "patent_search_opensearch_call_duration_seconds_bucket",
        **dependency_labels,
        le="300.0",
    ) == 1
    assert _sample_value(
        metrics,
        "patent_search_opensearch_call_duration_seconds_bucket",
        **dependency_labels,
        le="+Inf",
    ) == 1
