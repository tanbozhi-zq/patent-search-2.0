"""验证内部 Prometheus 冒烟验收对真实事件、容量护栏和配置风险的判定。"""

import copy
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from app.core.admin_metrics import _queries
from scripts.smoke_admin_metrics import (
    ACCEPTANCE_COUNTER_METRIC_FAMILIES,
    ACCEPTANCE_COUNTER_QUERIES,
    ACCEPTANCE_EVENT_VISIBILITY_WINDOW_SECONDS,
    ACCEPTANCE_METADATA_TARGET_LIMIT,
    EXPECTED_SCRAPE_BODY_SIZE_BYTES,
    EXPECTED_SCRAPE_SAMPLE_LIMIT,
    PROMETHEUS_JOB,
    _admin_snapshots_follow_counter_snapshot,
    _bounded_base_url,
    _capture_admin_payloads_after,
    _capture_fresh_acceptance_baseline,
    _prometheus_time,
    _read_acceptance_counter_metadata,
    _read_admin_payloads,
    _read_direct_metrics,
    _requires_event_visibility,
    _selected_windows,
    evaluate_acceptance_baseline,
    evaluate_acceptance_event_delta,
    evaluate_acceptance_guard,
    evaluate_admin_payloads,
    evaluate_admin_prometheus_comparison,
    evaluate_prometheus_payloads,
    evaluate_scrape_budget_payloads,
    main as metrics_main,
)


ROOT = Path(__file__).resolve().parents[1]
RELEASE_STARTED_AT = 1_700_000_000.0


def _release_status(
    *,
    commit="3b151b6",
    version="0.10.0",
    tag="v0.10.0",
    instance="instance-a",
    started_at="2023-11-14T22:13:20+00:00",
):
    return {
        "metrics_source": "prometheus",
        "release": {
            "service_version": version,
            "commit": commit,
            "tag": tag,
            "instance_id": instance,
            "started_at": started_at,
        },
    }


def _release_metric_samples(
    *,
    commit="3b151b6",
    version="0.10.0",
    tag="v0.10.0",
    instances=("instance-a",),
    started_at=RELEASE_STARTED_AT,
):
    return {
        "build_info": [
            {
                "labels": {
                    "instance": instance,
                    "version": version,
                    "commit": commit,
                    "tag": tag,
                },
                "value": 1.0,
            }
            for instance in instances
        ],
        "service_start_time_seconds": [
            {
                "labels": {"instance": instance},
                "value": started_at,
            }
            for instance in instances
        ],
    }


def _acceptance_snapshot(
    evaluation_time,
    *,
    counters=None,
    commit="3b151b6",
    version="0.10.0",
    tag="v0.10.0",
    instances=("instance-a",),
    started_at=RELEASE_STARTED_AT,
    scrape_time=None,
    counter_metadata=None,
):
    samples = _release_metric_samples(
        commit=commit,
        version=version,
        tag=tag,
        instances=instances,
        started_at=started_at,
    )
    samples["up"] = [
        {"labels": {"instance": instance}, "value": 1.0}
        for instance in instances
    ]
    samples["scrape_time"] = [
        {
            "labels": {"instance": instance},
            "value": evaluation_time if scrape_time is None else scrape_time,
        }
        for instance in instances
    ]
    counter_values = (
        {key: 1.0 for key in ACCEPTANCE_COUNTER_QUERIES}
        if counters is None
        else counters
    )
    for key in ACCEPTANCE_COUNTER_QUERIES:
        value = counter_values.get(key)
        samples[key] = (
            []
            if value is None
            else [
                {
                    "labels": {"instance": instance},
                    "value": value,
                }
                for instance in instances
            ]
        )
    metadata = (
        {
            metric: [
                {
                    "target": {
                        "instance": instance,
                        "job": PROMETHEUS_JOB,
                    },
                    "metric": metric,
                    "type": "counter",
                    "help": "acceptance test metric",
                    "unit": "",
                }
                for instance in instances
            ]
            for metric in ACCEPTANCE_COUNTER_METRIC_FAMILIES
        }
        if counter_metadata is None
        else counter_metadata
    )
    return {
        "evaluation_time": evaluation_time,
        "samples": samples,
        "counter_metadata": metadata,
    }


def _metric_result(key, *, samples=None, available=True):
    return {
        "key": key,
        "available": available,
        "samples": samples or [],
    }


def _vector(value="1"):
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [{"metric": {}, "value": [1_700_000_000, value]}],
        },
    }


def _vectors(*values):
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {
                    "metric": {"instance": f"instance-{index}"},
                    "value": [1, value],
                }
                for index, value in enumerate(values)
            ],
        },
    }


def _add_bulkhead_samples(by_key, instances):
    capacities = {"global": 8.0, "heavy_search": 3.0}
    in_flight = {"global": 2.0, "heavy_search": 1.0}
    for key, source in (
        ("bulkhead_capacity_by_instance", capacities),
        ("bulkhead_in_flight_by_instance", in_flight),
    ):
        by_key[key]["samples"] = [
            {
                "labels": {"bulkhead": bulkhead, "instance": instance},
                "value": value,
            }
            for bulkhead, value in source.items()
            for instance in instances
        ]
    by_key["bulkhead_utilization_by_instance"]["samples"] = [
        {
            "labels": {"bulkhead": bulkhead, "instance": instance},
            "value": in_flight[bulkhead] / capacities[bulkhead],
        }
        for bulkhead in capacities
        for instance in instances
    ]
    by_key["bulkhead_capacity_total"]["samples"] = [
        {
            "labels": {"bulkhead": bulkhead},
            "value": value * len(instances),
        }
        for bulkhead, value in capacities.items()
    ]
    by_key["bulkhead_in_flight_total"]["samples"] = [
        {
            "labels": {"bulkhead": bulkhead},
            "value": value * len(instances),
        }
        for bulkhead, value in in_flight.items()
    ]
    by_key["bulkhead_worst_utilization"]["samples"] = [
        {
            "labels": {"bulkhead": bulkhead},
            "value": in_flight[bulkhead] / capacities[bulkhead],
        }
        for bulkhead in capacities
    ]


def _add_acceptance_event_samples(by_key):
    by_key["request_rate"]["samples"] = [{"labels": {}, "value": 1.0}]
    by_key["success_rate"]["samples"] = [{"labels": {}, "value": 0.9}]
    for key in (
        "latency_p50_seconds",
        "latency_p95_seconds",
        "latency_p99_seconds",
    ):
        by_key[key]["samples"] = [{"labels": {}, "value": 0.5}]
    by_key["http_outcome_rate"]["samples"] = [
        {"labels": {"status": "200", "code": "0"}, "value": 0.1},
        {"labels": {"status": "400", "code": "40002"}, "value": 0.1},
    ]
    by_key["route_request_rate"]["samples"] = [
        {"labels": {"route": "/api/patent/search"}, "value": 0.2}
    ]
    by_key["opensearch_call_rate"]["samples"] = [
        {
            "labels": {"operation": "search", "outcome": "timeout"},
            "value": 0.1,
        }
    ]
    by_key["bulkhead_rejections"]["samples"] = [
        {"labels": {"bulkhead": "global"}, "value": 1.0},
        {"labels": {"bulkhead": "heavy_search"}, "value": 1.0},
    ]


