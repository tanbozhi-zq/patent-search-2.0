"""对部署后的内部 Prometheus 与管理指标接口执行有界只读验收。"""

import argparse
from datetime import datetime
import json
import math
import os
import re
from time import monotonic, sleep
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

from app.core.admin_metrics import (
    ADMIN_METRICS_CACHE_SECONDS,
    ADMIN_METRICS_MAX_RESPONSE_BYTES,
    ADMIN_METRICS_MAX_SERIES_PER_QUERY,
    ADMIN_METRIC_WINDOWS,
    _sample,
    _queries,
    _vector_result,
)


PROMETHEUS_JOB = "patent-search"
PROMETHEUS_SELF_JOB = "prometheus"
STORAGE_ALERT = "PatentSearchPrometheusStorageBudgetHigh"
EXPECTED_RETENTION_SECONDS = 30 * 24 * 60 * 60
EXPECTED_RETENTION_BYTES = 2 * 1024 * 1024 * 1024
EXPECTED_SCRAPE_SAMPLE_LIMIT = 1000
EXPECTED_SCRAPE_BODY_SIZE_BYTES = 256 * 1024
EXPECTED_BULKHEADS = frozenset({"global", "heavy_search"})
METRIC_COMPARISON_REL_TOLERANCE = 0.05
METRIC_COMPARISON_ABS_TOLERANCE = 1e-9
DIRECT_QUERY_TOTAL_TIMEOUT_SECONDS = 30
DEPENDENCY_FAILURE_OUTCOMES = {
    "connection_error",
    "unavailable",
    "timeout",
    "invalid_response",
    "error",
    "unexpected_error",
}
ACCEPTANCE_EVENT_MIN_INTERVAL_SECONDS = 30
ACCEPTANCE_EVENT_MAX_INTERVAL_SECONDS = 15 * 60
ACCEPTANCE_EVENT_VISIBILITY_WINDOW_SECONDS = max(ADMIN_METRIC_WINDOWS)
ACCEPTANCE_SCRAPE_MAX_AGE_SECONDS = 30
ACCEPTANCE_BASELINE_CAPTURE_TIMEOUT_SECONDS = 35
ACCEPTANCE_BASELINE_POLL_SECONDS = 1
# Settings rejects ADMIN_METRICS_TIMEOUT_SECONDS above five seconds. Waiting
# beyond that bound excludes any admin query batch that began before raw final.
ACCEPTANCE_ADMIN_PREEXISTING_QUERY_MAX_SECONDS = 5
ACCEPTANCE_ADMIN_CAPTURE_TIMEOUT_SECONDS = ADMIN_METRICS_CACHE_SECONDS + 15
ACCEPTANCE_ADMIN_CAPTURE_POLL_SECONDS = 0.5
_COMMIT_PATTERN = re.compile(r"[0-9A-Fa-f]{7,64}\Z")
_DEPENDENCY_FAILURE_SELECTOR = "|".join(sorted(DEPENDENCY_FAILURE_OUTCOMES))
ACCEPTANCE_COUNTER_QUERIES = {
    "normal_2xx": (
        "sum by (instance) (patent_search_http_requests_total{"
        f'job="{PROMETHEUS_JOB}",route="/api/patent/search",status=~"2.."'
        "})"
    ),
    "known_4xx": (
        "sum by (instance) (patent_search_http_requests_total{"
        f'job="{PROMETHEUS_JOB}",route="/api/patent/search",'
        'status="400",code="40002"'
        "})"
    ),
    "dependency_failure": (
        "sum by (instance) (patent_search_opensearch_calls_total{"
        f'job="{PROMETHEUS_JOB}",operation="search",'
        f'outcome=~"{_DEPENDENCY_FAILURE_SELECTOR}"'
        "})"
    ),
    "global_bulkhead_rejection": (
        "sum by (instance) (patent_search_bulkhead_rejections_total{"
        f'job="{PROMETHEUS_JOB}",bulkhead="global"'
        "})"
    ),
    "heavy_search_bulkhead_rejection": (
        "sum by (instance) (patent_search_bulkhead_rejections_total{"
        f'job="{PROMETHEUS_JOB}",bulkhead="heavy_search"'
        "})"
    ),
}
ACCEPTANCE_COUNTER_METRIC_FAMILIES = frozenset(
    {
        "patent_search_http_requests_total",
        "patent_search_opensearch_calls_total",
        "patent_search_bulkhead_rejections_total",
    }
)
ACCEPTANCE_METADATA_TARGET_LIMIT = ADMIN_METRICS_MAX_SERIES_PER_QUERY + 1
ACCEPTANCE_SNAPSHOT_QUERIES = {
    "up": f'up{{job="{PROMETHEUS_JOB}"}}',
    "scrape_time": f'timestamp(up{{job="{PROMETHEUS_JOB}"}})',
    "service_start_time_seconds": (
        "patent_search_service_start_time_seconds{"
        f'job="{PROMETHEUS_JOB}"' + "}"
    ),
    "build_info": (
        "patent_search_build_info{" + f'job="{PROMETHEUS_JOB}"' + "}"
    ),
    **ACCEPTANCE_COUNTER_QUERIES,
}


