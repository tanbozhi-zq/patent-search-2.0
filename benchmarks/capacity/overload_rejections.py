#!/usr/bin/env python3
"""Measure real heavy and global bulkhead rejection latency on a candidate service."""

from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
import json
import math
import os
from pathlib import Path
from time import perf_counter, sleep
from typing import Any, TextIO
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
from benchmarks.capacity.mixed_workload import (
    execute_specs,
    heavy_specs,
    overlap_count,
)


MAX_REJECTION_REQUESTS = 32
MAX_OVERLOAD_CLIENT_CONCURRENCY = 64


def _renamed_specs(
    specs: list[dict[str, Any]], prefix: str
) -> list[dict[str, Any]]:
    return [
        {**spec, "name": f"{prefix}_{index}"}
        for index, spec in enumerate(specs)
    ]


def light_holder_specs(patent_id: str, count: int) -> list[dict[str, Any]]:
    encoded = quote(patent_id, safe="")
    return [
        {
            "name": f"global_light_holder_{index}",
            "method": "GET",
            "path": f"/api/patent/detail/{encoded}?include_description=true",
            "payload": None,
        }
        for index in range(count)
    ]


def light_overload_specs(patent_id: str, count: int) -> list[dict[str, Any]]:
    encoded = quote(patent_id, safe="")
    return [
        {
            "name": f"global_overload_{index}",
            "method": "GET",
            "path": f"/api/patent/detail/{encoded}",
            "payload": None,
        }
        for index in range(count)
    ]