def test_smoke_acceptance_requires_real_events_and_two_instances():
    results = [_metric_result(key) for key in _queries(300)]
    by_key = {result["key"]: result for result in results}
    _add_acceptance_event_samples(by_key)
    by_key["build_info"]["samples"] = [
        {"labels": {"instance": "instance-a"}, "value": 1.0},
        {"labels": {"instance": "instance-b"}, "value": 1.0},
    ]
    _add_bulkhead_samples(by_key, ("instance-a", "instance-b"))

    checks = evaluate_admin_payloads(
        {"metrics_source": "prometheus"},
        {
            "source": "prometheus",
            "window_seconds": 300,
            "partial": False,
            "results": results,
        },
        window_seconds=300,
        minimum_instances=2,
        require_acceptance_events=True,
    )

    assert all(checks.values())


def test_smoke_binds_build_info_and_start_time_to_the_expected_release():
    results = [_metric_result(key) for key in _queries(300)]
    by_key = {result["key"]: result for result in results}
    release_samples = _release_metric_samples(commit="deadbee")
    for key, samples in release_samples.items():
        by_key[key]["samples"] = samples
    _add_bulkhead_samples(by_key, ("instance-a",))

    mismatched = evaluate_admin_payloads(
        _release_status(),
        {
            "source": "prometheus",
            "window_seconds": 300,
            "partial": False,
            "results": results,
        },
        window_seconds=300,
        minimum_instances=1,
        require_acceptance_events=False,
        expected_release_commit="3b151b6",
    )

    assert mismatched["admin_release_matches_expected"] is True
    assert mismatched["admin_build_release_matches"] is False
    assert mismatched["admin_build_instance_topology"] is False
    assert mismatched["admin_start_time_matches_release"] is False

    by_key["build_info"]["samples"] = _release_metric_samples()["build_info"]
    matched = evaluate_admin_payloads(
        _release_status(),
        {
            "source": "prometheus",
            "window_seconds": 300,
            "partial": False,
            "results": results,
        },
        window_seconds=300,
        minimum_instances=1,
        require_acceptance_events=False,
        expected_release_commit="3b151b6",
    )

    assert matched["admin_release_matches_expected"] is True
    assert matched["admin_build_release_matches"] is True
    assert matched["admin_build_instance_topology"] is True
    assert matched["admin_start_time_matches_release"] is True


def test_acceptance_baseline_is_bound_to_release_topology_and_start_time():
    checks = evaluate_acceptance_baseline(
        _release_status(),
        _acceptance_snapshot(RELEASE_STARTED_AT + 60),
        expected_release_commit="3b151b6",
        expected_instance_count=1,
    )

    assert all(checks.values())

    stale_build = evaluate_acceptance_baseline(
        _release_status(),
        _acceptance_snapshot(RELEASE_STARTED_AT + 60, commit="deadbee"),
        expected_release_commit="3b151b6",
        expected_instance_count=1,
    )
    assert stale_build["acceptance_baseline_release_matches"] is False

    extra_target = evaluate_acceptance_baseline(
        _release_status(),
        _acceptance_snapshot(
            RELEASE_STARTED_AT + 60,
            instances=("instance-a", "instance-b"),
        ),
        expected_release_commit="3b151b6",
        expected_instance_count=1,
    )
    assert extra_target["acceptance_baseline_release_matches"] is False


@pytest.mark.parametrize(
    ("status", "snapshot"),
    (
        (
            _release_status(version="unknown"),
            _acceptance_snapshot(RELEASE_STARTED_AT + 60, version="unknown"),
        ),
        (
            _release_status(tag="unknown"),
            _acceptance_snapshot(RELEASE_STARTED_AT + 60, tag="unknown"),
        ),
        (
            _release_status(instance="unknown"),
            _acceptance_snapshot(
                RELEASE_STARTED_AT + 60,
                instances=("unknown",),
            ),
        ),
    ),
)
def test_acceptance_baseline_rejects_placeholder_release_identity(status, snapshot):
    checks = evaluate_acceptance_baseline(
        status,
        snapshot,
        expected_release_commit="3b151b6",
        expected_instance_count=1,
    )

    assert checks["acceptance_baseline_release_matches"] is False


def test_acceptance_baseline_rejects_placeholder_secondary_instance():
    checks = evaluate_acceptance_baseline(
        _release_status(),
        _acceptance_snapshot(
            RELEASE_STARTED_AT + 60,
            instances=("instance-a", "unknown"),
        ),
        expected_release_commit="3b151b6",
        expected_instance_count=2,
    )

    assert checks["acceptance_baseline_release_matches"] is False


def test_acceptance_baseline_requires_a_scrape_after_capture_started():
    capture_started_at = RELEASE_STARTED_AT + 60
    simultaneous = evaluate_acceptance_baseline(
        _release_status(),
        _acceptance_snapshot(
            capture_started_at + 1,
            scrape_time=capture_started_at,
        ),
        expected_release_commit="3b151b6",
        expected_instance_count=1,
        minimum_scrape_time=capture_started_at,
    )
    stale = evaluate_acceptance_baseline(
        _release_status(),
        _acceptance_snapshot(
            capture_started_at + 1,
            scrape_time=capture_started_at - 1,
        ),
        expected_release_commit="3b151b6",
        expected_instance_count=1,
        minimum_scrape_time=capture_started_at,
    )
    fresh = evaluate_acceptance_baseline(
        _release_status(),
        _acceptance_snapshot(
            capture_started_at + 16,
            scrape_time=capture_started_at + 15,
        ),
        expected_release_commit="3b151b6",
        expected_instance_count=1,
        minimum_scrape_time=capture_started_at,
    )

    assert simultaneous["acceptance_baseline_scrapes_fresh"] is False
    assert stale["acceptance_baseline_scrapes_fresh"] is False
    assert fresh["acceptance_baseline_scrapes_fresh"] is True


def test_acceptance_baseline_requires_initialized_counter_series():
    checks = evaluate_acceptance_baseline(
        _release_status(),
        _acceptance_snapshot(
            RELEASE_STARTED_AT + 60,
            counters={},
        ),
        expected_release_commit="3b151b6",
        expected_instance_count=1,
    )

    assert checks["acceptance_baseline_counter_series_initialized"] is False