def _bounded_base_url(value: str, name: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{name} must be an HTTP(S) base URL without credentials")
    return value.rstrip("/")


def _samples_by_key(metrics: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {
        result.get("key", ""): result.get("samples", [])
        for result in metrics.get("results", [])
    }


def _selected_windows(values: list[int] | None) -> tuple[int, ...]:
    return tuple(sorted(set(values or ADMIN_METRIC_WINDOWS)))


def _finite_number(value: Any) -> float | None:
    if type(value) not in (int, float) or not math.isfinite(value):
        return None
    return float(value)


def _release_value_is_deployed(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value.strip().lower() != "unknown"
    )


def _iso_timestamp(raw_value: Any) -> float | None:
    if not isinstance(raw_value, str) or not raw_value:
        return None
    try:
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except (OverflowError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.timestamp()


def _release_started_at(status: dict[str, Any]) -> float | None:
    release = status.get("release")
    if not isinstance(release, dict):
        return None
    return _iso_timestamp(release.get("started_at"))


def _positive_build_instances(
    samples: list[dict[str, Any]],
) -> set[str]:
    return {
        instance
        for sample in samples
        if isinstance(sample, dict)
        and isinstance(sample.get("labels"), dict)
        and isinstance((instance := sample["labels"].get("instance")), str)
        and _release_value_is_deployed(instance)
        and (value := _finite_number(sample.get("value"))) is not None
        and value > 0
    }


def _release_binding(
    status: dict[str, Any],
    samples: dict[str, list[dict[str, Any]]],
    *,
    expected_release_commit: str,
    expected_instance_count: int,
) -> dict[str, Any]:
    raw_release = status.get("release")
    release = raw_release if isinstance(raw_release, dict) else {}
    status_release_matches = (
        _COMMIT_PATTERN.fullmatch(expected_release_commit) is not None
        and isinstance(raw_release, dict)
        and release.get("commit") == expected_release_commit
        and _release_value_is_deployed(release.get("service_version"))
        and _release_value_is_deployed(release.get("tag"))
        and _release_value_is_deployed(release.get("instance_id"))
        and _release_started_at(status) is not None
    )

    build_instances: set[str] = set()
    build_release_matches = status_release_matches
    for sample in samples.get("build_info", []):
        labels = sample.get("labels") if isinstance(sample, dict) else None
        value = _finite_number(sample.get("value")) if isinstance(sample, dict) else None
        instance = labels.get("instance") if isinstance(labels, dict) else None
        if (
            not _release_value_is_deployed(instance)
            or instance in build_instances
            or value != 1
            or labels.get("version") != release.get("service_version")
            or labels.get("commit") != expected_release_commit
            or labels.get("tag") != release.get("tag")
        ):
            build_release_matches = False
            continue
        build_instances.add(instance)
    if not build_instances:
        build_release_matches = False

    topology_matches = (
        build_release_matches
        and len(build_instances) == expected_instance_count
        and release.get("instance_id") in build_instances
    )

    start_values: dict[str, float] = {}
    start_series_valid = True
    for sample in samples.get("service_start_time_seconds", []):
        labels = sample.get("labels") if isinstance(sample, dict) else None
        instance = labels.get("instance") if isinstance(labels, dict) else None
        value = _finite_number(sample.get("value")) if isinstance(sample, dict) else None
        if (
            not isinstance(instance, str)
            or not instance
            or instance in start_values
            or value is None
            or value <= 0
        ):
            start_series_valid = False
            continue
        start_values[instance] = value
    status_started_at = _release_started_at(status)
    start_time_matches = (
        topology_matches
        and start_series_valid
        and set(start_values) == build_instances
        and status_started_at is not None
        and math.isclose(
            start_values.get(release.get("instance_id"), math.nan),
            status_started_at,
            rel_tol=0,
            abs_tol=0.001,
        )
    )
    return {
        "status_release_matches": status_release_matches,
        "build_release_matches": build_release_matches,
        "topology_matches": topology_matches,
        "start_time_matches": start_time_matches,
        "instances": build_instances,
        "start_values": start_values,
        "status_started_at": status_started_at,
    }


def _metric_semantic_checks(
    samples: dict[str, list[dict[str, Any]]],
) -> dict[str, bool]:
    success_samples = samples.get("success_rate", [])
    success_rate_valid = len(success_samples) <= 1 and all(
        isinstance(sample.get("value"), (int, float))
        and math.isfinite(sample["value"])
        and 0 <= sample["value"] <= 1
        for sample in success_samples
    )

    quantile_keys = (
        "latency_p50_seconds",
        "latency_p95_seconds",
        "latency_p99_seconds",
    )
    quantile_samples = [samples.get(key, []) for key in quantile_keys]
    if all(not values for values in quantile_samples):
        quantiles_valid = True
    elif all(len(values) == 1 for values in quantile_samples):
        quantiles = [values[0].get("value") for values in quantile_samples]
        quantiles_valid = all(
            isinstance(value, (int, float))
            and math.isfinite(value)
            and value >= 0
            for value in quantiles
        ) and quantiles[0] <= quantiles[1] <= quantiles[2]
    else:
        quantiles_valid = False

    return {
        "success_rate_in_range": success_rate_valid,
        "latency_quantiles_ordered": quantiles_valid,
    }


def _sample_map(
    samples: list[dict[str, Any]],
) -> dict[tuple[tuple[str, str], ...], float] | None:
    normalized: dict[tuple[tuple[str, str], ...], float] = {}
    for sample in samples:
        labels = sample.get("labels")
        value = sample.get("value")
        if (
            not isinstance(labels, dict)
            or not all(
                isinstance(name, str) and isinstance(label, str)
                for name, label in labels.items()
            )
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            return None
        label_key = tuple(sorted(labels.items()))
        if label_key in normalized:
            return None
        normalized[label_key] = float(value)
    return normalized


def evaluate_admin_prometheus_comparison(
    admin_metrics: dict[str, Any],
    direct_metrics: dict[str, Any],
    *,
    window_seconds: int,
) -> dict[str, bool]:
    required_keys = set(_queries(window_seconds))
    direct_results = {
        result.get("key", ""): result
        for result in direct_metrics.get("results", [])
    }
    direct_complete = (
        direct_metrics.get("source") == "prometheus"
        and direct_metrics.get("window_seconds") == window_seconds
        and direct_metrics.get("partial") is False
        and required_keys == set(direct_results)
        and all(
            direct_results[key].get("available") is True
            for key in required_keys
        )
    )
    admin_samples = _samples_by_key(admin_metrics)
    direct_samples = _samples_by_key(direct_metrics)
    sample_sets_match = direct_complete and required_keys == set(admin_samples)
    if sample_sets_match:
        for key in required_keys:
            admin_values = _sample_map(admin_samples[key])
            direct_values = _sample_map(direct_samples[key])
            if (
                admin_values is None
                or direct_values is None
                or set(admin_values) != set(direct_values)
                or any(
                    not math.isclose(
                        admin_values[labels],
                        direct_values[labels],
                        rel_tol=METRIC_COMPARISON_REL_TOLERANCE,
                        abs_tol=METRIC_COMPARISON_ABS_TOLERANCE,
                    )
                    for labels in admin_values
                )
            ):
                sample_sets_match = False
                break
    return {
        "direct_metric_queries_complete": direct_complete,
        "admin_matches_direct_prometheus": sample_sets_match,
    }


def _read_direct_metrics(
    client: httpx.Client,
    admin_payloads: dict[int, dict[str, Any]],
    windows: tuple[int, ...],
) -> dict[int, dict[str, Any]]:
    direct_payloads: dict[int, dict[str, Any]] = {}
    deadline = monotonic() + DIRECT_QUERY_TOTAL_TIMEOUT_SECONDS
    for window in windows:
        evaluation_time = admin_payloads[window].get("generated_at")
        if not isinstance(evaluation_time, str) or not evaluation_time:
            raise ValueError("admin metric generation time is invalid")
        results = []
        for key, expression in _queries(window).items():
            remaining_seconds = deadline - monotonic()
            if remaining_seconds <= 0:
                raise ValueError("direct Prometheus query batch timed out")
            payload = _prometheus_query(
                client,
                expression,
                evaluation_time=evaluation_time,
                request_timeout_seconds=min(5, remaining_seconds),
            )
            vector = _vector_result(payload)
            if len(vector) > ADMIN_METRICS_MAX_SERIES_PER_QUERY:
                raise ValueError("Prometheus response exceeds series limit")
            results.append(
                {
                    "key": key,
                    "available": True,
                    "samples": [
                        _sample(item).model_dump(mode="json")
                        for item in vector
                    ],
                }
            )
        direct_payloads[window] = {
            "source": "prometheus",
            "generated_at": evaluation_time,
            "window_seconds": window,
            "partial": False,
            "results": results,
        }
    return direct_payloads


def _has_numeric_sample(
    samples: list[dict[str, Any]],
    *,
    positive: bool,
) -> bool:
    for sample in samples:
        value = sample.get("value")
        if not isinstance(value, (int, float)):
            continue
        if value > 0 or (not positive and value >= 0):
            return True
    return False


def _requires_event_visibility(window_seconds: int, *, acceptance_final: bool) -> bool:
    return (
        acceptance_final
        and window_seconds == ACCEPTANCE_EVENT_VISIBILITY_WINDOW_SECONDS
    )


def _instance_values_by_bulkhead(
    samples: list[dict[str, Any]],
    *,
    positive: bool,
) -> dict[str, dict[str, float]] | None:
    grouped = {bulkhead: {} for bulkhead in EXPECTED_BULKHEADS}
    for sample in samples:
        labels = sample.get("labels", {})
        bulkhead = labels.get("bulkhead")
        instance = labels.get("instance")
        value = sample.get("value")
        if (
            bulkhead not in EXPECTED_BULKHEADS
            or not instance
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
            or (positive and value == 0)
            or instance in grouped[bulkhead]
        ):
            return None
        grouped[bulkhead][instance] = float(value)
    return grouped


def _values_by_bulkhead(
    samples: list[dict[str, Any]],
) -> dict[str, float] | None:
    values: dict[str, float] = {}
    for sample in samples:
        bulkhead = sample.get("labels", {}).get("bulkhead")
        value = sample.get("value")
        if (
            bulkhead not in EXPECTED_BULKHEADS
            or bulkhead in values
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            return None
        values[bulkhead] = float(value)
    return values


def _bulkhead_checks(
    samples: dict[str, list[dict[str, Any]]],
    build_instances: set[str],
) -> dict[str, bool]:
    capacity_by_instance = _instance_values_by_bulkhead(
        samples.get("bulkhead_capacity_by_instance", []),
        positive=True,
    )
    in_flight_by_instance = _instance_values_by_bulkhead(
        samples.get("bulkhead_in_flight_by_instance", []),
        positive=False,
    )
    utilization_by_instance = _instance_values_by_bulkhead(
        samples.get("bulkhead_utilization_by_instance", []),
        positive=False,
    )
    capacity_total = _values_by_bulkhead(
        samples.get("bulkhead_capacity_total", [])
    )
    in_flight_total = _values_by_bulkhead(
        samples.get("bulkhead_in_flight_total", [])
    )
    worst_utilization = _values_by_bulkhead(
        samples.get("bulkhead_worst_utilization", [])
    )

    instance_maps = (
        capacity_by_instance,
        in_flight_by_instance,
        utilization_by_instance,
    )
    topology_ok = bool(build_instances) and all(
        grouped is not None
        and set(grouped) == EXPECTED_BULKHEADS
        and all(set(grouped[bulkhead]) == build_instances for bulkhead in grouped)
        for grouped in instance_maps
    )
    if not topology_ok:
        return {
            "bulkhead_instance_series_complete": False,
            "bulkhead_aggregations_consistent": False,
        }

    assert capacity_by_instance is not None
    assert in_flight_by_instance is not None
    assert utilization_by_instance is not None
    aggregate_maps = (capacity_total, in_flight_total, worst_utilization)
    aggregate_labels_ok = all(
        values is not None and set(values) == EXPECTED_BULKHEADS
        for values in aggregate_maps
    )
    if not aggregate_labels_ok:
        return {
            "bulkhead_instance_series_complete": True,
            "bulkhead_aggregations_consistent": False,
        }

    assert capacity_total is not None
    assert in_flight_total is not None
    assert worst_utilization is not None
    aggregation_ok = all(
        math.isclose(
            capacity_total[bulkhead],
            sum(capacity_by_instance[bulkhead].values()),
        )
        and math.isclose(
            in_flight_total[bulkhead],
            sum(in_flight_by_instance[bulkhead].values()),
        )
        and math.isclose(
            worst_utilization[bulkhead],
            max(utilization_by_instance[bulkhead].values()),
        )
        and all(
            math.isclose(
                utilization_by_instance[bulkhead][instance],
                in_flight_by_instance[bulkhead][instance]
                / capacity_by_instance[bulkhead][instance],
            )
            for instance in build_instances
        )
        for bulkhead in EXPECTED_BULKHEADS
    )
    return {
        "bulkhead_instance_series_complete": True,
        "bulkhead_aggregations_consistent": aggregation_ok,
    }


def evaluate_admin_payloads(
    status: dict[str, Any],
    metrics: dict[str, Any],
    *,
    window_seconds: int,
    minimum_instances: int,
    require_acceptance_events: bool,
    expected_release_commit: str | None = None,
) -> dict[str, bool]:
    results = metrics.get("results", [])
    available_by_key = {
        result.get("key", ""): result.get("available") is True
        for result in results
    }
    samples = _samples_by_key(metrics)
    required_keys = set(_queries(window_seconds))
    build_instances = _positive_build_instances(samples.get("build_info", []))

    checks = {
        "admin_metrics_source": (
            status.get("metrics_source") == "prometheus"
            and metrics.get("source") == "prometheus"
        ),
        "admin_window": metrics.get("window_seconds") == window_seconds,
        "admin_metrics_complete": metrics.get("partial") is False,
        "admin_metric_keys": required_keys <= set(available_by_key),
        "admin_metric_queries_available": (
            required_keys <= available_by_key.keys()
            and all(available_by_key[key] for key in required_keys)
        ),
        "admin_build_instances": len(build_instances) >= minimum_instances,
    }
    checks.update(_bulkhead_checks(samples, build_instances))
    checks.update(_metric_semantic_checks(samples))

    if expected_release_commit is not None:
        release_binding = _release_binding(
            status,
            samples,
            expected_release_commit=expected_release_commit,
            expected_instance_count=minimum_instances,
        )
        checks.update(
            {
                "admin_release_matches_expected": release_binding[
                    "status_release_matches"
                ],
                "admin_build_release_matches": release_binding[
                    "build_release_matches"
                ],
                "admin_build_instance_topology": release_binding[
                    "topology_matches"
                ],
                "admin_start_time_matches_release": release_binding[
                    "start_time_matches"
                ],
            }
        )

    if not require_acceptance_events:
        return checks

    http_outcomes = samples.get("http_outcome_rate", [])
    route_samples = samples.get("route_request_rate", [])
    dependency_samples = samples.get("opensearch_call_rate", [])
    rejection_samples = samples.get("bulkhead_rejections", [])
    checks.update(
        {
            "normal_request_observed": (
                _has_numeric_sample(
                    samples.get("request_rate", []),
                    positive=True,
                )
                and _has_numeric_sample(
                    samples.get("success_rate", []),
                    positive=True,
                )
                and any(
                    str(sample.get("labels", {}).get("status", "")).startswith("2")
                    and isinstance(sample.get("value"), (int, float))
                    and sample["value"] > 0
                    for sample in http_outcomes
                )
                and any(
                    sample.get("labels", {}).get("route") == "/api/patent/search"
                    and isinstance(sample.get("value"), (int, float))
                    and sample["value"] > 0
                    for sample in route_samples
                )
            ),
            "latency_quantiles_observed": all(
                _has_numeric_sample(samples.get(key, []), positive=False)
                for key in (
                    "latency_p50_seconds",
                    "latency_p95_seconds",
                    "latency_p99_seconds",
                )
            ),
            "known_4xx_observed": any(
                sample.get("labels", {}).get("status") == "400"
                and sample.get("labels", {}).get("code") == "40002"
                and isinstance(sample.get("value"), (int, float))
                and sample["value"] > 0
                for sample in http_outcomes
            ),
            "dependency_failure_observed": any(
                sample.get("labels", {}).get("operation") == "search"
                and sample.get("labels", {}).get("outcome")
                in DEPENDENCY_FAILURE_OUTCOMES
                and isinstance(sample.get("value"), (int, float))
                and sample["value"] > 0
                for sample in dependency_samples
            ),
            "global_bulkhead_rejection_observed": any(
                sample.get("labels", {}).get("bulkhead") == "global"
                and isinstance(sample.get("value"), (int, float))
                and sample["value"] > 0
                for sample in rejection_samples
            ),
            "heavy_search_bulkhead_rejection_observed": any(
                sample.get("labels", {}).get("bulkhead") == "heavy_search"
                and isinstance(sample.get("value"), (int, float))
                and sample["value"] > 0
                for sample in rejection_samples
            ),
        }
    )
    return checks


def _vector_values(payload: dict[str, Any]) -> list[float] | None:
    if payload.get("status") != "success":
        return None
    data = payload.get("data", {})
    if data.get("resultType") != "vector":
        return None
    values: list[float] = []
    for result in data.get("result", []):
        value = result.get("value")
        if not isinstance(value, list) or len(value) != 2:
            return None
        try:
            parsed = float(value[1])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(parsed):
            return None
        values.append(parsed)
    return values


def _scalar_value(payload: dict[str, Any]) -> float | None:
    if payload.get("status") != "success":
        return None
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("resultType") != "scalar":
        return None
    result = data.get("result")
    if not isinstance(result, list) or len(result) != 2:
        return None
    evaluation_timestamp = _finite_number(result[0])
    raw_value = result[1]
    if (
        evaluation_timestamp is None
        or evaluation_timestamp <= 0
        or not isinstance(raw_value, str)
        or not raw_value
        or raw_value.strip() != raw_value
    ):
        return None
    try:
        parsed = float(raw_value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def evaluate_prometheus_payloads(
    up: dict[str, Any],
    self_up: dict[str, Any],
    retention_seconds: dict[str, Any],
    retention_bytes: dict[str, Any],
    rules: dict[str, Any],
    *,
    minimum_instances: int = 1,
) -> dict[str, bool]:
    storage_rules = [
        rule
        for group in rules.get("data", {}).get("groups", [])
        for rule in group.get("rules", [])
        if rule.get("name") == STORAGE_ALERT
    ]
    up_values = _vector_values(up)
    self_up_values = _vector_values(self_up)
    retention_second_values = _vector_values(retention_seconds)
    retention_byte_values = _vector_values(retention_bytes)
    return {
        "service_targets_up": (
            up_values is not None
            and len(up_values) >= minimum_instances
            and all(value == 1 for value in up_values)
        ),
        "prometheus_self_target_up": (
            bool(self_up_values) and all(value == 1 for value in self_up_values)
        ),
        "retention_time_configured": (
            bool(retention_second_values)
            and all(
                value == EXPECTED_RETENTION_SECONDS
                for value in retention_second_values
            )
        ),
        "retention_size_configured": (
            bool(retention_byte_values)
            and all(
                value == EXPECTED_RETENTION_BYTES
                for value in retention_byte_values
            )
        ),
        "storage_alert_loaded": (
            rules.get("status") == "success"
            and any(
                rule.get("type") == "alerting"
                and rule.get("health") == "ok"
                and not rule.get("lastError")
                for rule in storage_rules
            )
        ),
    }


def evaluate_scrape_budget_payloads(
    sample_limit: dict[str, Any],
    body_size: dict[str, Any],
    samples_scraped: dict[str, Any],
    *,
    minimum_instances: int,
) -> dict[str, bool]:
    sample_limit_values = _vector_values(sample_limit)
    body_size_values = _vector_values(body_size)
    samples_scraped_values = _vector_values(samples_scraped)

    def has_expected_targets(values: list[float] | None) -> bool:
        return values is not None and len(values) >= minimum_instances

    def below_deployment_gate(
        values: list[float] | None,
        configured_limit: int,
    ) -> bool:
        return has_expected_targets(values) and all(
            0 <= value and 2 * value < configured_limit
            for value in values or ()
        )

    return {
        "scrape_sample_limit_configured": (
            has_expected_targets(sample_limit_values)
            and all(
                value == EXPECTED_SCRAPE_SAMPLE_LIMIT
                for value in sample_limit_values
            )
        ),
        "scrape_body_within_limit": below_deployment_gate(
            body_size_values,
            EXPECTED_SCRAPE_BODY_SIZE_BYTES,
        ),
        "scrape_samples_within_limit": below_deployment_gate(
            samples_scraped_values,
            EXPECTED_SCRAPE_SAMPLE_LIMIT,
        ),
    }


def _prometheus_json_get(
    client: httpx.Client,
    path: str,
    *,
    params: dict[str, str | int],
    request_timeout_seconds: float | None = None,
) -> Any:
    request_timeout = (
        request_timeout_seconds
        if request_timeout_seconds is not None
        else client.timeout
    )
    with client.stream(
        "GET",
        path,
        params=params,
        timeout=request_timeout,
    ) as response:
        response.raise_for_status()
        declared_size = response.headers.get("Content-Length")
        if declared_size is not None:
            try:
                parsed_size = int(declared_size)
            except ValueError as exc:
                raise ValueError("Prometheus Content-Length is invalid") from exc
            if parsed_size < 0 or parsed_size > ADMIN_METRICS_MAX_RESPONSE_BYTES:
                raise ValueError("Prometheus response exceeds byte limit")
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > ADMIN_METRICS_MAX_RESPONSE_BYTES:
                raise ValueError("Prometheus response exceeds byte limit")
    return json.loads(body)


def _prometheus_query(
    client: httpx.Client,
    expression: str,
    *,
    evaluation_time: str | None = None,
    request_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    params: dict[str, str | int] = {"query": expression}
    if evaluation_time is not None:
        params["time"] = evaluation_time
    payload = _prometheus_json_get(
        client,
        "/api/v1/query",
        params=params,
        request_timeout_seconds=request_timeout_seconds,
    )
    if not isinstance(payload, dict):
        raise ValueError("Prometheus query payload is invalid")
    return payload


def _read_acceptance_counter_metadata(
    client: httpx.Client,
) -> dict[str, list[dict[str, Any]]]:
    metadata: dict[str, list[dict[str, Any]]] = {}
    for metric in sorted(ACCEPTANCE_COUNTER_METRIC_FAMILIES):
        payload = _prometheus_json_get(
            client,
            "/api/v1/targets/metadata",
            params={
                "match_target": f'{{job="{PROMETHEUS_JOB}"}}',
                "metric": metric,
                "limit": ACCEPTANCE_METADATA_TARGET_LIMIT,
            },
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("status") != "success"
            or not isinstance(data, list)
            or len(data) > ADMIN_METRICS_MAX_SERIES_PER_QUERY
            or not all(isinstance(item, dict) for item in data)
        ):
            raise ValueError("Prometheus target metadata payload is invalid")
        metadata[metric] = data
    return metadata


def _prometheus_time(client: httpx.Client) -> float:
    evaluation_time = _scalar_value(_prometheus_query(client, "time()"))
    if evaluation_time is None or evaluation_time <= 0:
        raise ValueError("Prometheus evaluation time is invalid")
    return evaluation_time


def _read_acceptance_snapshot(
    client: httpx.Client,
    *,
    evaluation_time: float | None = None,
) -> dict[str, Any]:
    if evaluation_time is None:
        snapshot_time = _prometheus_time(client)
    else:
        parsed_time = _finite_number(evaluation_time)
        if parsed_time is None or parsed_time <= 0:
            raise ValueError("acceptance baseline time is invalid")
        snapshot_time = parsed_time

    query_time = f"{snapshot_time:.6f}"
    samples: dict[str, list[dict[str, Any]]] = {}
    for key, expression in ACCEPTANCE_SNAPSHOT_QUERIES.items():
        vector = _vector_result(
            _prometheus_query(
                client,
                expression,
                evaluation_time=query_time,
            )
        )
        if len(vector) > ADMIN_METRICS_MAX_SERIES_PER_QUERY:
            raise ValueError("Prometheus response exceeds series limit")
        samples[key] = [
            _sample(item).model_dump(mode="json")
            for item in vector
        ]
    return {
        "evaluation_time": snapshot_time,
        "samples": samples,
        "counter_metadata": _read_acceptance_counter_metadata(client),
    }


def _instance_values(
    samples: list[dict[str, Any]],
    *,
    expected_instances: set[str],
    allow_missing: bool,
) -> dict[str, float] | None:
    values: dict[str, float] = {}
    for sample in samples:
        labels = sample.get("labels") if isinstance(sample, dict) else None
        instance = labels.get("instance") if isinstance(labels, dict) else None
        value = _finite_number(sample.get("value")) if isinstance(sample, dict) else None
        if (
            not isinstance(instance, str)
            or instance not in expected_instances
            or instance in values
            or value is None
            or value < 0
        ):
            return None
        values[instance] = value
    if not allow_missing and set(values) != expected_instances:
        return None
    if allow_missing:
        values = {
            instance: values.get(instance, 0.0)
            for instance in expected_instances
        }
    return values


def _snapshot_scrapes_are_fresh(
    snapshot: dict[str, Any],
    instances: set[str],
    *,
    minimum_scrape_time: float | None = None,
) -> bool:
    samples = snapshot.get("samples")
    snapshot_time = _finite_number(snapshot.get("evaluation_time"))
    if not isinstance(samples, dict) or snapshot_time is None or not instances:
        return False
    scrape_times = _instance_values(
        samples.get("scrape_time", []),
        expected_instances=instances,
        allow_missing=False,
    )
    return scrape_times is not None and all(
        scrape_time <= snapshot_time
        and snapshot_time - scrape_time <= ACCEPTANCE_SCRAPE_MAX_AGE_SECONDS
        and (minimum_scrape_time is None or scrape_time > minimum_scrape_time)
        for scrape_time in scrape_times.values()
    )


def _snapshot_counter_series_are_initialized(
    snapshot: dict[str, Any],
    instances: set[str],
) -> bool:
    return _snapshot_counter_values(snapshot, instances) is not None


def _snapshot_counter_values(
    snapshot: dict[str, Any],
    instances: set[str],
) -> dict[str, dict[str, float]] | None:
    samples = snapshot.get("samples")
    if not isinstance(samples, dict) or not instances:
        return None
    values: dict[str, dict[str, float]] = {}
    for key in ACCEPTANCE_COUNTER_QUERIES:
        instance_values = _instance_values(
            samples.get(key, []),
            expected_instances=instances,
            allow_missing=False,
        )
        if instance_values is None:
            return None
        values[key] = instance_values
    return values


def _snapshot_counter_metadata(
    snapshot: dict[str, Any],
    expected_instances: set[str],
) -> dict[str, tuple[str, ...]] | None:
    raw_metadata = snapshot.get("counter_metadata")
    if (
        not isinstance(raw_metadata, dict)
        or set(raw_metadata) != ACCEPTANCE_COUNTER_METRIC_FAMILIES
        or not expected_instances
    ):
        return None
    normalized: dict[str, tuple[str, ...]] = {}
    for metric in ACCEPTANCE_COUNTER_METRIC_FAMILIES:
        entries = raw_metadata.get(metric)
        if (
            not isinstance(entries, list)
            or len(entries) > ADMIN_METRICS_MAX_SERIES_PER_QUERY
        ):
            return None
        instances: set[str] = set()
        for entry in entries:
            target = entry.get("target") if isinstance(entry, dict) else None
            instance = target.get("instance") if isinstance(target, dict) else None
            reported_metric = entry.get("metric") if isinstance(entry, dict) else None
            if (
                not isinstance(target, dict)
                or target.get("job") != PROMETHEUS_JOB
                or not isinstance(instance, str)
                or instance not in expected_instances
                or instance in instances
                or entry.get("type") != "counter"
                or reported_metric != metric
            ):
                return None
            instances.add(instance)
        if instances != expected_instances:
            return None
        normalized[metric] = tuple(sorted(instances))
    return normalized


def _snapshot_release_is_bound(
    status: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    expected_release_commit: str,
    expected_instance_count: int,
) -> tuple[bool, dict[str, Any]]:
    samples = snapshot.get("samples")
    if not isinstance(samples, dict):
        return False, {
            "instances": set(),
            "start_values": {},
            "status_started_at": None,
        }
    binding = _release_binding(
        status,
        samples,
        expected_release_commit=expected_release_commit,
        expected_instance_count=expected_instance_count,
    )
    return (
        all(
            binding[key]
            for key in (
                "status_release_matches",
                "build_release_matches",
                "topology_matches",
                "start_time_matches",
            )
        ),
        binding,
    )


def _snapshot_targets_are_healthy(
    snapshot: dict[str, Any],
    instances: set[str],
) -> bool:
    samples = snapshot.get("samples")
    if not isinstance(samples, dict) or not instances:
        return False
    values = _instance_values(
        samples.get("up", []),
        expected_instances=instances,
        allow_missing=False,
    )
    return values is not None and all(value == 1 for value in values.values())


def evaluate_acceptance_baseline(
    status: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    expected_release_commit: str,
    expected_instance_count: int,
    minimum_scrape_time: float | None = None,
) -> dict[str, bool]:
    release_bound, binding = _snapshot_release_is_bound(
        status,
        snapshot,
        expected_release_commit=expected_release_commit,
        expected_instance_count=expected_instance_count,
    )
    snapshot_time = _finite_number(snapshot.get("evaluation_time"))
    started_at = binding.get("status_started_at")
    return {
        "acceptance_baseline_release_matches": release_bound,
        "acceptance_baseline_targets_healthy": (
            release_bound
            and _snapshot_targets_are_healthy(snapshot, binding["instances"])
        ),
        "acceptance_baseline_scrapes_fresh": (
            release_bound
            and _snapshot_scrapes_are_fresh(
                snapshot,
                binding["instances"],
                minimum_scrape_time=minimum_scrape_time,
            )
        ),
        "acceptance_baseline_counter_series_initialized": (
            release_bound
            and _snapshot_counter_series_are_initialized(
                snapshot,
                binding["instances"],
            )
        ),
        "acceptance_baseline_counter_metadata_matches": (
            release_bound
            and _snapshot_counter_metadata(
                snapshot,
                binding["instances"],
            )
            is not None
        ),
        "acceptance_baseline_time_after_start": (
            release_bound
            and snapshot_time is not None
            and started_at is not None
            and snapshot_time >= started_at
        ),
    }


def _capture_fresh_acceptance_baseline(
    client: httpx.Client,
    status: dict[str, Any],
    *,
    expected_release_commit: str,
    expected_instance_count: int,
    timeout_seconds: float = ACCEPTANCE_BASELINE_CAPTURE_TIMEOUT_SECONDS,
    poll_seconds: float = ACCEPTANCE_BASELINE_POLL_SECONDS,
    now: Callable[[], float] = monotonic,
    sleeper: Callable[[float], None] = sleep,
) -> tuple[dict[str, Any], dict[str, bool], float]:
    capture_started_at = _prometheus_time(client)
    deadline = now() + timeout_seconds
    while True:
        snapshot = _read_acceptance_snapshot(client)
        checks = evaluate_acceptance_baseline(
            status,
            snapshot,
            expected_release_commit=expected_release_commit,
            expected_instance_count=expected_instance_count,
            minimum_scrape_time=capture_started_at,
        )
        if all(checks.values()):
            return snapshot, checks, capture_started_at
        remaining = deadline - now()
        if remaining <= 0:
            raise ValueError("acceptance baseline capture timed out")
        sleeper(min(poll_seconds, remaining))


def _float_maps_match(left: dict[str, float], right: dict[str, float]) -> bool:
    return set(left) == set(right) and all(
        math.isclose(left[key], right[key], rel_tol=0, abs_tol=0.001)
        for key in left
    )


def evaluate_acceptance_event_delta(
    status: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    expected_release_commit: str,
    expected_instance_count: int,
) -> dict[str, bool]:
    before_bound, before_binding = _snapshot_release_is_bound(
        status,
        before,
        expected_release_commit=expected_release_commit,
        expected_instance_count=expected_instance_count,
    )
    after_bound, after_binding = _snapshot_release_is_bound(
        status,
        after,
        expected_release_commit=expected_release_commit,
        expected_instance_count=expected_instance_count,
    )
    before_time = _finite_number(before.get("evaluation_time"))
    after_time = _finite_number(after.get("evaluation_time"))
    elapsed = (
        after_time - before_time
        if before_time is not None and after_time is not None
        else math.nan
    )
    status_started_at = before_binding.get("status_started_at")
    interval_bounded = (
        math.isfinite(elapsed)
        and ACCEPTANCE_EVENT_MIN_INTERVAL_SECONDS <= elapsed
        <= ACCEPTANCE_EVENT_MAX_INTERVAL_SECONDS
        and before_time is not None
        and status_started_at is not None
        and before_time >= status_started_at
    )
    release_stable = (
        before_bound
        and after_bound
        and before_binding["instances"] == after_binding["instances"]
        and _float_maps_match(
            before_binding["start_values"],
            after_binding["start_values"],
        )
    )
    targets_healthy = (
        release_stable
        and _snapshot_targets_are_healthy(before, before_binding["instances"])
        and _snapshot_targets_are_healthy(after, after_binding["instances"])
    )
    scrapes_fresh = (
        release_stable
        and _snapshot_scrapes_are_fresh(before, before_binding["instances"])
        and _snapshot_scrapes_are_fresh(after, after_binding["instances"])
    )

    before_counter_values = (
        _snapshot_counter_values(before, before_binding["instances"])
        if release_stable
        else None
    )
    after_counter_values = (
        _snapshot_counter_values(after, after_binding["instances"])
        if release_stable
        else None
    )
    counter_series_valid = (
        before_counter_values is not None and after_counter_values is not None
    )
    before_counter_metadata = (
        _snapshot_counter_metadata(before, before_binding["instances"])
        if release_stable
        else None
    )
    after_counter_metadata = (
        _snapshot_counter_metadata(after, after_binding["instances"])
        if release_stable
        else None
    )
    counter_metadata_stable = (
        before_counter_metadata is not None
        and before_counter_metadata == after_counter_metadata
    )
    counter_pairs = {
        key: (before_counter_values[key], after_counter_values[key])
        for key in ACCEPTANCE_COUNTER_QUERIES
    } if counter_series_valid else {}

    counters_monotonic = counter_series_valid and all(
        all(after_values[instance] >= before_values[instance] for instance in before_values)
        for before_values, after_values in counter_pairs.values()
    )
    evidence_boundary_valid = (
        interval_bounded
        and release_stable
        and targets_healthy
        and scrapes_fresh
        and counter_metadata_stable
    )
    checks = {
        "acceptance_interval_bounded": interval_bounded,
        "acceptance_release_stable": release_stable,
        "acceptance_targets_healthy": targets_healthy,
        "acceptance_scrapes_fresh": scrapes_fresh,
        "acceptance_counter_metadata_stable": counter_metadata_stable,
        "acceptance_counters_monotonic": counters_monotonic,
    }
    for key in ACCEPTANCE_COUNTER_QUERIES:
        before_values, after_values = counter_pairs.get(key, ({}, {}))
        checks[f"{key}_delta_observed"] = (
            evidence_boundary_valid
            and counters_monotonic
            and sum(after_values.values()) > sum(before_values.values())
        )
    return checks


def evaluate_acceptance_guard(
    status: dict[str, Any],
    after: dict[str, Any],
    guard: dict[str, Any],
    *,
    expected_release_commit: str,
    expected_instance_count: int,
    minimum_scrape_time: float,
) -> dict[str, bool]:
    after_bound, after_binding = _snapshot_release_is_bound(
        status,
        after,
        expected_release_commit=expected_release_commit,
        expected_instance_count=expected_instance_count,
    )
    guard_bound, guard_binding = _snapshot_release_is_bound(
        status,
        guard,
        expected_release_commit=expected_release_commit,
        expected_instance_count=expected_instance_count,
    )
    release_stable = (
        after_bound
        and guard_bound
        and after_binding["instances"] == guard_binding["instances"]
        and after_binding["start_values"] == guard_binding["start_values"]
    )
    targets_healthy = (
        release_stable
        and _snapshot_targets_are_healthy(after, after_binding["instances"])
        and _snapshot_targets_are_healthy(guard, guard_binding["instances"])
    )
    guard_scrape_fresh = (
        release_stable
        and _snapshot_scrapes_are_fresh(
            guard,
            guard_binding["instances"],
            minimum_scrape_time=minimum_scrape_time,
        )
    )
    after_counters = (
        _snapshot_counter_values(after, after_binding["instances"])
        if release_stable
        else None
    )
    guard_counters = (
        _snapshot_counter_values(guard, guard_binding["instances"])
        if release_stable
        else None
    )
    counters_stable = (
        after_counters is not None
        and guard_counters is not None
        and after_counters == guard_counters
    )
    after_counter_metadata = (
        _snapshot_counter_metadata(after, after_binding["instances"])
        if release_stable
        else None
    )
    guard_counter_metadata = (
        _snapshot_counter_metadata(guard, guard_binding["instances"])
        if release_stable
        else None
    )
    counter_metadata_stable = (
        after_counter_metadata is not None
        and after_counter_metadata == guard_counter_metadata
    )
    return {
        "acceptance_guard_release_stable": release_stable,
        "acceptance_guard_targets_healthy": targets_healthy,
        "acceptance_guard_scrape_fresh": guard_scrape_fresh,
        "acceptance_guard_counter_metadata_stable": counter_metadata_stable,
        "acceptance_guard_counters_stable": counters_stable,
    }


def _capture_acceptance_guard(
    client: httpx.Client,
    status: dict[str, Any],
    after: dict[str, Any],
    *,
    expected_release_commit: str,
    expected_instance_count: int,
    minimum_scrape_time: float,
    timeout_seconds: float = ACCEPTANCE_BASELINE_CAPTURE_TIMEOUT_SECONDS,
    poll_seconds: float = ACCEPTANCE_BASELINE_POLL_SECONDS,
    now: Callable[[], float] = monotonic,
    sleeper: Callable[[float], None] = sleep,
) -> tuple[dict[str, Any], dict[str, bool]]:
    deadline = now() + timeout_seconds
    while True:
        guard = _read_acceptance_snapshot(client)
        checks = evaluate_acceptance_guard(
            status,
            after,
            guard,
            expected_release_commit=expected_release_commit,
            expected_instance_count=expected_instance_count,
            minimum_scrape_time=minimum_scrape_time,
        )
        if all(checks.values()):
            return guard, checks
        remaining = deadline - now()
        if remaining <= 0:
            raise ValueError("acceptance completion guard timed out")
        sleeper(min(poll_seconds, remaining))


def _admin_snapshot_times(
    admin_payloads: dict[int, dict[str, Any]],
    windows: tuple[int, ...],
) -> dict[int, float] | None:
    if set(admin_payloads) != set(windows):
        return None
    times: dict[int, float] = {}
    for window in windows:
        generated_at = _iso_timestamp(admin_payloads[window].get("generated_at"))
        if generated_at is None:
            return None
        times[window] = generated_at
    return times


def _admin_snapshots_follow_counter_snapshot(
    admin_payloads: dict[int, dict[str, Any]],
    windows: tuple[int, ...],
    *,
    minimum_generated_at: float,
) -> bool:
    lower_bound = _finite_number(minimum_generated_at)
    generated_times = _admin_snapshot_times(admin_payloads, windows)
    return (
        lower_bound is not None
        and generated_times is not None
        and all(
            generated_times[window]
            > lower_bound + ACCEPTANCE_ADMIN_PREEXISTING_QUERY_MAX_SECONDS
            for window in windows
        )
    )


def _read_admin_payloads(
    admin_base_url: str,
    *,
    username: str,
    password: str,
    windows: tuple[int, ...],
) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    with httpx.Client(
        base_url=admin_base_url,
        auth=httpx.BasicAuth(username, password),
        timeout=5,
        trust_env=False,
    ) as client:
        metrics: dict[int, dict[str, Any]] = {}
        for window in windows:
            metrics_response = client.get(
                "/admin-api/v1/metrics",
                params={"window_seconds": window},
            )
            metrics_response.raise_for_status()
            metrics_payload = metrics_response.json()
            if not isinstance(metrics_payload, dict):
                raise ValueError("admin metrics payload is invalid")
            metrics[window] = metrics_payload
        # Read status last so a restart while the metric windows are captured
        # cannot bind stale Prometheus samples to an earlier process identity.
        response = client.get("/admin-api/v1/status")
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("admin status payload is invalid")
    return payload, metrics


def _read_admin_status(
    admin_base_url: str,
    *,
    username: str,
    password: str,
) -> dict[str, Any]:
    status, _unused = _read_admin_payloads(
        admin_base_url,
        username=username,
        password=password,
        windows=(),
    )
    return status


def _capture_admin_payloads_after(
    admin_base_url: str,
    *,
    username: str,
    password: str,
    windows: tuple[int, ...],
    minimum_generated_at: float,
    timeout_seconds: float = ACCEPTANCE_ADMIN_CAPTURE_TIMEOUT_SECONDS,
    poll_seconds: float = ACCEPTANCE_ADMIN_CAPTURE_POLL_SECONDS,
    now: Callable[[], float] = monotonic,
    sleeper: Callable[[float], None] = sleep,
) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    deadline = now() + timeout_seconds
    while True:
        status, payloads = _read_admin_payloads(
            admin_base_url,
            username=username,
            password=password,
            windows=windows,
        )
        if _admin_snapshots_follow_counter_snapshot(
            payloads,
            windows,
            minimum_generated_at=minimum_generated_at,
        ):
            return status, payloads
        remaining = deadline - now()
        if remaining <= 0:
            raise ValueError("admin metrics snapshot capture timed out")
        sleeper(min(poll_seconds, remaining))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the bounded Issue #62 Prometheus/admin data path."
    )
    parser.add_argument("admin_base_url")
    parser.add_argument("prometheus_base_url")
    parser.add_argument(
        "--window-seconds",
        type=int,
        choices=sorted(ADMIN_METRIC_WINDOWS),
        action="append",
        default=None,
        help=(
            "repeat to select diagnostic windows; acceptance defaults to "
            "all supported windows"
        ),
    )
    parser.add_argument("--minimum-instances", type=int, default=1)
    parser.add_argument("--require-acceptance-events", action="store_true")
    parser.add_argument("--expected-release-commit", default="")
    acceptance_group = parser.add_mutually_exclusive_group()
    acceptance_group.add_argument(
        "--capture-acceptance-baseline",
        action="store_true",
    )
    acceptance_group.add_argument(
        "--acceptance-baseline-time",
        type=float,
        default=None,
    )
    args = parser.parse_args()

    username = os.environ.get("ADMIN_VIEWER_USERNAME", "")
    password = os.environ.get("ADMIN_VIEWER_PASSWORD", "")
    if not username or not password:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": (
                        "ADMIN_VIEWER_USERNAME and ADMIN_VIEWER_PASSWORD "
                        "must be supplied through the process environment"
                    ),
                }
            )
        )
        return 2
    if args.minimum_instances < 1:
        print(json.dumps({"ok": False, "error": "minimum instances must be positive"}))
        return 2
    if args.capture_acceptance_baseline and args.require_acceptance_events:
        print(json.dumps({"ok": False, "error": "acceptance_mode_conflict"}))
        return 2
    if args.require_acceptance_events != (args.acceptance_baseline_time is not None):
        print(json.dumps({"ok": False, "error": "acceptance_baseline_required"}))
        return 2
    acceptance_mode = (
        args.capture_acceptance_baseline or args.require_acceptance_events
    )
    if acceptance_mode and args.window_seconds is not None:
        print(json.dumps({"ok": False, "error": "acceptance_windows_are_fixed"}))
        return 2
    if args.expected_release_commit and _COMMIT_PATTERN.fullmatch(
        args.expected_release_commit
    ) is None:
        print(json.dumps({"ok": False, "error": "expected_release_commit_invalid"}))
        return 2
    if acceptance_mode and not args.expected_release_commit:
        print(json.dumps({"ok": False, "error": "expected_release_commit_required"}))
        return 2
    expected_release_commit = args.expected_release_commit or None
    windows = _selected_windows(args.window_seconds)
    acceptance_baseline_time: float | None = None

    try:
        admin_base_url = _bounded_base_url(args.admin_base_url, "admin_base_url")
        prometheus_base_url = _bounded_base_url(
            args.prometheus_base_url,
            "prometheus_base_url",
        )
        status_payload: dict[str, Any] = {}
        admin_payloads: dict[int, dict[str, Any]] = {}
        if not args.require_acceptance_events:
            status_payload, admin_payloads = _read_admin_payloads(
                admin_base_url,
                username=username,
                password=password,
                windows=windows,
            )

        with httpx.Client(
            base_url=prometheus_base_url,
            timeout=5,
            trust_env=False,
        ) as prom_client:
            ready_response = prom_client.get("/-/ready")
            ready_response.raise_for_status()
            up = _prometheus_query(
                prom_client,
                f'up{{job="{PROMETHEUS_JOB}"}}',
            )
            self_up = _prometheus_query(
                prom_client,
                f'up{{job="{PROMETHEUS_SELF_JOB}"}}',
            )
            retention_seconds = _prometheus_query(
                prom_client,
                (
                    "prometheus_tsdb_retention_limit_seconds"
                    f'{{job="{PROMETHEUS_SELF_JOB}"}}'
                ),
            )
            retention_bytes = _prometheus_query(
                prom_client,
                (
                    "prometheus_tsdb_retention_limit_bytes"
                    f'{{job="{PROMETHEUS_SELF_JOB}"}}'
                ),
            )
            scrape_sample_limit = _prometheus_query(
                prom_client,
                f'scrape_sample_limit{{job="{PROMETHEUS_JOB}"}}',
            )
            scrape_body_size = _prometheus_query(
                prom_client,
                f'scrape_body_size_bytes{{job="{PROMETHEUS_JOB}"}}',
            )
            scrape_samples = _prometheus_query(
                prom_client,
                f'scrape_samples_scraped{{job="{PROMETHEUS_JOB}"}}',
            )
            rules_response = prom_client.get(
                "/api/v1/rules",
                params={"type": "alert"},
            )
            rules_response.raise_for_status()
            prometheus_checks = evaluate_prometheus_payloads(
                up,
                self_up,
                retention_seconds,
                retention_bytes,
                rules_response.json(),
                minimum_instances=args.minimum_instances,
            )
            prometheus_checks["prometheus_ready"] = ready_response.status_code == 200
            prometheus_checks.update(
                evaluate_scrape_budget_payloads(
                    scrape_sample_limit,
                    scrape_body_size,
                    scrape_samples,
                    minimum_instances=args.minimum_instances,
                )
            )
            if args.require_acceptance_events:
                before = _read_acceptance_snapshot(
                    prom_client,
                    evaluation_time=args.acceptance_baseline_time,
                )
                after = _read_acceptance_snapshot(prom_client)
                after_time = _finite_number(after.get("evaluation_time"))
                if after_time is None:
                    raise ValueError("acceptance final time is invalid")
                status_payload, admin_payloads = _capture_admin_payloads_after(
                    admin_base_url,
                    username=username,
                    password=password,
                    windows=windows,
                    minimum_generated_at=after_time,
                )
                prometheus_checks[
                    "acceptance_admin_snapshots_follow_counter_snapshot"
                ] = _admin_snapshots_follow_counter_snapshot(
                    admin_payloads,
                    windows,
                    minimum_generated_at=after_time,
                )
                prometheus_checks.update(
                    evaluate_acceptance_event_delta(
                        status_payload,
                        before,
                        after,
                        expected_release_commit=args.expected_release_commit,
                        expected_instance_count=args.minimum_instances,
                    )
                )
            direct_payloads = _read_direct_metrics(
                prom_client,
                admin_payloads,
                windows,
            )
            window_checks: dict[str, bool] = {}
            for window in windows:
                checks_for_window = evaluate_admin_payloads(
                    status_payload,
                    admin_payloads[window],
                    window_seconds=window,
                    minimum_instances=args.minimum_instances,
                    # Raw Counter deltas bind events to this run. The longest
                    # fixed admin window separately proves those events are
                    # visible through the dashboard data path even when a
                    # valid 30--900 second run outlives the 300s window.
                    require_acceptance_events=_requires_event_visibility(
                        window,
                        acceptance_final=args.require_acceptance_events,
                    ),
                    expected_release_commit=expected_release_commit,
                )
                checks_for_window.update(
                    evaluate_admin_prometheus_comparison(
                        admin_payloads[window],
                        direct_payloads[window],
                        window_seconds=window,
                    )
                )
                window_checks.update(
                    {
                        f"{window}s_{name}": value
                        for name, value in checks_for_window.items()
                    }
                )
            if args.capture_acceptance_baseline:
                # Establish the formal evidence boundary only after all static
                # admin/direct comparisons have completed. Events observed while
                # this command is still doing those checks belong to the baseline.
                final_status = _read_admin_status(
                    admin_base_url,
                    username=username,
                    password=password,
                )
                baseline, baseline_checks, _capture_started_at = (
                    _capture_fresh_acceptance_baseline(
                        prom_client,
                        final_status,
                        expected_release_commit=args.expected_release_commit,
                        expected_instance_count=args.minimum_instances,
                    )
                )
                baseline_guard_started_at = _prometheus_time(prom_client)
                baseline_guard, baseline_guard_checks = _capture_acceptance_guard(
                    prom_client,
                    final_status,
                    baseline,
                    expected_release_commit=args.expected_release_commit,
                    expected_instance_count=args.minimum_instances,
                    minimum_scrape_time=baseline_guard_started_at,
                )
                prometheus_checks.update(baseline_checks)
                prometheus_checks.update(
                    {
                        name.replace(
                            "acceptance_guard_",
                            "acceptance_baseline_guard_",
                            1,
                        ): value
                        for name, value in baseline_guard_checks.items()
                    }
                )
                acceptance_baseline_time = baseline_guard["evaluation_time"]
            if args.require_acceptance_events:
                admin_snapshot_times = _admin_snapshot_times(
                    admin_payloads,
                    windows,
                )
                if admin_snapshot_times is None:
                    raise ValueError("admin metric generation time is invalid")
                guard_capture_started_at = _prometheus_time(prom_client)
                guard_minimum_scrape_time = max(
                    *admin_snapshot_times.values(),
                    guard_capture_started_at,
                )
                guard, guard_checks = _capture_acceptance_guard(
                    prom_client,
                    status_payload,
                    after,
                    expected_release_commit=args.expected_release_commit,
                    expected_instance_count=args.minimum_instances,
                    minimum_scrape_time=guard_minimum_scrape_time,
                )
                prometheus_checks.update(guard_checks)
                completion_status = _read_admin_status(
                    admin_base_url,
                    username=username,
                    password=password,
                )
                completion_event_checks = evaluate_acceptance_event_delta(
                    completion_status,
                    before,
                    after,
                    expected_release_commit=args.expected_release_commit,
                    expected_instance_count=args.minimum_instances,
                )
                completion_guard_checks = evaluate_acceptance_guard(
                    completion_status,
                    after,
                    guard,
                    expected_release_commit=args.expected_release_commit,
                    expected_instance_count=args.minimum_instances,
                    minimum_scrape_time=guard_minimum_scrape_time,
                )
                completion_admin_checks = []
                for window in windows:
                    completion_admin_checks.extend(
                        evaluate_admin_payloads(
                            completion_status,
                            admin_payloads[window],
                            window_seconds=window,
                            minimum_instances=args.minimum_instances,
                            require_acceptance_events=_requires_event_visibility(
                                window,
                                acceptance_final=True,
                            ),
                            expected_release_commit=expected_release_commit,
                        ).values()
                    )
                prometheus_checks[
                    "acceptance_status_stable_through_completion"
                ] = all(
                    (
                        *completion_event_checks.values(),
                        *completion_guard_checks.values(),
                        *completion_admin_checks,
                    )
                )
    except (
        ValueError,
        httpx.HTTPError,
        json.JSONDecodeError,
    ) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(exc).__name__,
                }
            )
        )
        return 1

    checks = {**window_checks, **prometheus_checks}
    ok = all(checks.values())
    output: dict[str, Any] = {"ok": ok, "checks": checks}
    if ok and acceptance_baseline_time is not None:
        output["acceptance_baseline_time"] = acceptance_baseline_time
    print(json.dumps(output, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
