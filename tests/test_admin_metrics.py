"""验证管理指标 reader 的固定查询、缓存合并、资源上限与部分失败降级。"""

import asyncio
from urllib.parse import parse_qs

import httpx
import pytest
from prometheus_client.parser import text_string_to_metric_families

from app.core.admin_metrics import (
    ADMIN_METRICS_MAX_RESPONSE_BYTES,
    ADMIN_METRICS_MAX_SERIES_PER_QUERY,
    PrometheusAdminMetricsReader,
    _queries,
)
from app.core.metrics import ServiceMetrics, should_observe_http_path


def _payload(*, labels=None, value="1"):
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": labels or {},
                    "value": [1_700_000_000, value],
                }
            ],
        },
    }


def test_admin_metrics_use_fixed_queries_whitelist_labels_and_cache_singleflight():
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        expression = parse_qs(request.url.query.decode())["query"][0]
        calls.append(expression)
        labels = {
            "instance": "service-a:8000",
            "job": "patent-search",
            "version": "0.10.0",
            "commit": "7a0468c",
            "tag": "v0.10.0",
            "token": "MUST_NOT_ESCAPE",
        }
        return httpx.Response(200, json=_payload(labels=labels))

    async def scenario():
        reader = PrometheusAdminMetricsReader(
            base_url="http://prometheus.internal",
            timeout_seconds=1,
            transport=httpx.MockTransport(handler),
        )
        try:
            first, second = await asyncio.gather(reader.read(300), reader.read(300))
            third = await reader.read(300)
        finally:
            await reader.close()
        return first, second, third

    first, second, third = asyncio.run(scenario())
    expected_queries = set(_queries(300).values())

    assert set(calls) == expected_queries
    assert len(calls) == len(expected_queries)
    assert first == second == third
    assert first.partial is False
    assert all("token" not in sample.labels for result in first.results for sample in result.samples)
    assert all("MUST_NOT_ESCAPE" not in result.model_dump_json() for result in first.results)


def test_admin_metrics_reject_non_allowlisted_windows_before_network_access():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request: {request.url}")

    async def scenario():
        reader = PrometheusAdminMetricsReader(
            base_url="http://prometheus.internal",
            timeout_seconds=1,
            transport=httpx.MockTransport(handler),
        )
        try:
            with pytest.raises(ValueError, match="unsupported administrator metric window"):
                await reader.read(301)
        finally:
            await reader.close()

    asyncio.run(scenario())


def test_multi_instance_bulkhead_queries_preserve_instance_and_global_semantics():
    queries = _queries(300)

    assert queries["bulkhead_capacity_by_instance"] == (
        'patent_search_bulkhead_capacity{job="patent-search"}'
    )
    assert queries["bulkhead_in_flight_by_instance"] == (
        'patent_search_bulkhead_in_flight{job="patent-search"}'
    )
    assert queries["bulkhead_utilization_by_instance"] == (
        'patent_search_bulkhead_in_flight{job="patent-search"} '
        '/ patent_search_bulkhead_capacity{job="patent-search"}'
    )
    assert queries["bulkhead_capacity_total"].startswith("sum by (bulkhead)")
    assert queries["bulkhead_in_flight_total"].startswith("sum by (bulkhead)")
    assert queries["bulkhead_worst_utilization"].startswith("max by (job, bulkhead)")
    assert all('job="patent-search"' in expression for expression in queries.values())


def test_one_oversized_prometheus_series_set_degrades_only_that_result():
    oversized_expression = _queries(300)["build_info"]

    async def handler(request: httpx.Request) -> httpx.Response:
        expression = parse_qs(request.url.query.decode())["query"][0]
        payload = _payload()
        if expression == oversized_expression:
            payload["data"]["result"] = [
                {"metric": {"instance": f"instance-{index}"}, "value": [1, "1"]}
                for index in range(ADMIN_METRICS_MAX_SERIES_PER_QUERY + 1)
            ]
        return httpx.Response(200, json=payload)

    async def scenario():
        reader = PrometheusAdminMetricsReader(
            base_url="http://prometheus.internal",
            timeout_seconds=1,
            transport=httpx.MockTransport(handler),
        )
        try:
            return await reader.read(300)
        finally:
            await reader.close()

    response = asyncio.run(scenario())
    results = {result.key: result for result in response.results}
    assert response.partial is True
    assert results["build_info"].available is False
    assert results["request_rate"].available is True


def test_oversized_prometheus_body_and_dependency_failure_are_sanitized():
    expressions = list(_queries(300).values())

    async def handler(request: httpx.Request) -> httpx.Response:
        expression = parse_qs(request.url.query.decode())["query"][0]
        if expression == expressions[0]:
            return httpx.Response(
                200,
                headers={"Content-Length": str(ADMIN_METRICS_MAX_RESPONSE_BYTES + 1)},
                json=_payload(),
            )
        if expression == expressions[1]:
            raise httpx.ConnectError("private-prometheus-host", request=request)
        return httpx.Response(200, json=_payload())

    async def scenario():
        reader = PrometheusAdminMetricsReader(
            base_url="http://prometheus.internal",
            timeout_seconds=1,
            transport=httpx.MockTransport(handler),
        )
        try:
            return await reader.read(300)
        finally:
            await reader.close()

    response = asyncio.run(scenario())
    assert response.partial is True
    assert sum(not result.available for result in response.results) == 2
    assert "private-prometheus-host" not in response.model_dump_json()


def test_prometheus_batch_has_a_total_wall_clock_deadline():
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=_payload())

    async def scenario():
        reader = PrometheusAdminMetricsReader(
            base_url="http://prometheus.internal",
            timeout_seconds=0.01,
            transport=httpx.MockTransport(handler),
        )
        try:
            return await reader.read(300)
        finally:
            await reader.close()

    response = asyncio.run(scenario())
    assert response.partial is True
    assert all(not result.available for result in response.results)


def test_prometheus_close_cancels_and_awaits_active_singleflight_tasks():
    started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.Event().wait()
        return httpx.Response(200, json=_payload())

    async def scenario():
        reader = PrometheusAdminMetricsReader(
            base_url="http://prometheus.internal",
            timeout_seconds=1,
            transport=httpx.MockTransport(handler),
        )
        read_task = asyncio.create_task(reader.read(300))
        await started.wait()
        await reader.close()
        result = await asyncio.gather(read_task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert reader._inflight == {}
        with pytest.raises(RuntimeError, match="reader is closed"):
            await reader.read(300)

    asyncio.run(scenario())


def test_build_info_is_low_cardinality_and_uses_prometheus_instance_externally():
    metrics = ServiceMetrics(
        http_routes=set(),
        started_at_seconds=1234,
        version="0.10.0",
        commit="7a0468c",
        tag="v0.10.0",
    )
    samples = [
        sample
        for family in text_string_to_metric_families(metrics.render().decode())
        for sample in family.samples
        if sample.name == "patent_search_build_info"
    ]

    assert len(samples) == 1
    assert samples[0].labels == {
        "version": "0.10.0",
        "commit": "7a0468c",
        "tag": "v0.10.0",
    }
    assert "instance" not in samples[0].labels


def test_admin_paths_are_excluded_without_hiding_similarly_named_public_paths():
    for path in (
        "/admin",
        "/admin/",
        "/admin/admin.js",
        "/admin-api/v1/status",
    ):
        assert should_observe_http_path(path) is False
    assert should_observe_http_path("/administrator") is True
    assert should_observe_http_path("/admin/not-a-route") is True
    assert should_observe_http_path("/admin-api/garbage") is True
    assert should_observe_http_path("/api/patent/search") is True