@pytest.mark.parametrize(
    "metadata_drift",
    (
        "gauge",
        "missing-metric",
        "null-metric",
        "wrong-metric",
        "missing-target",
        "duplicate-target",
        "extra-target",
    ),
)
def test_acceptance_baseline_requires_exact_counter_metadata_per_target(
    metadata_drift,
):
    snapshot = _acceptance_snapshot(
        RELEASE_STARTED_AT + 60,
        instances=("instance-a", "instance-b"),
    )
    metric = sorted(ACCEPTANCE_COUNTER_METRIC_FAMILIES)[0]
    entries = snapshot["counter_metadata"][metric]
    if metadata_drift == "gauge":
        entries[0]["type"] = "gauge"
    elif metadata_drift == "missing-metric":
        entries[0].pop("metric")
    elif metadata_drift == "null-metric":
        entries[0]["metric"] = None
    elif metadata_drift == "wrong-metric":
        entries[0]["metric"] = "unrelated_total"
    elif metadata_drift == "missing-target":
        entries.pop()
    elif metadata_drift == "duplicate-target":
        entries.append(copy.deepcopy(entries[0]))
    else:
        extra = copy.deepcopy(entries[0])
        extra["target"]["instance"] = "instance-c"
        entries.append(extra)

    checks = evaluate_acceptance_baseline(
        _release_status(),
        snapshot,
        expected_release_commit="3b151b6",
        expected_instance_count=2,
    )

    assert checks["acceptance_baseline_counter_metadata_matches"] is False
    assert all(checks.values()) is False


def test_acceptance_event_and_guard_bind_counter_metadata():
    before = _acceptance_snapshot(
        RELEASE_STARTED_AT + 60,
        counters={key: 10.0 for key in ACCEPTANCE_COUNTER_QUERIES},
    )
    after = _acceptance_snapshot(
        RELEASE_STARTED_AT + 660,
        counters={key: 11.0 for key in ACCEPTANCE_COUNTER_QUERIES},
    )
    guard = copy.deepcopy(after)
    guard["evaluation_time"] = RELEASE_STARTED_AT + 675
    guard["samples"]["scrape_time"][0]["value"] = RELEASE_STARTED_AT + 674
    metric = sorted(ACCEPTANCE_COUNTER_METRIC_FAMILIES)[0]
    after["counter_metadata"][metric][0]["type"] = "gauge"
    guard["counter_metadata"][metric][0]["type"] = "gauge"

    event_checks = evaluate_acceptance_event_delta(
        _release_status(),
        before,
        after,
        expected_release_commit="3b151b6",
        expected_instance_count=1,
    )
    guard_checks = evaluate_acceptance_guard(
        _release_status(),
        before,
        guard,
        expected_release_commit="3b151b6",
        expected_instance_count=1,
        minimum_scrape_time=RELEASE_STARTED_AT + 670,
    )

    assert event_checks["acceptance_counter_metadata_stable"] is False
    assert event_checks["normal_2xx_delta_observed"] is False
    assert guard_checks["acceptance_guard_counter_metadata_stable"] is False


def test_acceptance_counter_metadata_reader_is_bounded_and_job_scoped():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        metric = request.url.params["metric"]
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": [
                    {
                        "target": {
                            "instance": "instance-a",
                            "job": PROMETHEUS_JOB,
                        },
                        "metric": metric,
                        "type": "counter",
                        "help": f"metadata for {metric}",
                        "unit": "",
                    }
                ],
            },
        )

    with httpx.Client(
        base_url="http://prometheus.internal",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    ) as client:
        metadata = _read_acceptance_counter_metadata(client)

    assert set(metadata) == ACCEPTANCE_COUNTER_METRIC_FAMILIES
    assert len(calls) == len(ACCEPTANCE_COUNTER_METRIC_FAMILIES)
    assert all(request.url.path == "/api/v1/targets/metadata" for request in calls)
    assert all(
        request.url.params["match_target"] == f'{{job="{PROMETHEUS_JOB}"}}'
        and request.url.params["limit"] == str(ACCEPTANCE_METADATA_TARGET_LIMIT)
        for request in calls
    )


def test_prometheus_time_parses_the_official_scalar_response():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "scalar",
                    "result": [1_777_777_777.25, "1777777777.25"],
                },
            },
        )

    with httpx.Client(
        base_url="http://prometheus.internal",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    ) as client:
        evaluation_time = _prometheus_time(client)

    assert evaluation_time == 1_777_777_777.25
    assert len(calls) == 1
    assert calls[0].url.path == "/api/v1/query"
    assert calls[0].url.params["query"] == "time()"


def test_acceptance_baseline_capture_waits_for_a_new_scrape(monkeypatch):
    capture_started_at = RELEASE_STARTED_AT + 60
    snapshots = [
        _acceptance_snapshot(
            capture_started_at + 1,
            scrape_time=capture_started_at - 1,
        ),
        _acceptance_snapshot(
            capture_started_at + 16,
            scrape_time=capture_started_at + 15,
        ),
    ]
    clock = [0.0]
    monkeypatch.setattr(
        "scripts.smoke_admin_metrics._prometheus_time",
        lambda _client: capture_started_at,
    )
    monkeypatch.setattr(
        "scripts.smoke_admin_metrics._read_acceptance_snapshot",
        lambda _client: snapshots.pop(0),
    )

    snapshot, checks, started_at = _capture_fresh_acceptance_baseline(
        object(),
        _release_status(),
        expected_release_commit="3b151b6",
        expected_instance_count=1,
        timeout_seconds=2,
        poll_seconds=1,
        now=lambda: clock[0],
        sleeper=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )

    assert started_at == capture_started_at
    assert snapshot["evaluation_time"] == capture_started_at + 16
    assert all(checks.values())
    assert clock[0] == 1


