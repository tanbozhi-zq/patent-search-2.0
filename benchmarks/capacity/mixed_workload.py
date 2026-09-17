#!/usr/bin/env python3
"""Measure whether long searches crowd out lightweight patent endpoints."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
from time import perf_counter, sleep
from typing import Any
from urllib.parse import quote

import httpx

from benchmarks.capacity.benchmark_lib import (
    InFlightTracker,
    OpenSearchMetricsSampler,
    evaluate_capacity_run,
    file_sha256,
    load_policy,
    now_iso,
    run_id,
    summarize_http_records,
    summarize_opensearch_samples,
)


LIGHT_ENDPOINTS_PER_REPETITION = 3
MAX_MIXED_CLIENT_CONCURRENCY = 64
MAX_MIXED_TOTAL_REQUESTS = 256


def run_request(
    client: httpx.Client,
    trackers: tuple[InFlightTracker, ...],
    base_url: str,
    api_token: str,
    phase: str,
    workload_class: str,
    name: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None,
    origin: float,
) -> dict[str, Any]:
    started_at = now_iso()
    started_tick = perf_counter()
    parsed: dict[str, Any] = {}
    status: int | None = None
    client_error: str | None = None
    response_header_request_id: str | None = None
    response_body_request_id: Any = None
    retry_after: str | None = None
    try:
        with ExitStack() as stack:
            for tracker in trackers:
                stack.enter_context(tracker.slot())
            response = client.request(
                method,
                f"{base_url.rstrip('/')}{path}",
                headers={"X-API-Key": api_token},
                json=payload,
            )
        status = response.status_code
        response_header_request_id = response.headers.get("X-Request-ID")
        retry_after = response.headers.get("Retry-After")
        try:
            body = response.json()
            if isinstance(body, dict):
                parsed = body
                response_body_request_id = body.get("request_id")
        except ValueError:
            parsed = {"body_preview": response.text[:500]}
    except Exception as exc:
        client_error = f"{type(exc).__name__}: {exc}"
    finished_tick = perf_counter()
    return {
        "phase": phase,
        "workload_class": workload_class,
        "name": name,
        "method": method,
        "path": path,
        "started_at": started_at,
        "started_offset_seconds": round(started_tick - origin, 6),
        "finished_offset_seconds": round(finished_tick - origin, 6),
        "elapsed_seconds": round(finished_tick - started_tick, 6),
        "status": status,
        "client_error": client_error,
        "request_id": response_header_request_id or response_body_request_id,
        "response_header_request_id": response_header_request_id,
        "response_body_request_id": response_body_request_id,
        "server_code": parsed.get("code"),
        "server_message": parsed.get("message"),
        "server_retryable": parsed.get("retryable"),
        "retry_after": retry_after,
    }


def light_specs(patent_id: str, repetitions: int) -> list[dict[str, Any]]:
    encoded = quote(patent_id, safe="")
    endpoints = (
        ("detail", f"/api/patent/detail/{encoded}"),
        ("citations", f"/api/patent/citations/{encoded}"),
        ("legal_history", f"/api/patent/legal-history/{encoded}"),
    )
    return [
        {"name": name, "method": "GET", "path": path, "payload": None}
        for _ in range(repetitions)
        for name, path in endpoints
    ]


def heavy_specs(plan: dict[str, Any], concurrency: int) -> list[dict[str, Any]]:
    preferred = [
        query
        for query in plan["queries"]
        if query.get("channel") in {"bqp", "broad"}
    ]
    if not preferred:
        raise ValueError("query plan has no broad or bqp queries for the heavy phase")
    specs = []
    for index in range(concurrency):
        item = preferred[index % len(preferred)]
        specs.append(
            {
                "name": f"search_{index}",
                "method": "POST",
                "path": "/api/patent/search",
                "payload": {
                    "q": item["query"],
                    "ds": item.get("ds", "all"),
                    "sort": item.get("sort", "relation"),
                    "page": 1,
                    "page_size": min(int(item.get("max_patents", 50)), 100),
                    "highlight": 0,
                },
            }
        )
    return specs


def execute_specs(
    executor: ThreadPoolExecutor,
    client: httpx.Client,
    trackers: tuple[InFlightTracker, ...],
    base_url: str,
    api_token: str,
    phase: str,
    workload_class: str,
    specs: list[dict[str, Any]],
    origin: float,
) -> list[Future[dict[str, Any]]]:
    return [
        executor.submit(
            run_request,
            client,
            trackers,
            base_url,
            api_token,
            phase,
            workload_class,
            spec["name"],
            spec["method"],
            spec["path"],
            spec["payload"],
            origin,
        )
        for spec in specs
    ]


def overlap_count(
    light_records: list[dict[str, Any]], heavy_records: list[dict[str, Any]]
) -> int:
    return sum(
        any(
            heavy["started_offset_seconds"] <= light["finished_offset_seconds"]
            and heavy["finished_offset_seconds"] >= light["started_offset_seconds"]
            for heavy in heavy_records
        )
        for light in light_records
    )


def evaluate_mixed_workload(
    baseline_light_metrics: dict[str, Any],
    mixed_light_metrics: dict[str, Any],
    guardrail_decision: dict[str, Any],
    confirmed_overlap: int,
    policy: dict[str, Any],
) -> dict[str, Any]:
    request_count = int(mixed_light_metrics["request_count"])
    baseline_p95 = baseline_light_metrics["latency_seconds"]["p95"]
    mixed_p95 = mixed_light_metrics["latency_seconds"]["p95"]
    p95_growth = (
        round((mixed_p95 / baseline_p95) - 1.0, 6)
        if baseline_p95 not in (None, 0) and mixed_p95 is not None
        else None
    )
    baseline_failed = baseline_light_metrics["failure_count"] > 0
    mixed_failed = mixed_light_metrics["failure_count"] > 0
    p95_exceeded = bool(
        p95_growth is not None
        and p95_growth > float(policy["mixed"]["max_light_p95_growth_ratio"])
    )
    reasons = []
    if baseline_failed:
        reasons.append("standalone_light_baseline_failed")
    if confirmed_overlap != request_count:
        reasons.append("heavy_light_overlap_not_confirmed")
    if mixed_failed:
        reasons.append("overlapped_light_request_failed")
    if p95_exceeded:
        reasons.append("light_p95_growth_exceeded")
    if not guardrail_decision["safe_to_continue"]:
        reasons.append("application_or_opensearch_guardrail_failed")
    requires_isolation = bool(
        not baseline_failed
        and confirmed_overlap > 0
        and (mixed_failed or p95_exceeded)
    )
    return {
        "safe_to_release_shared_bulkhead": not reasons,
        "requires_workload_isolation": requires_isolation,
        "workload_stop_reasons": reasons,
        "light_requests_overlapping_heavy": confirmed_overlap,
        "light_request_count": request_count,
        "light_p95_growth_ratio": p95_growth,
        "guardrail_decision": guardrail_decision,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--plan",
        type=Path,
        default=Path("benchmarks/capacity/query_plan.json"),
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--patent-id", required=True)
    parser.add_argument("--heavy-concurrency", type=int, required=True)
    parser.add_argument("--light-repetitions", type=int, default=5)
    parser.add_argument("--light-concurrency", type=int, default=3)
    parser.add_argument("--heavy-settle-seconds", type=float, default=0.25)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path("benchmarks/capacity/capacity_policy.json"),
    )
    parser.add_argument("--opensearch-url", required=True)
    parser.add_argument("--opensearch-user-env", default="OPENSEARCH_USER")
    parser.add_argument("--opensearch-pass-env", default="OPENSEARCH_PASS")
    parser.add_argument("--opensearch-insecure", action="store_true")
    parser.add_argument("--metrics-interval", type=float, default=2.0)
    parser.add_argument("--metrics-timeout", type=float, default=10.0)
    parser.add_argument("--post-observation-seconds", type=float, default=10.0)
    parser.add_argument("--service-version", required=True)
    parser.add_argument("--service-commit", required=True)
    parser.add_argument("--read-target", required=True)
    parser.add_argument("--bulkhead-capacity", type=int, required=True)
    parser.add_argument("--heavy-bulkhead-capacity", type=int, required=True)
    parser.add_argument("--opensearch-pool-maxsize", type=int, required=True)
    parser.add_argument("--worker-count", type=int, required=True)
    args = parser.parse_args()
    for name in ("heavy_concurrency", "light_repetitions", "light_concurrency"):
        if getattr(args, name) < 1:
            parser.error(f"--{name.replace('_', '-')} must be at least 1")
    if args.timeout <= 0 or args.metrics_interval <= 0 or args.metrics_timeout <= 0:
        parser.error("timeouts and metrics interval must be positive")
    if args.heavy_settle_seconds < 0 or args.post_observation_seconds < 0:
        parser.error("observation delays cannot be negative")
    if args.worker_count != 1:
        parser.error("the per-process mixed gate requires exactly one service worker")
    if args.heavy_concurrency > args.heavy_bulkhead_capacity:
        parser.error("--heavy-concurrency cannot exceed --heavy-bulkhead-capacity")
    if args.heavy_bulkhead_capacity >= args.bulkhead_capacity:
        parser.error("--heavy-bulkhead-capacity must be less than --bulkhead-capacity")
    reserved_light_capacity = args.bulkhead_capacity - args.heavy_bulkhead_capacity
    if args.light_concurrency > reserved_light_capacity:
        parser.error(
            "--light-concurrency cannot exceed reserved light capacity "
            "(--bulkhead-capacity - --heavy-bulkhead-capacity)"
        )
    if args.bulkhead_capacity > args.opensearch_pool_maxsize:
        parser.error("--bulkhead-capacity cannot exceed --opensearch-pool-maxsize")
    client_concurrency = args.heavy_concurrency + args.light_concurrency
    if client_concurrency > MAX_MIXED_CLIENT_CONCURRENCY:
        parser.error(
            "heavy plus light concurrency cannot exceed the controlled client "
            f"concurrency limit of {MAX_MIXED_CLIENT_CONCURRENCY}"
        )
    planned_service_request_count = args.heavy_concurrency + (
        args.light_repetitions * LIGHT_ENDPOINTS_PER_REPETITION * 2
    )
    if planned_service_request_count > MAX_MIXED_TOTAL_REQUESTS:
        parser.error(
            "planned baseline and mixed service requests cannot exceed the "
            f"controlled limit of {MAX_MIXED_TOTAL_REQUESTS}"
        )
    return args


def main() -> int:
    args = parse_args()
    api_token = os.environ.get("API_TOKEN", "")
    if not api_token:
        raise SystemExit("API_TOKEN is required in the environment")
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    if plan.get("total_queries") != len(plan.get("queries", [])):
        raise SystemExit("query count does not match total_queries")
    policy, policy_sha256 = load_policy(args.policy)

    current_run_id = run_id()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"mixed_h{args.heavy_concurrency}_{current_run_id}"
    details_path = args.output_dir / f"{prefix}.jsonl"
    metrics_path = args.output_dir / f"{prefix}_opensearch.jsonl"
    summary_path = args.output_dir / f"{prefix}_summary.json"
    username = os.environ.get(args.opensearch_user_env, "")
    password = os.environ.get(args.opensearch_pass_env, "")
    sampler = OpenSearchMetricsSampler(
        base_url=args.opensearch_url,
        output_path=metrics_path,
        interval_seconds=args.metrics_interval,
        timeout_seconds=args.metrics_timeout,
        username=username,
        password=password,
        verify_certs=not args.opensearch_insecure,
    )

    light_requests = light_specs(args.patent_id, args.light_repetitions)
    heavy_requests = heavy_specs(plan, args.heavy_concurrency)
    limits = httpx.Limits(
        max_connections=args.heavy_concurrency + args.light_concurrency,
        max_keepalive_connections=args.heavy_concurrency + args.light_concurrency,
    )
    timeout = httpx.Timeout(args.timeout, connect=min(args.timeout, 10.0))
    origin = perf_counter()
    started_at = now_iso()
    baseline_tracker = InFlightTracker()
    mixed_tracker = InFlightTracker()
    heavy_tracker = InFlightTracker()
    light_tracker = InFlightTracker()
    baseline_records: list[dict[str, Any]] = []
    mixed_light_records: list[dict[str, Any]] = []
    mixed_heavy_records: list[dict[str, Any]] = []

    sampler.start()
    try:
        with details_path.open("x", encoding="utf-8") as details_handle:
            with httpx.Client(timeout=timeout, limits=limits) as client:
                baseline_started = perf_counter()
                with ThreadPoolExecutor(max_workers=args.light_concurrency) as executor:
                    futures = execute_specs(
                        executor,
                        client,
                        (baseline_tracker,),
                        args.base_url,
                        api_token,
                        "baseline",
                        "light",
                        light_requests,
                        origin,
                    )
                    for future in as_completed(futures):
                        record = future.result()
                        baseline_records.append(record)
                        details_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                        details_handle.flush()
                baseline_wall_seconds = perf_counter() - baseline_started

                mixed_started = perf_counter()
                with ThreadPoolExecutor(
                    max_workers=args.heavy_concurrency + args.light_concurrency
                ) as executor:
                    heavy_futures = execute_specs(
                        executor,
                        client,
                        (mixed_tracker, heavy_tracker),
                        args.base_url,
                        api_token,
                        "mixed",
                        "heavy",
                        heavy_requests,
                        origin,
                    )
                    if not heavy_tracker.wait_until_peak_at_least(
                        args.heavy_concurrency, timeout=min(args.timeout, 10.0)
                    ):
                        raise RuntimeError(
                            "heavy requests did not overlap at the requested concurrency"
                        )
                    if args.heavy_settle_seconds:
                        sleep(args.heavy_settle_seconds)
                    light_futures = execute_specs(
                        executor,
                        client,
                        (mixed_tracker, light_tracker),
                        args.base_url,
                        api_token,
                        "mixed",
                        "light",
                        light_requests,
                        origin,
                    )
                    for future in as_completed(light_futures):
                        record = future.result()
                        mixed_light_records.append(record)
                        details_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                        details_handle.flush()
                    for future in as_completed(heavy_futures):
                        record = future.result()
                        mixed_heavy_records.append(record)
                        details_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                        details_handle.flush()
                mixed_wall_seconds = perf_counter() - mixed_started
        if args.post_observation_seconds:
            sleep(args.post_observation_seconds)
    finally:
        sampler.stop()

    baseline_metrics = summarize_http_records(
        baseline_records, baseline_wall_seconds, baseline_tracker.peak
    )
    mixed_light_metrics = summarize_http_records(
        mixed_light_records, mixed_wall_seconds, light_tracker.peak
    )
    mixed_heavy_metrics = summarize_http_records(
        mixed_heavy_records, mixed_wall_seconds, heavy_tracker.peak
    )
    all_mixed_records = mixed_light_records + mixed_heavy_records
    mixed_metrics = summarize_http_records(
        all_mixed_records, mixed_wall_seconds, mixed_tracker.peak
    )
    opensearch_metrics = summarize_opensearch_samples(sampler.samples)
    guardrail_decision = evaluate_capacity_run(
        mixed_metrics,
        opensearch_metrics,
        policy,
    )
    confirmed_overlap = overlap_count(mixed_light_records, mixed_heavy_records)
    decision = evaluate_mixed_workload(
        baseline_metrics,
        mixed_light_metrics,
        guardrail_decision,
        confirmed_overlap,
        policy,
    )
    summary = {
        "run": {
            "run_id": current_run_id,
            "started_at": started_at,
            "finished_at": now_iso(),
            "base_url": args.base_url,
            "patent_id": args.patent_id,
            "heavy_concurrency": args.heavy_concurrency,
            "light_repetitions_per_endpoint": args.light_repetitions,
            "light_concurrency": args.light_concurrency,
            "planned_service_request_count": args.heavy_concurrency
            + args.light_repetitions * LIGHT_ENDPOINTS_PER_REPETITION * 2,
            "plan_sha256": file_sha256(args.plan),
            "policy_sha256": policy_sha256,
            "service_version": args.service_version,
            "service_commit": args.service_commit,
            "read_target": args.read_target,
            "bulkhead_capacity": args.bulkhead_capacity,
            "heavy_bulkhead_capacity": args.heavy_bulkhead_capacity,
            "opensearch_pool_maxsize": args.opensearch_pool_maxsize,
            "worker_count": args.worker_count,
            "details_file": str(details_path.resolve()),
            "opensearch_metrics_file": str(metrics_path.resolve()),
        },
        "baseline_light_metrics": baseline_metrics,
        "mixed_light_metrics": mixed_light_metrics,
        "mixed_heavy_metrics": mixed_heavy_metrics,
        "mixed_all_metrics": mixed_metrics,
        "opensearch_metrics": opensearch_metrics,
        "decision": decision,
        "application_bulkhead_log": {
            "status": "pending_operator_export",
            "instruction": "export service logs for the run window and summarize them",
        },
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(f"SUMMARY_FILE={summary_path.resolve()}", flush=True)
    return 0 if decision["safe_to_release_shared_bulkhead"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
