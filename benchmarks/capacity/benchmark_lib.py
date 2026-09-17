"""Issue 34 压测工具共享的只读采样、汇总、护栏和结果持久化辅助函数。

该模块描述客户端并发、OpenSearch 节点样本和服务端日志三类不同证据，避免压测
脚本将它们混为同一个“在途请求”指标；它不负责修改生产集群状态。
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from threading import Condition, Event, Lock, Thread
from typing import Any, Iterator

import httpx


NODE_STATS_PATH = "/_nodes/stats/os,jvm,thread_pool,breaker,search_backpressure"
NODE_STATS_FILTER_PATH = ",".join(
    (
        "_nodes.failed",
        "nodes.*.name",
        "nodes.*.roles",
        "nodes.*.os.cpu.percent",
        "nodes.*.jvm.mem.heap_used_percent",
        "nodes.*.thread_pool.search.active",
        "nodes.*.thread_pool.search.queue",
        "nodes.*.thread_pool.search.rejected",
        "nodes.*.breakers.*.tripped",
        "nodes.*.search_backpressure.mode",
        "nodes.*.search_backpressure.search_task.cancellation_stats.*",
        "nodes.*.search_backpressure.search_shard_task.cancellation_stats.*",
    )
)
LOG_FLOAT_PATTERN = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
BULKHEAD_LOG_PATTERN = re.compile(
    r"Application bulkhead event=(?P<event>\w+) "
    r"in_flight=(?P<in_flight>\d+) "
    r"peak_in_flight=(?P<peak_in_flight>\d+) "
    r"rejected_total=(?P<rejected_total>\d+) "
    r"capacity=(?P<capacity>\d+)"
    r"(?: name=(?P<name>[\w-]+))?"
    rf"(?: acquire_timeout_seconds=(?P<acquire_timeout_seconds>{LOG_FLOAT_PATTERN}))?"
    r"\s*$"
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return round(ordered[index], 3)


def latency_summary(values: list[float]) -> dict[str, float | None]:
    return {
        "min": min(values, default=None),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values, default=None),
    }


class InFlightTracker:
    """Track client-side concurrency without claiming it is server in-flight."""

    def __init__(self) -> None:
        self._condition = Condition(Lock())
        self._current = 0
        self._peak = 0

    @property
    def current(self) -> int:
        with self._condition:
            return self._current

    @property
    def peak(self) -> int:
        with self._condition:
            return self._peak

    def wait_until_peak_at_least(self, value: int, timeout: float) -> bool:
        with self._condition:
            return self._condition.wait_for(lambda: self._peak >= value, timeout)

    @contextmanager
    def slot(self) -> Iterator[None]:
        with self._condition:
            self._current += 1
            self._peak = max(self._peak, self._current)
            self._condition.notify_all()
        try:
            yield
        finally:
            with self._condition:
                self._current -= 1
                self._condition.notify_all()


def summarize_http_records(
    records: list[dict[str, Any]],
    wall_seconds: float,
    client_peak_in_flight: int,
) -> dict[str, Any]:
    elapsed = [float(record["elapsed_seconds"]) for record in records]
    rejections = [record for record in records if record.get("server_code") == 50301]
    statuses = Counter(
        str(record["status"]) if record.get("status") is not None else "client_error"
        for record in records
    )
    server_codes = Counter(
        str(record["server_code"])
        for record in records
        if record.get("server_code") is not None
    )
    failure_count = sum(record.get("status") != 200 for record in records)
    request_count = len(records)
    return {
        "request_count": request_count,
        "success_count": request_count - failure_count,
        "failure_count": failure_count,
        "failure_rate": round(failure_count / request_count, 6) if request_count else 0.0,
        "client_error_count": sum(bool(record.get("client_error")) for record in records),
        "status_counts": dict(statuses),
        "server_code_counts": dict(server_codes),
        "wall_seconds": round(wall_seconds, 3),
        "throughput_requests_per_second": (
            round(request_count / wall_seconds, 3) if wall_seconds > 0 else None
        ),
        "client_peak_in_flight": client_peak_in_flight,
        "latency_seconds": latency_summary(elapsed),
        "bulkhead_rejections": {
            "count": len(rejections),
            "rate": round(len(rejections) / request_count, 6) if request_count else 0.0,
            "latency_seconds": latency_summary(
                [float(record["elapsed_seconds"]) for record in rejections]
            ),
        },
    }


def _int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _optional_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _backpressure_cancellations(search_backpressure: dict[str, Any]) -> tuple[int, int]:
    cancellations = 0
    limits_reached = 0
    for task_name in ("search_task", "search_shard_task"):
        task = search_backpressure.get(task_name, {})
        cancellation_stats = task.get("cancellation_stats", {})
        cancellations += _int(cancellation_stats.get("cancellation_count"))
        limits_reached += _int(
            cancellation_stats.get("cancellation_limit_reached_count")
        )
    return cancellations, limits_reached


def normalize_opensearch_sample(
    payload: dict[str, Any], sampled_at: str | None = None
) -> dict[str, Any]:
    normalized_nodes: dict[str, Any] = {}
    for node_id, node in payload.get("nodes", {}).items():
        search_pool = node.get("thread_pool", {}).get("search", {})
        breakers = node.get("breakers", {})
        backpressure = node.get("search_backpressure", {})
        cpu_value = node.get("os", {}).get("cpu", {}).get("percent")
        heap_value = node.get("jvm", {}).get("mem", {}).get("heap_used_percent")
        missing_metrics = []
        if not isinstance(cpu_value, (int, float)):
            missing_metrics.append("os.cpu.percent")
        if not isinstance(heap_value, (int, float)):
            missing_metrics.append("jvm.mem.heap_used_percent")
        for key in ("active", "queue", "rejected"):
            if not isinstance(search_pool.get(key), (int, float)):
                missing_metrics.append(f"thread_pool.search.{key}")
        if not breakers:
            missing_metrics.append("breakers")
        if backpressure.get("mode") is None:
            missing_metrics.append("search_backpressure.mode")
        for task_name in ("search_task", "search_shard_task"):
            cancellation_stats = backpressure.get(task_name, {}).get(
                "cancellation_stats", {}
            )
            if not isinstance(
                cancellation_stats.get("cancellation_count"), (int, float)
            ):
                missing_metrics.append(
                    f"search_backpressure.{task_name}.cancellation_count"
                )
        cancellations, limits_reached = _backpressure_cancellations(backpressure)
        normalized_nodes[node_id] = {
            "name": node.get("name"),
            "roles": node.get("roles", []),
            "os_cpu_percent": _optional_float(cpu_value),
            "heap_used_percent": _optional_float(heap_value),
            "search_active": _int(search_pool.get("active")),
            "search_queue": _int(search_pool.get("queue")),
            "search_rejected_total": _int(search_pool.get("rejected")),
            "breaker_tripped_total": sum(
                _int(breaker.get("tripped")) for breaker in breakers.values()
            ),
            "backpressure_mode": backpressure.get("mode"),
            "backpressure_cancellation_total": cancellations,
            "backpressure_limit_reached_total": limits_reached,
            "missing_metrics": missing_metrics,
        }

    nodes = list(normalized_nodes.values())
    return {
        "sampled_at": sampled_at or now_iso(),
        "nodes_failed": _int(payload.get("_nodes", {}).get("failed")),
        "node_count": len(nodes),
        "nodes": normalized_nodes,
        "aggregate": {
            "search_active_total": sum(node["search_active"] for node in nodes),
            "search_queue_total": sum(node["search_queue"] for node in nodes),
            "search_rejected_total": sum(
                node["search_rejected_total"] for node in nodes
            ),
            "breaker_tripped_total": sum(
                node["breaker_tripped_total"] for node in nodes
            ),
            "backpressure_cancellation_total": sum(
                node["backpressure_cancellation_total"] for node in nodes
            ),
            "backpressure_limit_reached_total": sum(
                node["backpressure_limit_reached_total"] for node in nodes
            ),
            "missing_required_metric_count": sum(
                len(node["missing_metrics"]) for node in nodes
            ),
        },
    }


def _counter_delta(first: dict[str, Any], last: dict[str, Any], key: str) -> tuple[int, bool]:
    first_nodes = first.get("nodes", {})
    last_nodes = last.get("nodes", {})
    shared_node_ids = first_nodes.keys() & last_nodes.keys()
    delta = 0
    reset = False
    for node_id in shared_node_ids:
        first_value = _int(first_nodes[node_id].get(key))
        last_value = _int(last_nodes[node_id].get(key))
        delta += max(0, last_value - first_value)
        reset = reset or last_value < first_value
    return delta, reset


def summarize_opensearch_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    def is_successful(sample: dict[str, Any]) -> bool:
        nodes = sample.get("nodes")
        return (
            "error" not in sample
            and isinstance(nodes, dict)
            and bool(nodes)
            and _int(sample.get("node_count")) > 0
        )

    successful = [sample for sample in samples if is_successful(sample)]
    failed = [sample for sample in samples if not is_successful(sample)]
    if not successful:
        return {
            "sample_count": len(samples),
            "successful_sample_count": 0,
            "failed_sample_count": len(failed),
            "counter_reset_detected": False,
        }

    node_values = [
        node
        for sample in successful
        for node in sample.get("nodes", {}).values()
    ]
    longest_queue_run = 0
    current_queue_run = 0
    for sample in successful:
        if _int(sample.get("aggregate", {}).get("search_queue_total")) > 0:
            current_queue_run += 1
            longest_queue_run = max(longest_queue_run, current_queue_run)
        else:
            current_queue_run = 0

    deltas: dict[str, int] = {}
    counter_reset = False
    for key in (
        "search_rejected_total",
        "breaker_tripped_total",
        "backpressure_cancellation_total",
        "backpressure_limit_reached_total",
    ):
        total_delta = 0
        for previous_sample, current_sample in zip(successful, successful[1:]):
            delta, reset = _counter_delta(previous_sample, current_sample, key)
            total_delta += delta
            counter_reset = counter_reset or reset
        deltas[f"{key}_delta"] = total_delta

    cpu_values = [
        node["os_cpu_percent"]
        for node in node_values
        if node.get("os_cpu_percent") is not None
    ]
    heap_values = [
        node["heap_used_percent"]
        for node in node_values
        if node.get("heap_used_percent") is not None
    ]
    final_aggregate = successful[-1].get("aggregate", {})
    modes = sorted(
        {
            str(node["backpressure_mode"])
            for node in node_values
            if node.get("backpressure_mode") is not None
        }
    )
    return {
        "sample_count": len(samples),
        "successful_sample_count": len(successful),
        "failed_sample_count": len(failed),
        "max_nodes_failed": max(_int(sample.get("nodes_failed")) for sample in successful),
        "max_missing_required_metric_count": max(
            _int(sample.get("aggregate", {}).get("missing_required_metric_count"))
            for sample in successful
        ),
        "max_node_cpu_percent": max(cpu_values, default=None),
        "max_node_heap_percent": max(heap_values, default=None),
        "max_search_active_total": max(
            _int(sample.get("aggregate", {}).get("search_active_total"))
            for sample in successful
        ),
        "max_search_queue_total": max(
            _int(sample.get("aggregate", {}).get("search_queue_total"))
            for sample in successful
        ),
        "max_consecutive_search_queue_samples": longest_queue_run,
        "final_search_active_total": _int(final_aggregate.get("search_active_total")),
        "final_search_queue_total": _int(final_aggregate.get("search_queue_total")),
        "backpressure_modes": modes,
        "node_set_changed": any(
            set(previous_sample.get("nodes", {}))
            != set(current_sample.get("nodes", {}))
            for previous_sample, current_sample in zip(successful, successful[1:])
        ),
        "counter_reset_detected": counter_reset,
        **deltas,
    }


class OpenSearchMetricsSampler:
    """Poll read-only node statistics and persist each sample immediately."""

    def __init__(
        self,
        base_url: str,
        output_path: Path,
        interval_seconds: float,
        timeout_seconds: float,
        username: str = "",
        password: str = "",
        verify_certs: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if bool(username) != bool(password):
            raise ValueError("OpenSearch username and password must be provided together")
        self.base_url = base_url.rstrip("/")
        self.output_path = output_path
        self.interval_seconds = interval_seconds
        self.timeout_seconds = timeout_seconds
        self.samples: list[dict[str, Any]] = []
        self._stop = Event()
        self._write_lock = Lock()
        self._thread: Thread | None = None
        self._handle = None
        self._owns_client = client is None
        self._client = client or httpx.Client(
            auth=(username, password) if username else None,
            verify=verify_certs,
            timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0)),
        )

    def _fetch(self) -> dict[str, Any]:
        response = self._client.get(
            f"{self.base_url}{NODE_STATS_PATH}",
            params={"filter_path": NODE_STATS_FILTER_PATH},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("OpenSearch node stats response must be an object")
        sample = normalize_opensearch_sample(payload)
        if sample["node_count"] == 0:
            raise ValueError("OpenSearch node stats response contains no nodes")
        missing_metric_count = sample["aggregate"][
            "missing_required_metric_count"
        ]
        if missing_metric_count:
            raise ValueError(
                "OpenSearch node stats response is missing "
                f"{missing_metric_count} required metrics"
            )
        return sample

    def _record(self, sample: dict[str, Any]) -> None:
        with self._write_lock:
            self.samples.append(sample)
            if self._handle is not None:
                self._handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
                self._handle.flush()

    def _sample_or_error(self) -> None:
        try:
            self._record(self._fetch())
        except Exception as exc:  # Preserve monitoring failure as gate evidence.
            self._record(
                {
                    "sampled_at": now_iso(),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self._sample_or_error()

    def start(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.output_path.open("x", encoding="utf-8")
        try:
            # A missing preflight sample means the capacity run must not begin.
            initial = self._fetch()
            self._record(initial)
        except Exception:
            self._handle.close()
            self._handle = None
            if self._owns_client:
                self._client.close()
            raise
        self._thread = Thread(target=self._run, name="opensearch-metrics", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        sampler_thread_alive = False
        if self._thread is not None:
            self._thread.join(
                timeout=max(
                    1.0,
                    self.interval_seconds * 2,
                    self.timeout_seconds + 1.0,
                )
            )
            sampler_thread_alive = self._thread.is_alive()
        if sampler_thread_alive:
            self._record(
                {
                    "sampled_at": now_iso(),
                    "error": (
                        "RuntimeError: OpenSearch metrics sampler did not stop "
                        "before summary"
                    ),
                }
            )
        else:
            self._sample_or_error()
        with self._write_lock:
            if self._handle is not None:
                self._handle.close()
                self._handle = None
        if self._owns_client:
            self._client.close()
            if sampler_thread_alive and self._thread is not None:
                self._thread.join(timeout=1.0)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_policy(path: Path) -> tuple[dict[str, Any], str]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(policy, dict):
        raise ValueError("capacity policy must be a JSON object")
    if policy.get("levels") != [4, 6, 8, 10]:
        raise ValueError("capacity policy levels must be exactly 4, 6, 8, 10")
    required_keys = {
        "application": {
            "max_failure_rate",
            "max_bulkhead_rejections",
            "rejection_p99_slo_seconds",
        },
        "opensearch": {
            "max_failed_metric_samples",
            "max_missing_required_metric_count",
            "max_nodes_failed",
            "max_node_cpu_percent",
            "max_node_heap_percent",
            "max_consecutive_search_queue_samples",
            "max_search_rejected_delta",
            "max_breaker_tripped_delta",
            "max_backpressure_cancellation_delta",
            "require_final_search_idle",
        },
        "knee": {
            "minimum_throughput_gain_ratio",
            "maximum_tail_latency_growth_ratio",
        },
        "mixed": {"max_light_p95_growth_ratio"},
    }
    for section, keys in required_keys.items():
        values = policy.get(section)
        if not isinstance(values, dict) or not keys <= values.keys():
            raise ValueError(f"capacity policy section {section} is incomplete")
    if not 0 <= float(policy["application"]["max_failure_rate"]) <= 1:
        raise ValueError("max_failure_rate must be between 0 and 1")
    if float(policy["application"]["rejection_p99_slo_seconds"]) <= 0:
        raise ValueError("rejection_p99_slo_seconds must be positive")
    for key in ("max_node_cpu_percent", "max_node_heap_percent"):
        if not 0 <= float(policy["opensearch"][key]) <= 100:
            raise ValueError(f"{key} must be between 0 and 100")
    if not isinstance(policy["opensearch"]["require_final_search_idle"], bool):
        raise ValueError("require_final_search_idle must be boolean")
    if float(policy["mixed"]["max_light_p95_growth_ratio"]) < 0:
        raise ValueError("max_light_p95_growth_ratio cannot be negative")
    return policy, file_sha256(path)


def _ratio_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((current / previous) - 1.0, 6)


def compare_capacity_tiers(
    previous_summary: dict[str, Any],
    current_request_summary: dict[str, Any],
    knee_policy: dict[str, Any],
) -> dict[str, Any]:
    previous_request = previous_summary.get("request_metrics", {})
    throughput_gain = _ratio_change(
        current_request_summary.get("throughput_requests_per_second"),
        previous_request.get("throughput_requests_per_second"),
    )
    current_latency = current_request_summary.get("latency_seconds", {})
    previous_latency = previous_request.get("latency_seconds", {})
    p95_growth = _ratio_change(current_latency.get("p95"), previous_latency.get("p95"))
    p99_growth = _ratio_change(current_latency.get("p99"), previous_latency.get("p99"))
    latency_growths = [value for value in (p95_growth, p99_growth) if value is not None]
    knee_detected = bool(
        throughput_gain is not None
        and throughput_gain < float(knee_policy["minimum_throughput_gain_ratio"])
        and latency_growths
        and max(latency_growths)
        > float(knee_policy["maximum_tail_latency_growth_ratio"])
    )
    return {
        "previous_concurrency": previous_summary.get("run", {}).get("concurrency"),
        "throughput_gain_ratio": throughput_gain,
        "p95_growth_ratio": p95_growth,
        "p99_growth_ratio": p99_growth,
        "knee_detected": knee_detected,
    }


def evaluate_capacity_run(
    request_summary: dict[str, Any],
    opensearch_summary: dict[str, Any],
    policy: dict[str, Any],
    previous_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    reasons: list[str] = []
    application_policy = policy["application"]
    opensearch_policy = policy["opensearch"]

    if request_summary.get("failure_rate", 0) > float(
        application_policy["max_failure_rate"]
    ):
        reasons.append("application_failure_rate_exceeded")
    rejection = request_summary.get("bulkhead_rejections", {})
    if rejection.get("count", 0) > int(application_policy["max_bulkhead_rejections"]):
        reasons.append("application_bulkhead_rejection_observed")
    rejection_p99 = rejection.get("latency_seconds", {}).get("p99")
    if rejection_p99 is not None and rejection_p99 > float(
        application_policy["rejection_p99_slo_seconds"]
    ):
        reasons.append("fast_rejection_slo_exceeded")

    checks = (
        ("failed_sample_count", "max_failed_metric_samples", "metric_sample_failed"),
        (
            "max_missing_required_metric_count",
            "max_missing_required_metric_count",
            "required_opensearch_metric_missing",
        ),
        ("max_nodes_failed", "max_nodes_failed", "opensearch_node_stats_failed"),
        ("max_node_cpu_percent", "max_node_cpu_percent", "opensearch_cpu_guardrail"),
        ("max_node_heap_percent", "max_node_heap_percent", "opensearch_heap_guardrail"),
        (
            "max_consecutive_search_queue_samples",
            "max_consecutive_search_queue_samples",
            "opensearch_search_queue_sustained",
        ),
        (
            "search_rejected_total_delta",
            "max_search_rejected_delta",
            "opensearch_search_rejection_observed",
        ),
        (
            "breaker_tripped_total_delta",
            "max_breaker_tripped_delta",
            "opensearch_breaker_trip_observed",
        ),
        (
            "backpressure_cancellation_total_delta",
            "max_backpressure_cancellation_delta",
            "opensearch_backpressure_cancellation_observed",
        ),
    )
    for metric_key, policy_key, reason in checks:
        value = opensearch_summary.get(metric_key)
        if value is not None and value > float(opensearch_policy[policy_key]):
            reasons.append(reason)
    if opensearch_summary.get("counter_reset_detected"):
        reasons.append("opensearch_counter_reset_detected")
    if opensearch_summary.get("node_set_changed"):
        reasons.append("opensearch_node_set_changed")
    if opensearch_policy.get("require_final_search_idle") and (
        opensearch_summary.get("final_search_active_total", 0) > 0
        or opensearch_summary.get("final_search_queue_total", 0) > 0
    ):
        reasons.append("opensearch_not_idle_after_observation_window")

    comparison = None
    if previous_summary is not None:
        comparison = compare_capacity_tiers(
            previous_summary,
            request_summary,
            policy["knee"],
        )
        if comparison["knee_detected"]:
            reasons.append("capacity_knee_detected")

    return {
        "safe_to_continue": not reasons,
        "requires_human_approval_for_next_level": True,
        "stop_reasons": reasons,
        "comparison_to_previous_level": comparison,
    }


def summarize_bulkhead_log(lines: Iterator[str]) -> dict[str, Any]:
    values = [value for line in lines if (value := _bulkhead_log_value(line))]
    if not values:
        return {
            "event_count": 0,
            "rejected_event_count": 0,
            "error": "no_bulkhead_events_found",
        }

    def summarize(values_for_bulkhead: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "event_count": len(values_for_bulkhead),
            "rejected_event_count": sum(
                value["event"] == "rejected" for value in values_for_bulkhead
            ),
            "max_in_flight": max(
                value["in_flight"] for value in values_for_bulkhead
            ),
            "max_peak_in_flight": max(
                value["peak_in_flight"] for value in values_for_bulkhead
            ),
            "first_rejected_total": values_for_bulkhead[0]["rejected_total"],
            "last_rejected_total": values_for_bulkhead[-1]["rejected_total"],
            "capacities": sorted(
                {value["capacity"] for value in values_for_bulkhead}
            ),
            "acquire_timeout_seconds": sorted(
                {
                    value["acquire_timeout_seconds"]
                    for value in values_for_bulkhead
                    if value["acquire_timeout_seconds"] is not None
                }
            ),
            "final_in_flight": values_for_bulkhead[-1]["in_flight"],
        }

    result = {
        "event_count": len(values),
        "rejected_event_count": sum(value["event"] == "rejected" for value in values),
        "max_in_flight": max(value["in_flight"] for value in values),
        "max_peak_in_flight": max(value["peak_in_flight"] for value in values),
        "first_rejected_total": values[0]["rejected_total"],
        "last_rejected_total": values[-1]["rejected_total"],
        "capacities": sorted({value["capacity"] for value in values}),
        "acquire_timeout_seconds": sorted(
            {
                value["acquire_timeout_seconds"]
                for value in values
                if value["acquire_timeout_seconds"] is not None
            }
        ),
        "final_in_flight": values[-1]["in_flight"],
    }
    result["bulkheads"] = {
        name: summarize([value for value in values if value["name"] == name])
        for name in sorted({value["name"] for value in values})
    }
    return result


def _bulkhead_log_value(line: str) -> dict[str, Any] | None:
    json_start = line.find("{")
    if json_start >= 0:
        try:
            payload = json.loads(line[json_start:])
        except (json.JSONDecodeError, UnicodeError):
            payload = None
        if isinstance(payload, dict) and payload.get("event") in {
            "in_flight",
            "rejected",
        }:
            required = (
                "in_flight",
                "peak_in_flight",
                "rejected_total",
                "capacity",
            )
            if all(
                isinstance(payload.get(field), int)
                and not isinstance(payload.get(field), bool)
                for field in required
            ):
                name = payload.get("name")
                if name is not None and not isinstance(name, str):
                    return None
                acquire_timeout = payload.get("acquire_timeout_seconds")
                if acquire_timeout is None or (
                    isinstance(acquire_timeout, (int, float))
                    and not isinstance(acquire_timeout, bool)
                ):
                    return {
                        "event": payload["event"],
                        "in_flight": payload["in_flight"],
                        "peak_in_flight": payload["peak_in_flight"],
                        "rejected_total": payload["rejected_total"],
                        "capacity": payload["capacity"],
                        "name": name or "unlabeled",
                        "acquire_timeout_seconds": (
                            float(acquire_timeout)
                            if acquire_timeout is not None
                            else None
                        ),
                    }

    match = BULKHEAD_LOG_PATTERN.search(line)
    if match is None:
        return None
    return {
        "event": match.group("event"),
        "in_flight": int(match.group("in_flight")),
        "peak_in_flight": int(match.group("peak_in_flight")),
        "rejected_total": int(match.group("rejected_total")),
        "capacity": int(match.group("capacity")),
        "name": match.group("name") or "unlabeled",
        "acquire_timeout_seconds": (
            float(match.group("acquire_timeout_seconds"))
            if match.group("acquire_timeout_seconds") is not None
            else None
        ),
    }


def run_id() -> str:
    return datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