def test_baseline_output_is_captured_after_static_direct_checks(
    monkeypatch,
    capsys,
):
    counter = [100]
    windows = (300, 900, 3600)
    admin_payloads = {
        window: {"generated_at": "2023-11-14T22:14:56+00:00"}
        for window in windows
    }

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "success", "data": {"groups": []}}

    class Client:
        timeout = 5

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, *_args, **_kwargs):
            return Response()

    def capture_baseline(*_args, **_kwargs):
        snapshot_time = float(counter[0])
        return {"evaluation_time": snapshot_time}, {"baseline": True}, snapshot_time

    def read_direct(*_args, **_kwargs):
        # This event happens before the baseline command returns. It belongs to
        # the baseline, not to the formal second-round acceptance events.
        counter[0] += 1
        return {window: {} for window in windows}

    def capture_guard(
        _client,
        _status,
        baseline,
        *,
        minimum_scrape_time,
        **_kwargs,
    ):
        assert baseline["evaluation_time"] == float(counter[0])
        assert minimum_scrape_time > baseline["evaluation_time"]
        return (
            {"evaluation_time": minimum_scrape_time + 1},
            {
                "acceptance_guard_release_stable": True,
                "acceptance_guard_targets_healthy": True,
                "acceptance_guard_scrape_fresh": True,
                "acceptance_guard_counters_stable": True,
            },
        )

    monkeypatch.setenv("ADMIN_VIEWER_USERNAME", "viewer")
    monkeypatch.setenv("ADMIN_VIEWER_PASSWORD", "secret")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "smoke_admin_metrics",
            "http://admin.internal",
            "http://prometheus.internal",
            "--expected-release-commit",
            "3b151b6",
            "--capture-acceptance-baseline",
        ],
    )
    monkeypatch.setattr(
        "scripts.smoke_admin_metrics.httpx.Client",
        lambda **_kwargs: Client(),
    )
    monkeypatch.setattr(
        "scripts.smoke_admin_metrics._read_admin_payloads",
        lambda *_args, **_kwargs: (_release_status(), admin_payloads),
    )
    monkeypatch.setattr(
        "scripts.smoke_admin_metrics._read_admin_status",
        lambda *_args, **_kwargs: _release_status(),
    )
    monkeypatch.setattr(
        "scripts.smoke_admin_metrics._prometheus_query",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "scripts.smoke_admin_metrics.evaluate_prometheus_payloads",
        lambda *_args, **_kwargs: {"prometheus": True},
    )
    monkeypatch.setattr(
        "scripts.smoke_admin_metrics.evaluate_scrape_budget_payloads",
        lambda *_args, **_kwargs: {"scrape_budget": True},
    )
    monkeypatch.setattr(
        "scripts.smoke_admin_metrics._capture_fresh_acceptance_baseline",
        capture_baseline,
    )
    monkeypatch.setattr(
        "scripts.smoke_admin_metrics._prometheus_time",
        lambda *_args, **_kwargs: float(counter[0]) + 0.5,
    )
    monkeypatch.setattr(
        "scripts.smoke_admin_metrics._capture_acceptance_guard",
        capture_guard,
    )
    monkeypatch.setattr(
        "scripts.smoke_admin_metrics._read_direct_metrics",
        read_direct,
    )
    monkeypatch.setattr(
        "scripts.smoke_admin_metrics.evaluate_admin_payloads",
        lambda *_args, **_kwargs: {"admin": True},
    )
    monkeypatch.setattr(
        "scripts.smoke_admin_metrics.evaluate_admin_prometheus_comparison",
        lambda *_args, **_kwargs: {"direct": True},
    )

    assert metrics_main() == 0
    output = json.loads(capsys.readouterr().out)

    assert output["ok"] is True
    assert output["acceptance_baseline_time"] > float(counter[0])
    assert output["checks"]["acceptance_baseline_guard_counters_stable"] is True


def test_acceptance_baseline_capture_timeout_cannot_be_flipped_by_new_status(
    monkeypatch,
):
    capture_started_at = RELEASE_STARTED_AT + 60
    restarted = _acceptance_snapshot(
        capture_started_at + 16,
        started_at=RELEASE_STARTED_AT + 1,
        scrape_time=capture_started_at + 15,
    )
    restarted_status = _release_status(started_at="2023-11-14T22:13:21+00:00")
    clock = [0.0]
    monkeypatch.setattr(
        "scripts.smoke_admin_metrics._prometheus_time",
        lambda _client: capture_started_at,
    )
    monkeypatch.setattr(
        "scripts.smoke_admin_metrics._read_acceptance_snapshot",
        lambda _client: restarted,
    )

    with pytest.raises(ValueError, match="baseline capture timed out"):
        _capture_fresh_acceptance_baseline(
            object(),
            _release_status(),
            expected_release_commit="3b151b6",
            expected_instance_count=1,
            timeout_seconds=2,
            poll_seconds=1,
            now=lambda: clock[0],
            sleeper=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        )

    assert all(
        evaluate_acceptance_baseline(
            restarted_status,
            restarted,
            expected_release_commit="3b151b6",
            expected_instance_count=1,
            minimum_scrape_time=capture_started_at,
        ).values()
    )


def test_acceptance_counter_queries_use_reviewed_business_event_contracts():
    assert 'route="/api/patent/search"' in ACCEPTANCE_COUNTER_QUERIES[
        "normal_2xx"
    ]
    assert 'status="400",code="40002"' in ACCEPTANCE_COUNTER_QUERIES[
        "known_4xx"
    ]
    assert 'operation="search"' in ACCEPTANCE_COUNTER_QUERIES[
        "dependency_failure"
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("commit", "deadbee"),
        ("version", "0.9.0"),
        ("tag", "v0.9.0"),
    ),
)
def test_acceptance_baseline_rejects_each_build_identity_drift(field, value):
    snapshot = _acceptance_snapshot(
        RELEASE_STARTED_AT + 60,
        **{field: value},
    )

    checks = evaluate_acceptance_baseline(
        _release_status(),
        snapshot,
        expected_release_commit="3b151b6",
        expected_instance_count=1,
    )

    assert checks["acceptance_baseline_release_matches"] is False


def test_acceptance_event_delta_requires_new_events_after_the_baseline():
    baseline_counters = {key: 10.0 for key in ACCEPTANCE_COUNTER_QUERIES}
    before = _acceptance_snapshot(
        RELEASE_STARTED_AT + 60,
        counters=baseline_counters,
    )
    unchanged = _acceptance_snapshot(
        RELEASE_STARTED_AT + 90,
        counters=baseline_counters,
    )

    checks = evaluate_acceptance_event_delta(
        _release_status(),
        before,
        unchanged,
        expected_release_commit="3b151b6",
        expected_instance_count=1,
    )

    assert checks["acceptance_interval_bounded"] is True
    assert checks["acceptance_release_stable"] is True
    assert checks["acceptance_counters_monotonic"] is True
    assert not any(
        checks[f"{key}_delta_observed"]
        for key in ACCEPTANCE_COUNTER_QUERIES
    )


def test_acceptance_event_delta_requires_every_reviewed_event_category():
    before_values = {key: 10.0 for key in ACCEPTANCE_COUNTER_QUERIES}
    after_values = {key: 11.0 for key in ACCEPTANCE_COUNTER_QUERIES}
    after_values["heavy_search_bulkhead_rejection"] = 10.0

    checks = evaluate_acceptance_event_delta(
        _release_status(),
        _acceptance_snapshot(
            RELEASE_STARTED_AT + 60,
            counters=before_values,
        ),
        _acceptance_snapshot(
            RELEASE_STARTED_AT + 660,
            counters=after_values,
        ),
        expected_release_commit="3b151b6",
        expected_instance_count=1,
    )

    assert checks["normal_2xx_delta_observed"] is True
    assert checks["known_4xx_delta_observed"] is True
    assert checks["dependency_failure_delta_observed"] is True
    assert checks["global_bulkhead_rejection_delta_observed"] is True
    assert checks["heavy_search_bulkhead_rejection_delta_observed"] is False