def _collect(
    futures: list[Future[dict[str, Any]]],
    details_handle: TextIO,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for future in as_completed(futures):
        record = future.result()
        records.append(record)
        details_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        details_handle.flush()
    return records


def run_overload_phase(
    *,
    client: httpx.Client,
    base_url: str,
    api_token: str,
    phase: str,
    holder_batches: list[tuple[str, list[dict[str, Any]]]],
    overload_workload_class: str,
    overload_specs: list[dict[str, Any]],
    holder_capacity: int,
    holder_settle_seconds: float,
    timeout_seconds: float,
    origin: float,
    details_handle: TextIO,
) -> dict[str, Any]:
    phase_tracker = InFlightTracker()
    holder_tracker = InFlightTracker()
    overload_tracker = InFlightTracker()
    holder_futures: list[Future[dict[str, Any]]] = []
    started = perf_counter()

    with ThreadPoolExecutor(
        max_workers=holder_capacity + len(overload_specs)
    ) as executor:
        for workload_class, specs in holder_batches:
            holder_futures.extend(
                execute_specs(
                    executor,
                    client,
                    (phase_tracker, holder_tracker),
                    base_url,
                    api_token,
                    phase,
                    workload_class,
                    specs,
                    origin,
                )
            )
        holder_peak_reached = holder_tracker.wait_until_peak_at_least(
            holder_capacity,
            timeout=min(timeout_seconds, 10.0),
        )
        if holder_settle_seconds:
            sleep(holder_settle_seconds)
        overload_futures = execute_specs(
            executor,
            client,
            (phase_tracker, overload_tracker),
            base_url,
            api_token,
            phase,
            overload_workload_class,
            overload_specs,
            origin,
        )
        overload_records = _collect(overload_futures, details_handle)
        holder_records = _collect(holder_futures, details_handle)

    return {
        "holder_records": holder_records,
        "overload_records": overload_records,
        "wall_seconds": perf_counter() - started,
        "client_peak_in_flight": phase_tracker.peak,
        "client_holder_peak": holder_tracker.peak,
        "client_overload_peak": overload_tracker.peak,
        "client_holder_peak_reached": holder_peak_reached,
    }


def _phase_acceptance(
    phase: str,
    holder_records: list[dict[str, Any]],
    overload_records: list[dict[str, Any]],
    rejection_slo_seconds: float,
) -> tuple[dict[str, Any], list[str]]:
    reasons: list[str] = []
    holder_failures = sum(record.get("status") != 200 for record in holder_records)
    rejection_contract_count = sum(
        record.get("status") == 503
        and record.get("server_code") == 50301
        and record.get("server_retryable") is True
        and record.get("retry_after") == "1"
        and isinstance(record.get("response_header_request_id"), str)
        and bool(record["response_header_request_id"].strip())
        and isinstance(record.get("response_body_request_id"), str)
        and bool(record["response_body_request_id"].strip())
        and record["response_header_request_id"]
        == record["response_body_request_id"]
        and not record.get("client_error")
        for record in overload_records
    )
    rejection_metrics = summarize_http_records(
        overload_records,
        wall_seconds=max(
            (float(record["elapsed_seconds"]) for record in overload_records),
            default=0.0,
        ),
        client_peak_in_flight=len(overload_records),
    )
    rejection_latencies = sorted(
        float(record["elapsed_seconds"])
        for record in overload_records
        if record.get("server_code") == 50301
    )
    rejection_p99 = (
        rejection_latencies[max(0, math.ceil(len(rejection_latencies) * 0.99) - 1)]
        if rejection_latencies
        else None
    )
    rejection_latency_summary = dict(
        rejection_metrics["bulkhead_rejections"]["latency_seconds"]
    )
    rejection_latency_summary["p99"] = rejection_p99
    overlapped = overlap_count(overload_records, holder_records)

    if holder_failures:
        reasons.append(f"{phase}_holder_request_failed")
    if not overload_records or rejection_contract_count != len(overload_records):
        reasons.append(f"{phase}_overload_not_all_50301")
    if overlapped != len(overload_records):
        reasons.append(f"{phase}_holder_overlap_not_confirmed")
    if rejection_p99 is None or rejection_p99 > rejection_slo_seconds:
        reasons.append(f"{phase}_rejection_p99_exceeded")

    return (
        {
            "holder_request_count": len(holder_records),
            "holder_failure_count": holder_failures,
            "overload_request_count": len(overload_records),
            "rejection_contract_count": rejection_contract_count,
            "overload_requests_overlapping_holders": overlapped,
            "rejection_latency_seconds": rejection_latency_summary,
            "passed": not reasons,
        },
        reasons,
    )


def evaluate_overload_acceptance(
    *,
    heavy_holder_records: list[dict[str, Any]],
    heavy_overload_records: list[dict[str, Any]],
    global_holder_records: list[dict[str, Any]],
    global_overload_records: list[dict[str, Any]],
    guardrail_decision: dict[str, Any],
    policy: dict[str, Any],
    deployed_acquire_timeout_seconds: float,
) -> dict[str, Any]:
    rejection_slo_seconds = float(
        policy["application"]["rejection_p99_slo_seconds"]
    )
    heavy, heavy_reasons = _phase_acceptance(
        "heavy",
        heavy_holder_records,
        heavy_overload_records,
        rejection_slo_seconds,
    )
    global_phase, global_reasons = _phase_acceptance(
        "global",
        global_holder_records,
        global_overload_records,
        rejection_slo_seconds,
    )
    reasons = [*heavy_reasons, *global_reasons]
    if deployed_acquire_timeout_seconds >= rejection_slo_seconds:
        reasons.append("configured_acquire_timeout_has_no_rejection_slo_headroom")
    if not guardrail_decision.get("safe_to_continue"):
        reasons.append("application_or_opensearch_guardrail_failed")
    return {
        "http_rejection_stage_passed": not reasons,
        "requires_server_log_confirmation": True,
        "stop_reasons": reasons,
        "rejection_p99_slo_seconds": rejection_slo_seconds,
        "deployed_acquire_timeout_seconds": deployed_acquire_timeout_seconds,
        "heavy": heavy,
        "global": global_phase,
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
    parser.add_argument("--rejection-requests", type=int, default=8)
    parser.add_argument("--holder-settle-seconds", type=float, default=0.05)
    parser.add_argument("--phase-gap-seconds", type=float, default=1.0)
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
    parser.add_argument(
        "--deployed-acquire-timeout-seconds",
        type=float,
        required=True,
    )
    parser.add_argument("--opensearch-pool-maxsize", type=int, required=True)
    parser.add_argument("--worker-count", type=int, required=True)
    args = parser.parse_args()

    if not 1 <= args.rejection_requests <= MAX_REJECTION_REQUESTS:
        parser.error(
            "--rejection-requests must be between 1 and "
            f"{MAX_REJECTION_REQUESTS}"
        )
    if args.timeout <= 0 or args.metrics_interval <= 0 or args.metrics_timeout <= 0:
        parser.error("timeouts and metrics interval must be positive")
    if (
        args.holder_settle_seconds < 0
        or args.phase_gap_seconds < 0
        or args.post_observation_seconds < 0
    ):
        parser.error("observation delays cannot be negative")
    if args.worker_count != 1:
        parser.error("the per-process overload gate requires exactly one service worker")
    if args.heavy_bulkhead_capacity < 1:
        parser.error("--heavy-bulkhead-capacity must be at least 1")
    if args.heavy_bulkhead_capacity >= args.bulkhead_capacity:
        parser.error("--heavy-bulkhead-capacity must be less than --bulkhead-capacity")
    if args.bulkhead_capacity > args.opensearch_pool_maxsize:
        parser.error("--bulkhead-capacity cannot exceed --opensearch-pool-maxsize")
    if (
        args.bulkhead_capacity + args.rejection_requests
        > MAX_OVERLOAD_CLIENT_CONCURRENCY
    ):
        parser.error(
            "global capacity plus rejection requests cannot exceed the controlled "
            f"client concurrency limit of {MAX_OVERLOAD_CLIENT_CONCURRENCY}"
        )
    if not 0 < args.deployed_acquire_timeout_seconds <= 0.1:
        parser.error("--deployed-acquire-timeout-seconds must be in (0, 0.1]")
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

    heavy_holders = _renamed_specs(
        heavy_specs(plan, args.heavy_bulkhead_capacity),
        "heavy_holder",
    )
    heavy_overload = _renamed_specs(
        heavy_specs(plan, args.rejection_requests),
        "heavy_overload",
    )
    reserved_light_capacity = (
        args.bulkhead_capacity - args.heavy_bulkhead_capacity
    )
    global_heavy_holders = _renamed_specs(
        heavy_specs(plan, args.heavy_bulkhead_capacity),
        "global_heavy_holder",
    )
    global_light_holders = light_holder_specs(
        args.patent_id,
        reserved_light_capacity,
    )
    global_overload = light_overload_specs(
        args.patent_id,
        args.rejection_requests,
    )

    current_run_id = run_id()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"overload_{current_run_id}"
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
    max_connections = args.bulkhead_capacity + args.rejection_requests
    limits = httpx.Limits(
        max_connections=max_connections,
        max_keepalive_connections=max_connections,
    )
    timeout = httpx.Timeout(args.timeout, connect=min(args.timeout, 10.0))
    origin = perf_counter()
    started_at = now_iso()

    sampler.start()
    try:
        with details_path.open("x", encoding="utf-8") as details_handle:
            with httpx.Client(timeout=timeout, limits=limits) as client:
                heavy_phase = run_overload_phase(
                    client=client,
                    base_url=args.base_url,
                    api_token=api_token,
                    phase="heavy_overload",
                    holder_batches=[("heavy_holder", heavy_holders)],
                    overload_workload_class="heavy_overload",
                    overload_specs=heavy_overload,
                    holder_capacity=args.heavy_bulkhead_capacity,
                    holder_settle_seconds=args.holder_settle_seconds,
                    timeout_seconds=args.timeout,
                    origin=origin,
                    details_handle=details_handle,
                )
                if args.phase_gap_seconds:
                    sleep(args.phase_gap_seconds)
                global_phase = run_overload_phase(
                    client=client,
                    base_url=args.base_url,
                    api_token=api_token,
                    phase="global_overload",
                    holder_batches=[
                        ("global_heavy_holder", global_heavy_holders),
                        ("global_light_holder", global_light_holders),
                    ],
                    overload_workload_class="global_overload",
                    overload_specs=global_overload,
                    holder_capacity=args.bulkhead_capacity,
                    holder_settle_seconds=args.holder_settle_seconds,
                    timeout_seconds=args.timeout,
                    origin=origin,
                    details_handle=details_handle,
                )
        if args.post_observation_seconds:
            sleep(args.post_observation_seconds)
    finally:
        sampler.stop()

    all_holders = (
        heavy_phase["holder_records"] + global_phase["holder_records"]
    )
    holder_metrics = summarize_http_records(
        all_holders,
        wall_seconds=heavy_phase["wall_seconds"] + global_phase["wall_seconds"],
        client_peak_in_flight=max(
            heavy_phase["client_holder_peak"],
            global_phase["client_holder_peak"],
        ),
    )
    opensearch_metrics = summarize_opensearch_samples(sampler.samples)
    guardrail_decision = evaluate_capacity_run(
        holder_metrics,
        opensearch_metrics,
        policy,
    )
    decision = evaluate_overload_acceptance(
        heavy_holder_records=heavy_phase["holder_records"],
        heavy_overload_records=heavy_phase["overload_records"],
        global_holder_records=global_phase["holder_records"],
        global_overload_records=global_phase["overload_records"],
        guardrail_decision=guardrail_decision,
        policy=policy,
        deployed_acquire_timeout_seconds=(
            args.deployed_acquire_timeout_seconds
        ),
    )

    def phase_summary(phase: dict[str, Any]) -> dict[str, Any]:
        return {
            "client_holder_peak_reached": phase["client_holder_peak_reached"],
            "client_peak_in_flight": phase["client_peak_in_flight"],
            "holder_metrics": summarize_http_records(
                phase["holder_records"],
                phase["wall_seconds"],
                phase["client_holder_peak"],
            ),
            "overload_metrics": summarize_http_records(
                phase["overload_records"],
                phase["wall_seconds"],
                phase["client_overload_peak"],
            ),
        }

    summary = {
        "run": {
            "run_id": current_run_id,
            "started_at": started_at,
            "finished_at": now_iso(),
            "base_url": args.base_url,
            "patent_id": args.patent_id,
            "rejection_requests_per_phase": args.rejection_requests,
            "holder_settle_seconds": args.holder_settle_seconds,
            "plan_sha256": file_sha256(args.plan),
            "policy_sha256": policy_sha256,
            "service_version": args.service_version,
            "service_commit": args.service_commit,
            "read_target": args.read_target,
            "bulkhead_capacity": args.bulkhead_capacity,
            "heavy_bulkhead_capacity": args.heavy_bulkhead_capacity,
            "deployed_acquire_timeout_seconds": (
                args.deployed_acquire_timeout_seconds
            ),
            "opensearch_pool_maxsize": args.opensearch_pool_maxsize,
            "worker_count": args.worker_count,
            "details_file": str(details_path.resolve()),
            "opensearch_metrics_file": str(metrics_path.resolve()),
        },
        "heavy_phase": phase_summary(heavy_phase),
        "global_phase": phase_summary(global_phase),
        "opensearch_metrics": opensearch_metrics,
        "decision": decision,
        "application_bulkhead_log": {
            "status": "pending_operator_export",
            "expected": {
                "global_capacity": args.bulkhead_capacity,
                "heavy_capacity": args.heavy_bulkhead_capacity,
                "acquire_timeout_seconds": (
                    args.deployed_acquire_timeout_seconds
                ),
            },
            "instruction": (
                "export the service log for this exact run window, summarize it, "
                "and confirm both capacities, acquire_timeout_seconds, rejection "
                "events, peak in-flight, and final in-flight=0"
            ),
        },
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(f"SUMMARY_FILE={summary_path.resolve()}", flush=True)
    print(
        "HTTP_REJECTION_STAGE_PASSED="
        f"{str(decision['http_rejection_stage_passed']).lower()}",
        flush=True,
    )
    return 0 if decision["http_rejection_stage_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
