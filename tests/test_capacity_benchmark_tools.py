"""验证 Issue 34 压测工具的采样、护栏、容量决策和审计输出规则。"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
from threading import BoundedSemaphore, Event, Thread
from time import sleep

import httpx
import pytest

from benchmarks.capacity.benchmark_lib import (
    InFlightTracker,
    NODE_STATS_PATH,
    OpenSearchMetricsSampler,
    compare_capacity_tiers,
    evaluate_capacity_run,
    load_policy,
    normalize_opensearch_sample,
    percentile,
    summarize_bulkhead_log,
    summarize_http_records,
    summarize_opensearch_samples,
)
from benchmarks.capacity.mixed_workload import (
    MAX_MIXED_CLIENT_CONCURRENCY,
    MAX_MIXED_TOTAL_REQUESTS,
    evaluate_mixed_workload,
    heavy_specs,
    light_specs,
    overlap_count,
    parse_args as parse_mixed_args,
)
from benchmarks.capacity.overload_rejections import (
    MAX_OVERLOAD_CLIENT_CONCURRENCY,
    MAX_REJECTION_REQUESTS,
    evaluate_overload_acceptance,
    parse_args as parse_overload_args,
)
from benchmarks.capacity.replay_queries import (
    load_previous_summary,
    parse_args as parse_replay_args,
)


def test_percentile_uses_nearest_rank():
    values = [5.0, 1.0, 4.0, 2.0, 3.0]

    assert percentile(values, 0.50) == 3.0
    assert percentile(values, 0.95) == 5.0
    assert percentile([], 0.95) is None


def test_in_flight_tracker_distinguishes_current_and_peak():
    tracker = InFlightTracker()

    with tracker.slot():
        assert tracker.current == 1
        with tracker.slot():
            assert tracker.current == 2
            assert tracker.peak == 2
        assert tracker.current == 1

    assert tracker.current == 0
    assert tracker.peak == 2


def test_http_summary_counts_bulkhead_rejections_and_client_peak():
    records = [
        {"status": 200, "server_code": 0, "elapsed_seconds": 1.0, "client_error": None},
        {
            "status": 503,
            "server_code": 50301,
            "elapsed_seconds": 0.02,
            "client_error": None,
        },
        {
            "status": None,
            "server_code": None,
            "elapsed_seconds": 2.0,
            "client_error": "ReadTimeout",
        },
    ]

    summary = summarize_http_records(records, wall_seconds=3.0, client_peak_in_flight=3)

    assert summary["request_count"] == 3
    assert summary["failure_count"] == 2
    assert summary["failure_rate"] == 0.666667
    assert summary["client_peak_in_flight"] == 3
    assert summary["bulkhead_rejections"]["count"] == 1
    assert summary["bulkhead_rejections"]["latency_seconds"]["p99"] == 0.02


def _node_stats(
    *,
    cpu: int,
    heap: int,
    active: int,
    queue: int,
    rejected: int,
    breaker_tripped: int,
    cancellations: int,
):
    return {
        "name": "data-1",
        "roles": ["data"],
        "os": {"cpu": {"percent": cpu}},
        "jvm": {"mem": {"heap_used_percent": heap}},
        "thread_pool": {
            "search": {
                "active": active,
                "queue": queue,
                "rejected": rejected,
            }
        },
        "breakers": {"parent": {"tripped": breaker_tripped}},
        "search_backpressure": {
            "mode": "monitor_only",
            "search_task": {
                "cancellation_stats": {
                    "cancellation_count": cancellations,
                    "cancellation_limit_reached_count": 0,
                }
            },
            "search_shard_task": {
                "cancellation_stats": {
                    "cancellation_count": 0,
                    "cancellation_limit_reached_count": 0,
                }
            },
        },
    }


def test_opensearch_samples_capture_load_and_cumulative_counter_deltas():
    first = normalize_opensearch_sample(
        {
            "_nodes": {"failed": 0},
            "nodes": {
                "node-1": _node_stats(
                    cpu=10,
                    heap=40,
                    active=2,
                    queue=0,
                    rejected=5,
                    breaker_tripped=1,
                    cancellations=2,
                )
            },
        },
        sampled_at="first",
    )
    second = normalize_opensearch_sample(
        {
            "_nodes": {"failed": 0},
            "nodes": {
                "node-1": _node_stats(
                    cpu=70,
                    heap=60,
                    active=5,
                    queue=2,
                    rejected=6,
                    breaker_tripped=1,
                    cancellations=3,
                )
            },
        },
        sampled_at="second",
    )
    third = normalize_opensearch_sample(
        {
            "_nodes": {"failed": 0},
            "nodes": {
                "node-1": _node_stats(
                    cpu=30,
                    heap=50,
                    active=0,
                    queue=1,
                    rejected=6,
                    breaker_tripped=1,
                    cancellations=3,
                )
            },
        },
        sampled_at="third",
    )

    summary = summarize_opensearch_samples([first, second, third])

    assert summary["max_node_cpu_percent"] == 70.0
    assert summary["max_node_heap_percent"] == 60.0
    assert summary["max_search_active_total"] == 5
    assert summary["max_search_queue_total"] == 2
    assert summary["max_consecutive_search_queue_samples"] == 2
    assert summary["search_rejected_total_delta"] == 1
    assert summary["breaker_tripped_total_delta"] == 0
    assert summary["backpressure_cancellation_total_delta"] == 1
    assert summary["final_search_active_total"] == 0
    assert summary["final_search_queue_total"] == 1
    assert summary["node_set_changed"] is False


def test_empty_opensearch_nodes_are_a_hard_guardrail_failure():
    sample = normalize_opensearch_sample(
        {"_nodes": {"failed": 0}, "nodes": {}},
        sampled_at="empty",
    )

    summary = summarize_opensearch_samples([sample])
    decision = evaluate_capacity_run(
        _healthy_request_metrics(),
        summary,
        _policy(),
    )

    assert summary["successful_sample_count"] == 0
    assert summary["failed_sample_count"] == 1
    assert decision["safe_to_continue"] is False
    assert "metric_sample_failed" in decision["stop_reasons"]


def test_missing_critical_node_metric_is_a_hard_guardrail_failure():
    node = _node_stats(
        cpu=10,
        heap=40,
        active=0,
        queue=0,
        rejected=0,
        breaker_tripped=0,
        cancellations=0,
    )
    del node["os"]["cpu"]["percent"]
    sample = normalize_opensearch_sample(
        {"_nodes": {"failed": 0}, "nodes": {"node-1": node}},
        sampled_at="missing-cpu",
    )

    summary = summarize_opensearch_samples([sample])
    decision = evaluate_capacity_run(
        _healthy_request_metrics(),
        summary,
        _policy(),
    )

    assert summary["max_missing_required_metric_count"] == 1
    assert decision["safe_to_continue"] is False
    assert "required_opensearch_metric_missing" in decision["stop_reasons"]


def test_opensearch_sampler_preflights_and_persists_filtered_read_only_stats(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "_nodes": {"failed": 0},
                "nodes": {
                    "node-1": _node_stats(
                        cpu=10,
                        heap=40,
                        active=0,
                        queue=0,
                        rejected=0,
                        breaker_tripped=0,
                        cancellations=0,
                    )
                },
            },
        )

    metrics_path = tmp_path / "metrics.jsonl"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sampler = OpenSearchMetricsSampler(
            base_url="https://opensearch.example",
            output_path=metrics_path,
            interval_seconds=60,
            timeout_seconds=1,
            client=client,
        )
        sampler.start()
        sampler.stop()

    assert len(requests) == 2
    assert {request.url.path for request in requests} == {NODE_STATS_PATH}
    assert all("filter_path=" in str(request.url) for request in requests)
    persisted = [json.loads(line) for line in metrics_path.read_text().splitlines()]
    assert len(persisted) == 2
    assert persisted[0]["aggregate"]["search_active_total"] == 0


def test_opensearch_sampler_stop_fails_closed_if_background_fetch_is_still_running(
    tmp_path,
):
    background_entered = Event()
    release_background = Event()
    request_count = 0

    def handler(_request):
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(
                200,
                json={
                    "_nodes": {"failed": 0},
                    "nodes": {
                        "node-1": _node_stats(
                            cpu=10,
                            heap=40,
                            active=0,
                            queue=0,
                            rejected=0,
                            breaker_tripped=0,
                            cancellations=0,
                        )
                    },
                },
            )
        background_entered.set()
        if not release_background.wait(timeout=3):
            raise RuntimeError("test background release timed out")
        return httpx.Response(500)

    metrics_path = tmp_path / "metrics.jsonl"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sampler = OpenSearchMetricsSampler(
            base_url="https://opensearch.example",
            output_path=metrics_path,
            interval_seconds=0.01,
            timeout_seconds=0.01,
            client=client,
        )
        sampler.start()
        assert background_entered.wait(timeout=1)
        sampler.stop()

        immediate_summary = summarize_opensearch_samples(sampler.samples)
        persisted = [json.loads(line) for line in metrics_path.read_text().splitlines()]
        assert immediate_summary["failed_sample_count"] == 1
        assert any(
            "did not stop before summary" in sample.get("error", "")
            for sample in persisted
        )

        release_background.set()
        assert sampler._thread is not None
        sampler._thread.join(timeout=1)


def test_opensearch_sampler_refuses_to_start_when_preflight_has_no_nodes(tmp_path):
    def handler(_request):
        return httpx.Response(200, json={"_nodes": {"failed": 0}, "nodes": {}})

    metrics_path = tmp_path / "metrics.jsonl"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sampler = OpenSearchMetricsSampler(
            base_url="https://opensearch.example",
            output_path=metrics_path,
            interval_seconds=60,
            timeout_seconds=1,
            client=client,
        )
        with pytest.raises(ValueError, match="contains no nodes"):
            sampler.start()

    assert metrics_path.read_text() == ""


def test_opensearch_sampler_refuses_to_start_when_preflight_metrics_are_missing(
    tmp_path,
):
    node = _node_stats(
        cpu=10,
        heap=40,
        active=0,
        queue=0,
        rejected=0,
        breaker_tripped=0,
        cancellations=0,
    )
    del node["jvm"]["mem"]["heap_used_percent"]

    def handler(_request):
        return httpx.Response(
            200,
            json={"_nodes": {"failed": 0}, "nodes": {"node-1": node}},
        )

    metrics_path = tmp_path / "metrics.jsonl"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sampler = OpenSearchMetricsSampler(
            base_url="https://opensearch.example",
            output_path=metrics_path,
            interval_seconds=60,
            timeout_seconds=1,
            client=client,
        )
        with pytest.raises(ValueError, match="missing 1 required metrics"):
            sampler.start()

    assert metrics_path.read_text() == ""


def test_replay_cli_runs_one_safe_level_and_writes_auditable_outputs(tmp_path):
    metrics_preflight_delay_seconds = 0.6
    node_payload = {
        "_nodes": {"failed": 0},
        "nodes": {
            "node-1": _node_stats(
                cpu=10,
                heap=40,
                active=0,
                queue=0,
                rejected=0,
                breaker_tripped=0,
                cancellations=0,
            )
        },
    }

    class ServiceHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = json.dumps(
                {
                    "code": 0,
                    "total": 0,
                    "took_ms": 1,
                    "records": [],
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Request-ID", "test-request")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    class OpenSearchHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            sleep(metrics_preflight_delay_seconds)
            body = json.dumps(node_payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    service_server = ThreadingHTTPServer(("127.0.0.1", 0), ServiceHandler)
    opensearch_server = ThreadingHTTPServer(("127.0.0.1", 0), OpenSearchHandler)
    threads = [
        Thread(target=service_server.serve_forever, daemon=True),
        Thread(target=opensearch_server.serve_forever, daemon=True),
    ]
    for thread in threads:
        thread.start()

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "version": "test",
                "total_queries": 4,
                "queries": [
                    {"query": f"query-{index}", "channel": "test", "mode": "test"}
                    for index in range(4)
                ],
            }
        )
    )
    output_dir = tmp_path / "output"
    command = [
        sys.executable,
        "-m",
        "benchmarks.capacity.replay_queries",
        "--plan",
        str(plan_path),
        "--base-url",
        f"http://127.0.0.1:{service_server.server_port}",
        "--opensearch-url",
        f"http://127.0.0.1:{opensearch_server.server_port}",
        "--concurrency",
        "4",
        "--timeout",
        "5",
        "--output-dir",
        str(output_dir),
        "--post-observation-seconds",
        "0",
        "--service-version",
        "test",
        "--service-commit",
        "abc",
        "--read-target",
        "test-index",
        "--bulkhead-capacity",
        "10",
        "--heavy-bulkhead-capacity",
        "9",
        "--opensearch-pool-maxsize",
        "10",
        "--worker-count",
        "1",
    ]
    environment = {
        **os.environ,
        "API_TOKEN": "test-token",
        "OPENSEARCH_USER": "",
        "OPENSEARCH_PASS": "",
    }
    try:
        completed = subprocess.run(
            command,
            cwd=Path.cwd(),
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    finally:
        service_server.shutdown()
        opensearch_server.shutdown()
        service_server.server_close()
        opensearch_server.server_close()

    assert completed.returncode == 0, completed.stdout + completed.stderr
    summary_path = next(output_dir.glob("search_c4_*_summary.json"))
    summary = json.loads(summary_path.read_text())
    assert summary["request_metrics"]["success_count"] == 4
    assert summary["request_metrics"]["client_peak_in_flight"] >= 1
    assert summary["opensearch_metrics"]["failed_sample_count"] == 0
    assert summary["decision"]["safe_to_continue"] is True
    assert summary["run"]["heavy_bulkhead_capacity"] == 9
    assert summary["run"]["workload_started_at"] >= summary["run"]["started_at"]
    assert summary["request_metrics"]["wall_seconds"] < metrics_preflight_delay_seconds
    assert len(list(output_dir.glob("search_c4_*.jsonl"))) == 2


def test_replay_cli_rejects_search_concurrency_above_heavy_capacity(
    monkeypatch,
    capsys,
    tmp_path,
):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "replay_queries",
            "--base-url",
            "http://service",
            "--concurrency",
            "4",
            "--output-dir",
            str(tmp_path),
            "--opensearch-url",
            "http://opensearch",
            "--service-version",
            "test",
            "--service-commit",
            "abc",
            "--read-target",
            "index-v2",
            "--bulkhead-capacity",
            "4",
            "--heavy-bulkhead-capacity",
            "3",
            "--opensearch-pool-maxsize",
            "4",
            "--worker-count",
            "1",
        ],
    )

    with pytest.raises(SystemExit) as error:
        parse_replay_args()

    assert error.value.code == 2
    assert "must cover the offered search concurrency" in capsys.readouterr().err


def _policy():
    return {
        "application": {
            "max_failure_rate": 0.0,
            "max_bulkhead_rejections": 0,
            "rejection_p99_slo_seconds": 0.1,
        },
        "opensearch": {
            "max_failed_metric_samples": 0,
            "max_missing_required_metric_count": 0,
            "max_nodes_failed": 0,
            "max_node_cpu_percent": 80,
            "max_node_heap_percent": 65,
            "max_consecutive_search_queue_samples": 3,
            "max_search_rejected_delta": 0,
            "max_breaker_tripped_delta": 0,
            "max_backpressure_cancellation_delta": 0,
            "require_final_search_idle": True,
        },
        "knee": {
            "minimum_throughput_gain_ratio": 0.15,
            "maximum_tail_latency_growth_ratio": 0.3,
        },
        "mixed": {"max_light_p95_growth_ratio": 1.0},
    }


def _healthy_request_metrics():
    return {
        "failure_rate": 0.0,
        "throughput_requests_per_second": 1.0,
        "latency_seconds": {"p95": 10.0, "p99": 12.0},
        "bulkhead_rejections": {
            "count": 0,
            "latency_seconds": {"p99": None},
        },
    }


def _healthy_opensearch_metrics():
    return {
        "failed_sample_count": 0,
        "max_missing_required_metric_count": 0,
        "max_nodes_failed": 0,
        "max_node_cpu_percent": 50,
        "max_node_heap_percent": 50,
        "max_consecutive_search_queue_samples": 0,
        "search_rejected_total_delta": 0,
        "breaker_tripped_total_delta": 0,
        "backpressure_cancellation_total_delta": 0,
        "counter_reset_detected": False,
        "final_search_active_total": 0,
        "final_search_queue_total": 0,
    }


def test_capacity_decision_requires_every_guardrail_and_human_gate():
    decision = evaluate_capacity_run(
        _healthy_request_metrics(),
        _healthy_opensearch_metrics(),
        _policy(),
    )

    assert decision == {
        "safe_to_continue": True,
        "requires_human_approval_for_next_level": True,
        "stop_reasons": [],
        "comparison_to_previous_level": None,
    }

    overloaded = _healthy_opensearch_metrics()
    overloaded["max_node_cpu_percent"] = 81
    overloaded["search_rejected_total_delta"] = 1
    decision = evaluate_capacity_run(
        _healthy_request_metrics(),
        overloaded,
        _policy(),
    )
    assert decision["safe_to_continue"] is False
    assert decision["stop_reasons"] == [
        "opensearch_cpu_guardrail",
        "opensearch_search_rejection_observed",
    ]

    incomplete = _healthy_opensearch_metrics()
    incomplete["max_missing_required_metric_count"] = 1
    decision = evaluate_capacity_run(
        _healthy_request_metrics(),
        incomplete,
        _policy(),
    )
    assert decision["stop_reasons"] == ["required_opensearch_metric_missing"]


def test_capacity_comparison_detects_knee_before_next_level():
    previous = {
        "run": {"concurrency": 4},
        "request_metrics": {
            "throughput_requests_per_second": 1.0,
            "latency_seconds": {"p95": 10.0, "p99": 12.0},
        },
    }
    current = {
        "throughput_requests_per_second": 1.1,
        "latency_seconds": {"p95": 15.0, "p99": 18.0},
    }

    comparison = compare_capacity_tiers(previous, current, _policy()["knee"])

    assert comparison["throughput_gain_ratio"] == 0.1
    assert comparison["p95_growth_ratio"] == 0.5
    assert comparison["knee_detected"] is True


def test_next_level_requires_the_same_run_context_and_a_safe_previous_level(tmp_path):
    context = {
        "base_url": "http://service",
        "plan_sha256": "plan",
        "policy_sha256": "policy",
        "service_version": "0.10.0",
        "service_commit": "abc",
        "read_target": "index-v2",
        "bulkhead_capacity": 10,
        "heavy_bulkhead_capacity": 9,
        "opensearch_pool_maxsize": 10,
        "worker_count": 1,
    }
    previous_path = tmp_path / "c4.json"
    previous_path.write_text(
        json.dumps(
            {
                "run": {"concurrency": 4, **context},
                "decision": {"safe_to_continue": True},
            }
        )
    )

    assert load_previous_summary(previous_path, 6, context)["run"]["concurrency"] == 4

    with pytest.raises(ValueError, match="different read_target"):
        load_previous_summary(previous_path, 6, {**context, "read_target": "index-v3"})

    with pytest.raises(ValueError, match="different heavy_bulkhead_capacity"):
        load_previous_summary(
            previous_path,
            6,
            {**context, "heavy_bulkhead_capacity": 8},
        )


def test_next_level_can_only_override_a_previous_heap_guardrail(tmp_path):
    context = {
        "base_url": "http://service",
        "plan_sha256": "plan",
        "policy_sha256": "policy",
        "service_version": "0.10.0",
        "service_commit": "abc",
        "read_target": "index-v2",
        "bulkhead_capacity": 10,
        "heavy_bulkhead_capacity": 9,
        "opensearch_pool_maxsize": 10,
        "worker_count": 1,
    }
    previous_path = tmp_path / "c4.json"
    previous = {
        "run": {"concurrency": 4, **context},
        "decision": {
            "safe_to_continue": False,
            "stop_reasons": ["opensearch_heap_guardrail"],
        },
    }
    previous_path.write_text(json.dumps(previous))

    with pytest.raises(ValueError, match="not safe_to_continue"):
        load_previous_summary(previous_path, 6, context)

    assert (
        load_previous_summary(previous_path, 6, context, "operator accepted baseline")
        == previous
    )

    previous["decision"]["stop_reasons"].append("application_request_failed")
    previous_path.write_text(json.dumps(previous))
    with pytest.raises(ValueError, match="only permits"):
        load_previous_summary(previous_path, 6, context, "operator accepted baseline")


def test_bulkhead_log_summary_uses_server_side_events():
    summary = summarize_bulkhead_log(
        iter(
            [
                "INFO Application bulkhead event=in_flight in_flight=1 "
                "peak_in_flight=1 rejected_total=0 capacity=4\n",
                "WARN Application bulkhead event=rejected in_flight=4 "
                "peak_in_flight=4 rejected_total=1 capacity=4\n",
                "INFO Application bulkhead event=in_flight in_flight=0 "
                "peak_in_flight=4 rejected_total=1 capacity=4\n",
            ]
        )
    )

    assert summary["rejected_event_count"] == 1
    assert summary["max_in_flight"] == 4
    assert summary["max_peak_in_flight"] == 4
    assert summary["capacities"] == [4]
    assert summary["final_in_flight"] == 0
    assert summary["bulkheads"]["unlabeled"]["max_in_flight"] == 4


def test_bulkhead_log_summary_separates_global_and_heavy_limits():
    summary = summarize_bulkhead_log(
        iter(
            [
                "INFO Application bulkhead event=in_flight in_flight=1 "
                "peak_in_flight=1 rejected_total=0 capacity=3 name=heavy_search "
                "acquire_timeout_seconds=0.01\n",
                "INFO Application bulkhead event=in_flight in_flight=1 "
                "peak_in_flight=1 rejected_total=0 capacity=4 name=global "
                "acquire_timeout_seconds=0.01\n",
                "WARN Application bulkhead event=rejected in_flight=3 "
                "peak_in_flight=3 rejected_total=1 capacity=3 name=heavy_search "
                "acquire_timeout_seconds=0.01\n",
            ]
        )
    )

    assert summary["bulkheads"]["global"]["capacities"] == [4]
    assert summary["bulkheads"]["heavy_search"]["capacities"] == [3]
    assert summary["bulkheads"]["heavy_search"]["rejected_event_count"] == 1
    assert summary["bulkheads"]["global"]["acquire_timeout_seconds"] == [0.01]
    assert summary["bulkheads"]["heavy_search"]["acquire_timeout_seconds"] == [0.01]


def test_bulkhead_log_summary_accepts_current_json_and_legacy_lines():
    summary = summarize_bulkhead_log(
        iter(
            [
                '{"event":"in_flight","request_id":"request-1",'
                '"name":"global","in_flight":1,"peak_in_flight":1,'
                '"rejected_total":0,"capacity":4,'
                '"acquire_timeout_seconds":0.01}\n',
                "WARN Application bulkhead event=rejected in_flight=4 "
                "peak_in_flight=4 rejected_total=1 capacity=4 name=global "
                "acquire_timeout_seconds=0.01\n",
            ]
        )
    )

    assert summary["event_count"] == 2
    assert summary["rejected_event_count"] == 1
    assert summary["bulkheads"]["global"]["final_in_flight"] == 4


def test_bulkhead_log_summary_parses_scientific_timeout_without_partial_matches():
    summary = summarize_bulkhead_log(
        iter(
            [
                "INFO Application bulkhead event=rejected in_flight=1 "
                "peak_in_flight=1 rejected_total=1 capacity=1 name=global "
                "acquire_timeout_seconds=1e-06\n"
            ]
        )
    )

    assert summary["acquire_timeout_seconds"] == [1e-06]
    assert summary["bulkheads"]["global"]["acquire_timeout_seconds"] == [1e-06]

    malformed = summarize_bulkhead_log(
        iter(
            [
                "INFO Application bulkhead event=rejected in_flight=1 "
                "peak_in_flight=1 rejected_total=1 capacity=1 name=global "
                "acquire_timeout_seconds=1e-06broken\n"
            ]
        )
    )
    assert malformed["error"] == "no_bulkhead_events_found"


def test_mixed_workload_builds_three_light_endpoints_and_measures_overlap():
    specs = light_specs("CN 1/A", repetitions=2)
    heavy = heavy_specs(
        {
            "queries": [
                {"query": "q", "channel": "bqp", "max_patents": 50},
            ]
        },
        concurrency=2,
    )

    assert len(specs) == 6
    assert {spec["name"] for spec in specs} == {
        "detail",
        "citations",
        "legal_history",
    }
    assert all("CN%201%2FA" in spec["path"] for spec in specs)
    assert len(heavy) == 2
    assert all(spec["path"] == "/api/patent/search" for spec in heavy)
    assert overlap_count(
        [{"started_offset_seconds": 1.0, "finished_offset_seconds": 2.0}],
        [{"started_offset_seconds": 0.5, "finished_offset_seconds": 1.5}],
    ) == 1


def test_mixed_workload_requires_isolation_for_failed_or_starved_light_requests():
    baseline = {
        "request_count": 3,
        "failure_count": 0,
        "latency_seconds": {"p95": 0.1},
    }
    mixed = {
        "request_count": 3,
        "failure_count": 1,
        "latency_seconds": {"p95": 0.25},
    }
    decision = evaluate_mixed_workload(
        baseline,
        mixed,
        {"safe_to_continue": False},
        confirmed_overlap=3,
        policy=_policy(),
    )

    assert decision["safe_to_release_shared_bulkhead"] is False
    assert decision["requires_workload_isolation"] is True
    assert decision["light_p95_growth_ratio"] == 1.5
    assert decision["workload_stop_reasons"] == [
        "overlapped_light_request_failed",
        "light_p95_growth_exceeded",
        "application_or_opensearch_guardrail_failed",
    ]


def test_mixed_workload_rejects_light_concurrency_above_reserved_capacity(
    monkeypatch,
    capsys,
    tmp_path,
):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mixed_workload",
            "--base-url",
            "http://service",
            "--patent-id",
            "patent-1",
            "--heavy-concurrency",
            "5",
            "--light-concurrency",
            "3",
            "--output-dir",
            str(tmp_path),
            "--opensearch-url",
            "http://opensearch",
            "--service-version",
            "test",
            "--service-commit",
            "abc",
            "--read-target",
            "index-v2",
            "--bulkhead-capacity",
            "6",
            "--heavy-bulkhead-capacity",
            "5",
            "--opensearch-pool-maxsize",
            "10",
            "--worker-count",
            "1",
        ],
    )

    with pytest.raises(SystemExit) as error:
        parse_mixed_args()

    assert error.value.code == 2
    assert "cannot exceed reserved light capacity" in capsys.readouterr().err


@pytest.mark.parametrize(
    (
        "heavy_concurrency",
        "light_concurrency",
        "light_repetitions",
        "global_capacity",
        "heavy_capacity",
        "timeout_seconds",
        "message",
    ),
    [
        (
            MAX_MIXED_CLIENT_CONCURRENCY,
            1,
            1,
            MAX_MIXED_CLIENT_CONCURRENCY + 1,
            MAX_MIXED_CLIENT_CONCURRENCY,
            300,
            "controlled client concurrency limit",
        ),
        (
            1,
            1,
            MAX_MIXED_TOTAL_REQUESTS // 6 + 1,
            2,
            1,
            300,
            "planned baseline and mixed service requests",
        ),
        (1, 1, 1, 2, 1, 0, "timeouts and metrics interval must be positive"),
    ],
)
def test_mixed_workload_rejects_unsafe_load_generator_parameters(
    monkeypatch,
    capsys,
    tmp_path,
    heavy_concurrency,
    light_concurrency,
    light_repetitions,
    global_capacity,
    heavy_capacity,
    timeout_seconds,
    message,
):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mixed_workload",
            "--base-url",
            "http://service",
            "--patent-id",
            "patent-1",
            "--heavy-concurrency",
            str(heavy_concurrency),
            "--light-concurrency",
            str(light_concurrency),
            "--light-repetitions",
            str(light_repetitions),
            "--timeout",
            str(timeout_seconds),
            "--output-dir",
            str(tmp_path),
            "--opensearch-url",
            "http://opensearch",
            "--service-version",
            "test",
            "--service-commit",
            "abc",
            "--read-target",
            "index-v2",
            "--bulkhead-capacity",
            str(global_capacity),
            "--heavy-bulkhead-capacity",
            str(heavy_capacity),
            "--opensearch-pool-maxsize",
            str(global_capacity),
            "--worker-count",
            "1",
        ],
    )

    with pytest.raises(SystemExit) as error:
        parse_mixed_args()

    assert error.value.code == 2
    assert message in capsys.readouterr().err


def _overload_record(
    *,
    status: int,
    started: float,
    finished: float,
    server_code: int = 0,
    retryable: bool | None = None,
    retry_after: str | None = None,
    header_request_id: str | None = "request-id",
    body_request_id: str | None = "request-id",
):
    return {
        "status": status,
        "client_error": None,
        "server_code": server_code,
        "server_retryable": retryable,
        "retry_after": retry_after,
        "response_header_request_id": header_request_id,
        "response_body_request_id": body_request_id,
        "elapsed_seconds": round(finished - started, 3),
        "started_offset_seconds": started,
        "finished_offset_seconds": finished,
    }


def test_overload_acceptance_requires_real_heavy_and_global_fast_rejections():
    heavy_holders = [
        _overload_record(status=200, started=0.0, finished=1.0)
        for _ in range(3)
    ]
    heavy_overload = [
        _overload_record(
            status=503,
            server_code=50301,
            retryable=True,
            retry_after="1",
            started=0.1,
            finished=0.12,
        )
        for _ in range(8)
    ]
    global_holders = [
        _overload_record(status=200, started=2.0, finished=3.0)
        for _ in range(4)
    ]
    global_overload = [
        _overload_record(
            status=503,
            server_code=50301,
            retryable=True,
            retry_after="1",
            started=2.1,
            finished=2.13,
        )
        for _ in range(8)
    ]

    decision = evaluate_overload_acceptance(
        heavy_holder_records=heavy_holders,
        heavy_overload_records=heavy_overload,
        global_holder_records=global_holders,
        global_overload_records=global_overload,
        guardrail_decision={"safe_to_continue": True, "stop_reasons": []},
        policy=_policy(),
        deployed_acquire_timeout_seconds=0.01,
    )

    assert decision["http_rejection_stage_passed"] is True
    assert decision["heavy"]["rejection_contract_count"] == 8
    assert decision["global"]["rejection_contract_count"] == 8
    assert decision["heavy"]["rejection_latency_seconds"]["p99"] == 0.02
    assert decision["global"]["rejection_latency_seconds"]["p99"] == 0.03
    assert decision["requires_server_log_confirmation"] is True

    heavy_overload[0]["retry_after"] = "2"
    failed = evaluate_overload_acceptance(
        heavy_holder_records=heavy_holders,
        heavy_overload_records=heavy_overload,
        global_holder_records=global_holders,
        global_overload_records=global_overload,
        guardrail_decision={"safe_to_continue": True, "stop_reasons": []},
        policy=_policy(),
        deployed_acquire_timeout_seconds=0.1,
    )
    assert failed["http_rejection_stage_passed"] is False
    assert failed["stop_reasons"] == [
        "heavy_overload_not_all_50301",
        "configured_acquire_timeout_has_no_rejection_slo_headroom",
    ]


def test_overload_acceptance_requires_matching_header_and_body_request_ids():
    holders = [
        _overload_record(status=200, started=0.0, finished=1.0)
        for _ in range(2)
    ]
    missing_ids = [
        _overload_record(
            status=503,
            server_code=50301,
            retryable=True,
            retry_after="1",
            header_request_id=None,
            body_request_id=None,
            started=0.1,
            finished=0.12,
        )
    ]
    mismatched_ids = [
        _overload_record(
            status=503,
            server_code=50301,
            retryable=True,
            retry_after="1",
            header_request_id="header-id",
            body_request_id="body-id",
            started=0.1,
            finished=0.12,
        )
    ]

    missing = evaluate_overload_acceptance(
        heavy_holder_records=holders,
        heavy_overload_records=missing_ids,
        global_holder_records=holders,
        global_overload_records=missing_ids,
        guardrail_decision={"safe_to_continue": True},
        policy=_policy(),
        deployed_acquire_timeout_seconds=0.01,
    )
    assert missing["http_rejection_stage_passed"] is False
    assert missing["stop_reasons"] == [
        "heavy_overload_not_all_50301",
        "global_overload_not_all_50301",
    ]

    mismatched = evaluate_overload_acceptance(
        heavy_holder_records=holders,
        heavy_overload_records=mismatched_ids,
        global_holder_records=holders,
        global_overload_records=mismatched_ids,
        guardrail_decision={"safe_to_continue": True},
        policy=_policy(),
        deployed_acquire_timeout_seconds=0.01,
    )
    assert mismatched["http_rejection_stage_passed"] is False
    assert mismatched["stop_reasons"] == [
        "heavy_overload_not_all_50301",
        "global_overload_not_all_50301",
    ]


@pytest.mark.parametrize(
    ("global_capacity", "heavy_capacity", "rejection_requests", "message"),
    [
        (10, 9, MAX_REJECTION_REQUESTS + 1, "must be between 1 and"),
        (
            MAX_OVERLOAD_CLIENT_CONCURRENCY - MAX_REJECTION_REQUESTS + 1,
            MAX_OVERLOAD_CLIENT_CONCURRENCY - MAX_REJECTION_REQUESTS,
            MAX_REJECTION_REQUESTS,
            "controlled client concurrency limit",
        ),
    ],
)
def test_overload_cli_rejects_unsafe_client_fanout(
    monkeypatch,
    capsys,
    tmp_path,
    global_capacity,
    heavy_capacity,
    rejection_requests,
    message,
):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "overload_rejections",
            "--base-url",
            "http://service",
            "--patent-id",
            "patent-1",
            "--rejection-requests",
            str(rejection_requests),
            "--output-dir",
            str(tmp_path),
            "--opensearch-url",
            "http://opensearch",
            "--service-version",
            "test",
            "--service-commit",
            "abc",
            "--read-target",
            "index-v2",
            "--bulkhead-capacity",
            str(global_capacity),
            "--heavy-bulkhead-capacity",
            str(heavy_capacity),
            "--deployed-acquire-timeout-seconds",
            "0.01",
            "--opensearch-pool-maxsize",
            str(global_capacity),
            "--worker-count",
            "1",
        ],
    )

    with pytest.raises(SystemExit) as error:
        parse_overload_args()

    assert error.value.code == 2
    assert message in capsys.readouterr().err


def test_overload_cli_measures_both_real_rejection_boundaries(tmp_path):
    global_slots = BoundedSemaphore(2)
    heavy_slots = BoundedSemaphore(1)
    node_payload = {
        "_nodes": {"failed": 0},
        "nodes": {
            "node-1": _node_stats(
                cpu=10,
                heap=40,
                active=0,
                queue=0,
                rejected=0,
                breaker_tripped=0,
                cancellations=0,
            )
        },
    }

    class ServiceHandler(BaseHTTPRequestHandler):
        def _send_json(self, status, payload, *, retry_after=None):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Request-ID", "overload-test")
            if retry_after is not None:
                self.send_header("Retry-After", retry_after)
            self.end_headers()
            self.wfile.write(body)

        def _reject(self):
            self._send_json(
                503,
                {
                    "success": False,
                    "code": 50301,
                    "message": "busy",
                    "retryable": True,
                    "request_id": "overload-test",
                },
                retry_after="1",
            )

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if not heavy_slots.acquire(timeout=0.01):
                self._reject()
                return
            global_acquired = False
            try:
                global_acquired = global_slots.acquire(timeout=0.01)
                if not global_acquired:
                    self._reject()
                    return
                sleep(0.25)
                self._send_json(200, {"code": 0, "records": [], "total": 0})
            finally:
                if global_acquired:
                    global_slots.release()
                heavy_slots.release()

        def do_GET(self):
            if not global_slots.acquire(timeout=0.01):
                self._reject()
                return
            try:
                sleep(0.25)
                self._send_json(200, {"code": 0, "id": "patent-1"})
            finally:
                global_slots.release()

        def log_message(self, *_args):
            pass

    class OpenSearchHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(node_payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    service_server = ThreadingHTTPServer(("127.0.0.1", 0), ServiceHandler)
    opensearch_server = ThreadingHTTPServer(("127.0.0.1", 0), OpenSearchHandler)
    threads = [
        Thread(target=service_server.serve_forever, daemon=True),
        Thread(target=opensearch_server.serve_forever, daemon=True),
    ]
    for thread in threads:
        thread.start()

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "version": "test",
                "total_queries": 1,
                "queries": [
                    {"query": "broad query", "channel": "broad", "mode": "test"}
                ],
            }
        )
    )
    policy = {"version": "test", "levels": [4, 6, 8, 10], **_policy()}
    policy["application"]["rejection_p99_slo_seconds"] = 0.5
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy))
    output_dir = tmp_path / "output"
    command = [
        sys.executable,
        "-m",
        "benchmarks.capacity.overload_rejections",
        "--plan",
        str(plan_path),
        "--policy",
        str(policy_path),
        "--base-url",
        f"http://127.0.0.1:{service_server.server_port}",
        "--patent-id",
        "patent-1",
        "--rejection-requests",
        "2",
        "--holder-settle-seconds",
        "0.05",
        "--phase-gap-seconds",
        "0",
        "--timeout",
        "2",
        "--output-dir",
        str(output_dir),
        "--opensearch-url",
        f"http://127.0.0.1:{opensearch_server.server_port}",
        "--metrics-interval",
        "0.05",
        "--metrics-timeout",
        "1",
        "--post-observation-seconds",
        "0",
        "--service-version",
        "test",
        "--service-commit",
        "abc",
        "--read-target",
        "test-index",
        "--bulkhead-capacity",
        "2",
        "--heavy-bulkhead-capacity",
        "1",
        "--deployed-acquire-timeout-seconds",
        "0.01",
        "--opensearch-pool-maxsize",
        "2",
        "--worker-count",
        "1",
    ]
    environment = {
        **os.environ,
        "API_TOKEN": "test-token",
        "OPENSEARCH_USER": "",
        "OPENSEARCH_PASS": "",
    }
    try:
        completed = subprocess.run(
            command,
            cwd=Path.cwd(),
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    finally:
        service_server.shutdown()
        opensearch_server.shutdown()
        service_server.server_close()
        opensearch_server.server_close()

    assert completed.returncode == 0, completed.stdout + completed.stderr
    summary_path = next(output_dir.glob("overload_*_summary.json"))
    summary = json.loads(summary_path.read_text())
    assert summary["decision"]["http_rejection_stage_passed"] is True
    assert summary["decision"]["heavy"]["rejection_contract_count"] == 2
    assert summary["decision"]["global"]["rejection_contract_count"] == 2
    assert summary["run"]["deployed_acquire_timeout_seconds"] == 0.01
    assert summary["application_bulkhead_log"]["status"] == "pending_operator_export"


def test_capacity_policy_file_is_valid_json():
    with open("benchmarks/capacity/capacity_policy.json", encoding="utf-8") as handle:
        policy = json.load(handle)

    assert policy["levels"] == [4, 6, 8, 10]
    assert policy["application"]["rejection_p99_slo_seconds"] == 0.1
    assert load_policy(Path("benchmarks/capacity/capacity_policy.json"))[0] == policy


def test_capacity_policy_rejects_a_different_ladder(tmp_path):
    policy = json.loads(Path("benchmarks/capacity/capacity_policy.json").read_text())
    policy["levels"] = [4, 8]
    path = tmp_path / "invalid-policy.json"
    path.write_text(json.dumps(policy))

    with pytest.raises(ValueError, match="exactly 4, 6, 8, 10"):
        load_policy(path)