def test_acceptance_event_delta_rejects_a_series_created_only_after_baseline():
    before_values = {key: 10.0 for key in ACCEPTANCE_COUNTER_QUERIES}
    before_values["known_4xx"] = None
    after_values = {key: 11.0 for key in ACCEPTANCE_COUNTER_QUERIES}

    checks = evaluate_acceptance_event_delta(
        _release_status(),
        _acceptance_snapshot(
            RELEASE_STARTED_AT + 60,
            counters=before_values,
        ),
        _acceptance_snapshot(
            RELEASE_STARTED_AT + 90,
            counters=after_values,
        ),
        expected_release_commit="3b151b6",
        expected_instance_count=1,
    )

    assert checks["acceptance_counters_monotonic"] is False
    assert checks["known_4xx_delta_observed"] is False


def test_acceptance_event_delta_rejects_restart_and_counter_reset():
    before_values = {key: 10.0 for key in ACCEPTANCE_COUNTER_QUERIES}
    after_values = {key: 11.0 for key in ACCEPTANCE_COUNTER_QUERIES}
    before = _acceptance_snapshot(
        RELEASE_STARTED_AT + 60,
        counters=before_values,
    )
    restarted = _acceptance_snapshot(
        RELEASE_STARTED_AT + 90,
        counters=after_values,
        started_at=RELEASE_STARTED_AT + 1,
    )

    restart_checks = evaluate_acceptance_event_delta(
        _release_status(),
        before,
        restarted,
        expected_release_commit="3b151b6",
        expected_instance_count=1,
    )
    assert restart_checks["acceptance_release_stable"] is False
    assert restart_checks["normal_2xx_delta_observed"] is False

    reset_checks = evaluate_acceptance_event_delta(
        _release_status(),
        before,
        _acceptance_snapshot(
            RELEASE_STARTED_AT + 90,
            counters={key: 1.0 for key in ACCEPTANCE_COUNTER_QUERIES},
        ),
        expected_release_commit="3b151b6",
        expected_instance_count=1,
    )
    assert reset_checks["acceptance_counters_monotonic"] is False
    assert reset_checks["normal_2xx_delta_observed"] is False


def test_acceptance_completion_status_rejects_restart_after_admin_capture():
    before_values = {key: 10.0 for key in ACCEPTANCE_COUNTER_QUERIES}
    after_values = {key: 11.0 for key in ACCEPTANCE_COUNTER_QUERIES}
    before = _acceptance_snapshot(
        RELEASE_STARTED_AT + 60,
        counters=before_values,
    )
    after = _acceptance_snapshot(
        RELEASE_STARTED_AT + 90,
        counters=after_values,
    )

    captured_checks = evaluate_acceptance_event_delta(
        _release_status(),
        before,
        after,
        expected_release_commit="3b151b6",
        expected_instance_count=1,
    )
    completion_checks = evaluate_acceptance_event_delta(
        _release_status(started_at="2023-11-14T22:13:21+00:00"),
        before,
        after,
        expected_release_commit="3b151b6",
        expected_instance_count=1,
    )

    assert all(captured_checks.values())
    assert all(completion_checks.values()) is False
    assert completion_checks["acceptance_release_stable"] is False


def test_acceptance_event_delta_rejects_stale_up_even_when_it_is_one():
    before_values = {key: 10.0 for key in ACCEPTANCE_COUNTER_QUERIES}
    after_values = {key: 11.0 for key in ACCEPTANCE_COUNTER_QUERIES}
    before_time = RELEASE_STARTED_AT + 60
    after_time = before_time + 60

    checks = evaluate_acceptance_event_delta(
        _release_status(),
        _acceptance_snapshot(
            before_time,
            counters=before_values,
            scrape_time=before_time,
        ),
        _acceptance_snapshot(
            after_time,
            counters=after_values,
            scrape_time=after_time - 31,
        ),
        expected_release_commit="3b151b6",
        expected_instance_count=1,
    )

    assert checks["acceptance_targets_healthy"] is True
    assert checks["acceptance_scrapes_fresh"] is False
    assert checks["normal_2xx_delta_observed"] is False


def test_acceptance_event_delta_passes_same_build_with_all_new_events():
    before_values = {key: 10.0 for key in ACCEPTANCE_COUNTER_QUERIES}
    after_values = {key: 11.0 for key in ACCEPTANCE_COUNTER_QUERIES}

    checks = evaluate_acceptance_event_delta(
        _release_status(),
        _acceptance_snapshot(
            RELEASE_STARTED_AT + 60,
            counters=before_values,
        ),
        _acceptance_snapshot(
            RELEASE_STARTED_AT + 660,
            counters=after_values,
        ),
        expected_release_commit="3b151b6",
        expected_instance_count=1,
    )

    assert all(checks.values())


def test_acceptance_completion_guard_requires_stable_targets_and_counters():
    instances = ("instance-a", "instance-b")
    after_time = RELEASE_STARTED_AT + 90
    minimum_scrape_time = after_time + 28
    counters = {key: 10.0 for key in ACCEPTANCE_COUNTER_QUERIES}
    after = _acceptance_snapshot(
        after_time,
        counters=counters,
        instances=instances,
    )
    stable_guard = _acceptance_snapshot(
        after_time + 30,
        counters=counters,
        instances=instances,
        scrape_time=after_time + 29,
    )

    stable_checks = evaluate_acceptance_guard(
        _release_status(),
        after,
        stable_guard,
        expected_release_commit="3b151b6",
        expected_instance_count=2,
        minimum_scrape_time=minimum_scrape_time,
    )

    assert all(stable_checks.values())

    secondary_restarted = copy.deepcopy(stable_guard)
    for sample in secondary_restarted["samples"]["service_start_time_seconds"]:
        if sample["labels"]["instance"] == "instance-b":
            sample["value"] += 1
    restart_checks = evaluate_acceptance_guard(
        _release_status(),
        after,
        secondary_restarted,
        expected_release_commit="3b151b6",
        expected_instance_count=2,
        minimum_scrape_time=minimum_scrape_time,
    )
    assert restart_checks["acceptance_guard_release_stable"] is False

    extra_event = copy.deepcopy(stable_guard)
    extra_event["samples"]["known_4xx"][0]["value"] += 1
    counter_checks = evaluate_acceptance_guard(
        _release_status(),
        after,
        extra_event,
        expected_release_commit="3b151b6",
        expected_instance_count=2,
        minimum_scrape_time=minimum_scrape_time,
    )
    assert counter_checks["acceptance_guard_counters_stable"] is False

    equal_scrape = copy.deepcopy(stable_guard)
    for sample in equal_scrape["samples"]["scrape_time"]:
        sample["value"] = minimum_scrape_time
    scrape_checks = evaluate_acceptance_guard(
        _release_status(),
        after,
        equal_scrape,
        expected_release_commit="3b151b6",
        expected_instance_count=2,
        minimum_scrape_time=minimum_scrape_time,
    )
    assert scrape_checks["acceptance_guard_scrape_fresh"] is False

    pre_guard_scrape = copy.deepcopy(stable_guard)
    for sample in pre_guard_scrape["samples"]["scrape_time"]:
        sample["value"] = after_time + 20
    pre_guard_checks = evaluate_acceptance_guard(
        _release_status(),
        after,
        pre_guard_scrape,
        expected_release_commit="3b151b6",
        expected_instance_count=2,
        minimum_scrape_time=minimum_scrape_time,
    )
    assert pre_guard_checks["acceptance_guard_scrape_fresh"] is False


def test_final_acceptance_requires_event_visibility_in_the_longest_admin_window():
    assert ACCEPTANCE_EVENT_VISIBILITY_WINDOW_SECONDS == 3600
    assert _requires_event_visibility(3600, acceptance_final=True) is True
    assert _requires_event_visibility(300, acceptance_final=True) is False
    assert _requires_event_visibility(900, acceptance_final=True) is False
    assert _requires_event_visibility(3600, acceptance_final=False) is False


def test_final_admin_visibility_snapshot_must_follow_raw_counter_after():
    results = [_metric_result(key) for key in _queries(3600)]
    by_key = {result["key"]: result for result in results}
    for key, samples in _release_metric_samples().items():
        by_key[key]["samples"] = samples
    _add_acceptance_event_samples(by_key)
    _add_bulkhead_samples(by_key, ("instance-a",))
    historical_admin_checks = evaluate_admin_payloads(
        _release_status(),
        {
            "source": "prometheus",
            "window_seconds": 3600,
            "partial": False,
            "results": results,
        },
        window_seconds=3600,
        minimum_instances=1,
        require_acceptance_events=True,
        expected_release_commit="3b151b6",
    )
    before_time = RELEASE_STARTED_AT + 60
    after_time = before_time + 30
    delta_checks = evaluate_acceptance_event_delta(
        _release_status(),
        _acceptance_snapshot(
            before_time,
            counters={key: 10.0 for key in ACCEPTANCE_COUNTER_QUERIES},
        ),
        _acceptance_snapshot(
            after_time,
            counters={key: 11.0 for key in ACCEPTANCE_COUNTER_QUERIES},
        ),
        expected_release_commit="3b151b6",
        expected_instance_count=1,
    )
    windows = (300, 900, 3600)
    stale_payloads = {
        window: {"generated_at": "2023-11-14T22:14:40+00:00"}
        for window in windows
    }
    boundary_payloads = {
        window: {"generated_at": "2023-11-14T22:14:55+00:00"}
        for window in windows
    }
    fresh_payloads = {
        window: {"generated_at": "2023-11-14T22:14:56+00:00"}
        for window in windows
    }

    assert all(historical_admin_checks.values())
    assert all(delta_checks.values())
    assert (
        _admin_snapshots_follow_counter_snapshot(
            stale_payloads,
            windows,
            minimum_generated_at=after_time,
        )
        is False
    )
    assert (
        _admin_snapshots_follow_counter_snapshot(
            boundary_payloads,
            windows,
            minimum_generated_at=after_time,
        )
        is False
    )
    assert _admin_snapshots_follow_counter_snapshot(
        fresh_payloads,
        windows,
        minimum_generated_at=after_time,
    )


def test_final_admin_snapshot_capture_polls_past_cached_payloads(monkeypatch):
    windows = (300, 900, 3600)
    raw_after = RELEASE_STARTED_AT + 90
    stale = {
        window: {"generated_at": "2023-11-14T22:14:50+00:00"}
        for window in windows
    }
    fresh = {
        window: {"generated_at": "2023-11-14T22:14:56+00:00"}
        for window in windows
    }
    snapshots = [(_release_status(), stale), (_release_status(), fresh)]
    clock = [0.0]
    monkeypatch.setattr(
        "scripts.smoke_admin_metrics._read_admin_payloads",
        lambda *_args, **_kwargs: snapshots.pop(0),
    )

    status, payloads = _capture_admin_payloads_after(
        "http://admin.internal",
        username="viewer",
        password="secret",
        windows=windows,
        minimum_generated_at=raw_after,
        timeout_seconds=2,
        poll_seconds=1,
        now=lambda: clock[0],
        sleeper=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )

    assert status == _release_status()
    assert payloads == fresh
    assert clock[0] == 1


def test_final_admin_snapshot_capture_rejects_a_permanently_stale_cache(
    monkeypatch,
):
    windows = (300, 900, 3600)
    stale = {
        window: {"generated_at": "2023-11-14T22:14:50+00:00"}
        for window in windows
    }
    clock = [0.0]
    monkeypatch.setattr(
        "scripts.smoke_admin_metrics._read_admin_payloads",
        lambda *_args, **_kwargs: (_release_status(), stale),
    )

    with pytest.raises(ValueError, match="admin metrics snapshot capture timed out"):
        _capture_admin_payloads_after(
            "http://admin.internal",
            username="viewer",
            password="secret",
            windows=windows,
            minimum_generated_at=RELEASE_STARTED_AT + 90,
            timeout_seconds=2,
            poll_seconds=1,
            now=lambda: clock[0],
            sleeper=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        )

    assert clock[0] == 2


def test_admin_payload_reader_observes_status_after_all_metric_windows(monkeypatch):
    calls = []

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, path, *, params=None):
            calls.append((path, params))
            if path == "/admin-api/v1/status":
                return Response(_release_status())
            return Response({"generated_at": "2023-11-14T22:14:51+00:00"})

    monkeypatch.setattr(
        "scripts.smoke_admin_metrics.httpx.Client",
        lambda **_kwargs: Client(),
    )

    status, payloads = _read_admin_payloads(
        "http://admin.internal",
        username="viewer",
        password="secret",
        windows=(300, 900, 3600),
    )

    assert status == _release_status()
    assert set(payloads) == {300, 900, 3600}
    assert calls[-1] == ("/admin-api/v1/status", None)


def test_raw_counter_deltas_do_not_replace_dashboard_event_visibility():
    results = [_metric_result(key) for key in _queries(3600)]
    by_key = {result["key"]: result for result in results}
    for key, samples in _release_metric_samples().items():
        by_key[key]["samples"] = samples
    _add_bulkhead_samples(by_key, ("instance-a",))
    admin_checks = evaluate_admin_payloads(
        _release_status(),
        {
            "source": "prometheus",
            "window_seconds": 3600,
            "partial": False,
            "results": results,
        },
        window_seconds=3600,
        minimum_instances=1,
        require_acceptance_events=True,
        expected_release_commit="3b151b6",
    )
    before_values = {key: 10.0 for key in ACCEPTANCE_COUNTER_QUERIES}
    after_values = {key: 11.0 for key in ACCEPTANCE_COUNTER_QUERIES}
    delta_checks = evaluate_acceptance_event_delta(
        _release_status(),
        _acceptance_snapshot(
            RELEASE_STARTED_AT + 60,
            counters=before_values,
        ),
        _acceptance_snapshot(
            RELEASE_STARTED_AT + 660,
            counters=after_values,
        ),
        expected_release_commit="3b151b6",
        expected_instance_count=1,
    )

    assert all(delta_checks.values())
    assert admin_checks["normal_request_observed"] is False
    assert admin_checks["known_4xx_observed"] is False
    assert admin_checks["dependency_failure_observed"] is False
    assert admin_checks["global_bulkhead_rejection_observed"] is False
    assert admin_checks["heavy_search_bulkhead_rejection_observed"] is False
    assert all({**admin_checks, **delta_checks}.values()) is False


@pytest.mark.parametrize("elapsed_seconds", (29, 901))
def test_acceptance_event_delta_rejects_time_outside_the_reviewed_window(
    elapsed_seconds,
):
    before_values = {key: 10.0 for key in ACCEPTANCE_COUNTER_QUERIES}
    after_values = {key: 11.0 for key in ACCEPTANCE_COUNTER_QUERIES}

    checks = evaluate_acceptance_event_delta(
        _release_status(),
        _acceptance_snapshot(
            RELEASE_STARTED_AT + 60,
            counters=before_values,
        ),
        _acceptance_snapshot(
            RELEASE_STARTED_AT + 60 + elapsed_seconds,
            counters=after_values,
        ),
        expected_release_commit="3b151b6",
        expected_instance_count=1,
    )

    assert checks["acceptance_interval_bounded"] is False
    assert checks["normal_2xx_delta_observed"] is False


def test_smoke_does_not_treat_empty_available_queries_as_live_acceptance():
    results = [_metric_result(key) for key in _queries(300)]
    checks = evaluate_admin_payloads(
        {"metrics_source": "prometheus"},
        {
            "source": "prometheus",
            "window_seconds": 300,
            "partial": False,
            "results": results,
        },
        window_seconds=300,
        minimum_instances=1,
        require_acceptance_events=True,
    )

    assert checks["admin_metric_queries_available"] is True
    assert checks["admin_build_instances"] is False
    assert checks["normal_request_observed"] is False
    assert checks["latency_quantiles_observed"] is False
    assert checks["known_4xx_observed"] is False
    assert checks["dependency_failure_observed"] is False
    assert checks["global_bulkhead_rejection_observed"] is False
    assert checks["heavy_search_bulkhead_rejection_observed"] is False
    assert checks["bulkhead_instance_series_complete"] is False
    assert checks["bulkhead_aggregations_consistent"] is False


def test_smoke_does_not_treat_zero_preinitialized_series_as_events():
    results = [_metric_result(key) for key in _queries(300)]
    by_key = {result["key"]: result for result in results}
    by_key["request_rate"]["samples"] = [{"labels": {}, "value": 0.0}]
    by_key["success_rate"]["samples"] = [{"labels": {}, "value": 0.0}]
    for key in (
        "latency_p50_seconds",
        "latency_p95_seconds",
        "latency_p99_seconds",
    ):
        by_key[key]["samples"] = [{"labels": {}, "value": 0.0}]
    by_key["http_outcome_rate"]["samples"] = [
        {"labels": {"status": "400", "code": "40001"}, "value": 0.0}
    ]
    by_key["opensearch_call_rate"]["samples"] = [
        {
            "labels": {"operation": "search", "outcome": "timeout"},
            "value": 0.0,
        }
    ]
    by_key["bulkhead_rejections"]["samples"] = [
        {"labels": {"bulkhead": "global"}, "value": 0.0}
    ]
    by_key["build_info"]["samples"] = [
        {"labels": {"instance": "instance-a"}, "value": 1.0}
    ]
    _add_bulkhead_samples(by_key, ("instance-a",))

    checks = evaluate_admin_payloads(
        {"metrics_source": "prometheus"},
        {
            "source": "prometheus",
            "window_seconds": 300,
            "partial": False,
            "results": results,
        },
        window_seconds=300,
        minimum_instances=1,
        require_acceptance_events=True,
    )

    assert checks["latency_quantiles_observed"] is True
    assert checks["normal_request_observed"] is False
    assert checks["known_4xx_observed"] is False
    assert checks["dependency_failure_observed"] is False
    assert checks["global_bulkhead_rejection_observed"] is False
    assert checks["heavy_search_bulkhead_rejection_observed"] is False
    assert checks["bulkhead_instance_series_complete"] is True
    assert checks["bulkhead_aggregations_consistent"] is True


def test_smoke_rejects_invalid_success_ratio_and_quantile_order():
    results = [_metric_result(key) for key in _queries(300)]
    by_key = {result["key"]: result for result in results}
    by_key["success_rate"]["samples"] = [{"labels": {}, "value": 9.0}]
    by_key["latency_p50_seconds"]["samples"] = [
        {"labels": {}, "value": 99.0}
    ]
    by_key["latency_p95_seconds"]["samples"] = [
        {"labels": {}, "value": 1.0}
    ]
    by_key["latency_p99_seconds"]["samples"] = [
        {"labels": {}, "value": 0.0}
    ]

    checks = evaluate_admin_payloads(
        {"metrics_source": "prometheus"},
        {
            "source": "prometheus",
            "window_seconds": 300,
            "partial": False,
            "results": results,
        },
        window_seconds=300,
        minimum_instances=1,
        require_acceptance_events=False,
    )

    assert checks["success_rate_in_range"] is False
    assert checks["latency_quantiles_ordered"] is False


def test_smoke_compares_every_fixed_admin_query_with_direct_prometheus():
    results = [_metric_result(key) for key in _queries(300)]
    by_key = {result["key"]: result for result in results}
    by_key["request_rate"]["samples"] = [{"labels": {}, "value": 1.0}]
    admin_payload = {
        "source": "prometheus",
        "window_seconds": 300,
        "partial": False,
        "results": results,
    }
    direct_payload = copy.deepcopy(admin_payload)

    assert all(
        evaluate_admin_prometheus_comparison(
            admin_payload,
            direct_payload,
            window_seconds=300,
        ).values()
    )

    direct_by_key = {
        result["key"]: result for result in direct_payload["results"]
    }
    direct_by_key["request_rate"]["samples"][0]["value"] = 9.0
    mismatch = evaluate_admin_prometheus_comparison(
        admin_payload,
        direct_payload,
        window_seconds=300,
    )
    assert mismatch["direct_metric_queries_complete"] is True
    assert mismatch["admin_matches_direct_prometheus"] is False


def test_smoke_direct_queries_use_the_admin_snapshot_time():
    evaluation_time = "2026-08-20T05:00:00Z"
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_vector())

    with httpx.Client(
        base_url="http://prometheus.internal",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    ) as client:
        payloads = _read_direct_metrics(
            client,
            {300: {"generated_at": evaluation_time}},
            (300,),
        )

    assert len(calls) == len(_queries(300))
    assert all(request.url.params["time"] == evaluation_time for request in calls)
    assert payloads[300]["partial"] is False
    assert len(payloads[300]["results"]) == len(_queries(300))


def test_smoke_defaults_to_all_admin_windows():
    assert _selected_windows(None) == (300, 900, 3600)
    assert _selected_windows([900]) == (900,)
    assert _selected_windows([3600, 300, 300]) == (300, 3600)


def test_smoke_documented_module_entrypoint_is_runnable():
    completed = subprocess.run(
        [sys.executable, "-m", "scripts.smoke_admin_metrics", "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert "all supported windows" in completed.stdout


def test_smoke_rejects_incomplete_or_inconsistent_bulkhead_aggregation():
    results = [_metric_result(key) for key in _queries(300)]
    by_key = {result["key"]: result for result in results}
    by_key["build_info"]["samples"] = [
        {"labels": {"instance": "instance-a"}, "value": 1.0},
        {"labels": {"instance": "instance-b"}, "value": 1.0},
    ]
    _add_bulkhead_samples(by_key, ("instance-a", "instance-b"))
    by_key["bulkhead_capacity_total"]["samples"][0]["value"] += 1

    checks = evaluate_admin_payloads(
        {"metrics_source": "prometheus"},
        {
            "source": "prometheus",
            "window_seconds": 300,
            "partial": False,
            "results": results,
        },
        window_seconds=300,
        minimum_instances=2,
        require_acceptance_events=False,
    )

    assert checks["bulkhead_instance_series_complete"] is True
    assert checks["bulkhead_aggregations_consistent"] is False

    by_key["bulkhead_capacity_total"]["samples"][0]["value"] -= 1
    by_key["bulkhead_utilization_by_instance"]["samples"][0]["value"] += 0.1
    utilization_checks = evaluate_admin_payloads(
        {"metrics_source": "prometheus"},
        {
            "source": "prometheus",
            "window_seconds": 300,
            "partial": False,
            "results": results,
        },
        window_seconds=300,
        minimum_instances=2,
        require_acceptance_events=False,
    )
    assert utilization_checks["bulkhead_instance_series_complete"] is True
    assert utilization_checks["bulkhead_aggregations_consistent"] is False


def test_prometheus_smoke_requires_target_retention_and_storage_rule():
    rules = {
        "status": "success",
        "data": {
            "groups": [
                {
                    "rules": [
                        {
                            "name": "PatentSearchPrometheusStorageBudgetHigh",
                            "type": "alerting",
                            "health": "ok",
                            "lastError": "",
                        }
                    ]
                }
            ]
        },
    }

    assert all(
        evaluate_prometheus_payloads(
            _vector(),
            _vector(),
            _vector("2592000"),
            _vector("2147483648"),
            rules,
        ).values()
    )
    failed = evaluate_prometheus_payloads(
        _vector("0"),
        _vector("0"),
        _vector("0"),
        _vector("0"),
        {"status": "success", "data": {"groups": []}},
    )
    assert not any(failed.values())

    mixed_target_health = evaluate_prometheus_payloads(
        _vectors("1", "0"),
        _vector("1"),
        _vector("2592000"),
        _vector("2147483648"),
        rules,
        minimum_instances=2,
    )
    assert mixed_target_health["service_targets_up"] is False

    wrong_budget = evaluate_prometheus_payloads(
        _vectors("1", "1"),
        _vector("1"),
        _vector("1"),
        _vector("1"),
        rules,
        minimum_instances=2,
    )
    assert wrong_budget["retention_time_configured"] is False
    assert wrong_budget["retention_size_configured"] is False

    unhealthy_rule = {
        "status": "success",
        "data": {
            "groups": [
                {
                    "rules": [
                        {
                            "name": "PatentSearchPrometheusStorageBudgetHigh",
                            "type": "alerting",
                            "health": "err",
                            "lastError": "evaluation failed",
                        }
                    ]
                }
            ]
        },
    }
    unhealthy = evaluate_prometheus_payloads(
        _vector(),
        _vector(),
        _vector("2592000"),
        _vector("2147483648"),
        unhealthy_rule,
    )
    assert unhealthy["storage_alert_loaded"] is False


def test_prometheus_smoke_rejects_scrapes_over_the_measured_budget():
    healthy = evaluate_scrape_budget_payloads(
        _vectors("1000", "1000"),
        _vectors("13951", "13951"),
        _vectors("101", "101"),
        minimum_instances=2,
    )
    assert all(healthy.values())

    excessive = evaluate_scrape_budget_payloads(
        _vectors("1000", "1000"),
        _vectors("262145", "13951"),
        _vectors("1001", "101"),
        minimum_instances=2,
    )
    assert excessive["scrape_sample_limit_configured"] is True
    assert excessive["scrape_body_within_limit"] is False
    assert excessive["scrape_samples_within_limit"] is False

    wrong_limit = evaluate_scrape_budget_payloads(
        _vector("0"),
        _vector("13951"),
        _vector("101"),
        minimum_instances=1,
    )
    assert wrong_limit["scrape_sample_limit_configured"] is False


@pytest.mark.parametrize(
    ("percentage", "expected"),
    (
        (49, True),
        (50, False),
        (51, False),
    ),
)
def test_prometheus_smoke_enforces_half_limit_deployment_gate(
    percentage,
    expected,
):
    checks = evaluate_scrape_budget_payloads(
        _vector(str(EXPECTED_SCRAPE_SAMPLE_LIMIT)),
        _vector(str(EXPECTED_SCRAPE_BODY_SIZE_BYTES * percentage // 100)),
        _vector(str(EXPECTED_SCRAPE_SAMPLE_LIMIT * percentage // 100)),
        minimum_instances=1,
    )

    assert checks["scrape_body_within_limit"] is expected
    assert checks["scrape_samples_within_limit"] is expected


@pytest.mark.parametrize(
    ("body_size", "samples_scraped", "expected"),
    (
        (
            EXPECTED_SCRAPE_BODY_SIZE_BYTES // 2 - 1,
            EXPECTED_SCRAPE_SAMPLE_LIMIT // 2 - 1,
            True,
        ),
        (
            EXPECTED_SCRAPE_BODY_SIZE_BYTES // 2,
            EXPECTED_SCRAPE_SAMPLE_LIMIT // 2,
            False,
        ),
        (
            EXPECTED_SCRAPE_BODY_SIZE_BYTES // 2 + 1,
            EXPECTED_SCRAPE_SAMPLE_LIMIT // 2 + 1,
            False,
        ),
    ),
)
def test_prometheus_smoke_half_limit_adjacent_integer_boundaries(
    body_size,
    samples_scraped,
    expected,
):
    checks = evaluate_scrape_budget_payloads(
        _vector(str(EXPECTED_SCRAPE_SAMPLE_LIMIT)),
        _vector(str(body_size)),
        _vector(str(samples_scraped)),
        minimum_instances=1,
    )

    assert checks["scrape_body_within_limit"] is expected
    assert checks["scrape_samples_within_limit"] is expected


@pytest.mark.parametrize(
    "value",
    [
        "http://user:password@prometheus.internal",
        "ftp://prometheus.internal",
        "http://prometheus.internal?token=secret",
    ],
)
def test_smoke_rejects_urls_that_can_embed_credentials(value):
    with pytest.raises(ValueError, match="without credentials"):
        _bounded_base_url(value, "test_url")
