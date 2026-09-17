#!/usr/bin/env python3
"""Run one human-gated Issue #34 capacity level with OpenSearch telemetry."""

from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
from time import perf_counter, sleep
from typing import Any

import httpx

from benchmarks.capacity.benchmark_lib import (
    InFlightTracker,
    OpenSearchMetricsSampler,
    evaluate_capacity_run,
    file_sha256,
    load_policy,
    now_iso,
    percentile,
    run_id,
    summarize_http_records,
    summarize_opensearch_samples,
)


def run_one(
    client: httpx.Client,
    tracker: InFlightTracker,
    base_url: str,
    api_token: str,
    index: int,
    item: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "q": item["query"],
        "ds": item.get("ds", "all"),
        "sort": item.get("sort", "relation"),
        "page": 1,
        "page_size": min(int(item.get("max_patents", 50)), 100),
        "highlight": 0,
    }
    started_at = now_iso()
    started = perf_counter()
    status: int | None = None
    response_bytes = 0
    parsed: dict[str, Any] = {}
    client_error: str | None = None
    request_id: str | None = None
    try:
        with tracker.slot():
            response = client.post(
                f"{base_url.rstrip('/')}/api/patent/search",
                headers={"X-API-Key": api_token},
                json=payload,
            )
        status = response.status_code
        response_bytes = len(response.content)
        request_id = response.headers.get("X-Request-ID")
        try:
            body = response.json()
            if isinstance(body, dict):
                parsed = body
        except ValueError:
            parsed = {"body_preview": response.text[:500]}
    except Exception as exc:  # Preserve the exact client-side failure class.
        client_error = f"{type(exc).__name__}: {exc}"
    elapsed = round(perf_counter() - started, 3)
    records = parsed.get("records")
    return {
        "index": index,
        "started_at": started_at,
        "finished_at": now_iso(),
        "elapsed_seconds": elapsed,
        "status": status,
        "client_error": client_error,
        "request_id": request_id or parsed.get("request_id"),
        "server_code": parsed.get("code"),
        "server_message": parsed.get("message"),
        "server_took_ms": parsed.get("took_ms"),
        "total_hits": parsed.get("total"),
        "record_count": len(records) if isinstance(records, list) else None,
        "response_bytes": response_bytes,
        "channel": item.get("channel"),
        "mode": item.get("mode"),
        "element_id": item.get("element_id"),
        "ipc_used": item.get("ipc_used"),
        "sort": item.get("sort"),
        "query": item["query"],
    }


def grouped_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, Any] = {}
    for field in ("channel", "mode"):
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in results:
            buckets[str(item.get(field))].append(item)
        grouped[field] = {
            name: {
                "count": len(items),
                "success": sum(item["status"] == 200 for item in items),
                "failure": sum(item["status"] != 200 for item in items),
                "bulkhead_rejections": sum(
                    item.get("server_code") == 50301 for item in items
                ),
                "p50_seconds": percentile(
                    [float(item["elapsed_seconds"]) for item in items], 0.50
                ),
                "p95_seconds": percentile(
                    [float(item["elapsed_seconds"]) for item in items], 0.95
                ),
                "p99_seconds": percentile(
                    [float(item["elapsed_seconds"]) for item in items], 0.99
                ),
                "max_seconds": max(
                    (float(item["elapsed_seconds"]) for item in items), default=0.0
                ),
            }
            for name, items in sorted(buckets.items())
        }
    return grouped


def load_previous_summary(
    path: Path | None,
    concurrency: int,
    expected_run_context: dict[str, Any],
    heap_guardrail_override_reason: str | None = None,
) -> dict[str, Any] | None:
    if path is None:
        return None
    previous = json.loads(path.read_text(encoding="utf-8"))
    previous_decision = previous.get("decision", {})
    if not previous_decision.get("safe_to_continue"):
        stop_reasons = previous_decision.get("stop_reasons", [])
        if not heap_guardrail_override_reason or not heap_guardrail_override_reason.strip():
            raise ValueError("previous level is not safe_to_continue")
        if stop_reasons != ["opensearch_heap_guardrail"]:
            raise ValueError(
                "operator override only permits a previous opensearch_heap_guardrail"
            )
    elif heap_guardrail_override_reason:
        raise ValueError("operator override is unnecessary for a safe previous level")
    previous_concurrency = previous.get("run", {}).get("concurrency")
    if not isinstance(previous_concurrency, int) or previous_concurrency >= concurrency:
        raise ValueError("previous level concurrency must be lower than this level")
    previous_run = previous.get("run", {})
    for key, expected_value in expected_run_context.items():
        if previous_run.get(key) != expected_value:
            raise ValueError(f"previous level uses a different {key}")
    return previous


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, default=Path("benchmarks/capacity/query_plan.json"))
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path("benchmarks/capacity/capacity_policy.json"),
    )
    parser.add_argument("--previous-summary", type=Path)
    parser.add_argument(
        "--previous-heap-guardrail-override-reason",
        help=(
            "operator approval for continuing only when the previous level stopped "
            "solely on opensearch_heap_guardrail"
        ),
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
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    if args.timeout <= 0 or args.metrics_interval <= 0 or args.metrics_timeout <= 0:
        parser.error("timeouts and metrics interval must be positive")
    if args.post_observation_seconds < 0:
        parser.error("--post-observation-seconds cannot be negative")
    if args.worker_count != 1:
        parser.error("the per-process capacity gate requires exactly one service worker")
    if args.heavy_bulkhead_capacity < args.concurrency:
        parser.error(
            "--heavy-bulkhead-capacity must cover the offered search concurrency"
        )
    if args.heavy_bulkhead_capacity >= args.bulkhead_capacity:
        parser.error("--heavy-bulkhead-capacity must be less than --bulkhead-capacity")
    if args.bulkhead_capacity > args.opensearch_pool_maxsize:
        parser.error("--bulkhead-capacity cannot exceed --opensearch-pool-maxsize")
    return args


def main() -> int:
    args = parse_args()
    api_token = os.environ.get("API_TOKEN", "")
    if not api_token:
        raise SystemExit("API_TOKEN is required in the environment")

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    queries = plan["queries"]
    if plan.get("total_queries") != len(queries):
        raise SystemExit("query count does not match total_queries")
    policy, policy_sha256 = load_policy(args.policy)
    levels = policy["levels"]
    if args.concurrency not in levels:
        raise SystemExit(f"--concurrency must be one of {levels}")
    level_index = levels.index(args.concurrency)
    if level_index == 0 and args.previous_summary is not None:
        raise SystemExit("the first capacity level must not have --previous-summary")
    if level_index == 0 and args.previous_heap_guardrail_override_reason:
        raise SystemExit("the first capacity level cannot use a previous-level override")
    if level_index > 0 and args.previous_summary is None:
        raise SystemExit("higher capacity levels require --previous-summary")
    plan_sha256 = file_sha256(args.plan)
    previous = load_previous_summary(
        args.previous_summary,
        args.concurrency,
        {
            "base_url": args.base_url,
            "plan_sha256": plan_sha256,
            "policy_sha256": policy_sha256,
            "service_version": args.service_version,
            "service_commit": args.service_commit,
            "read_target": args.read_target,
            "bulkhead_capacity": args.bulkhead_capacity,
            "heavy_bulkhead_capacity": args.heavy_bulkhead_capacity,
            "opensearch_pool_maxsize": args.opensearch_pool_maxsize,
            "worker_count": args.worker_count,
        },
        args.previous_heap_guardrail_override_reason,
    )
    if previous is not None and previous["run"]["concurrency"] != levels[level_index - 1]:
        raise SystemExit("--previous-summary must be the immediately preceding level")

    current_run_id = run_id()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"search_c{args.concurrency}_{current_run_id}"
    details_path = args.output_dir / f"{prefix}.jsonl"
    metrics_path = args.output_dir / f"{prefix}_opensearch.jsonl"
    summary_path = args.output_dir / f"{prefix}_summary.json"
    tracker = InFlightTracker()
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

    limits = httpx.Limits(
        max_connections=args.concurrency,
        max_keepalive_connections=args.concurrency,
    )
    timeout = httpx.Timeout(args.timeout, connect=min(args.timeout, 10.0))
    run_started_at = now_iso()
    results: list[dict[str, Any]] = []
    sampler.start()
    try:
        with details_path.open("x", encoding="utf-8") as details_handle:
            with httpx.Client(timeout=timeout, limits=limits) as client:
                with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
                    workload_started_at = now_iso()
                    workload_started = perf_counter()
                    futures = {
                        executor.submit(
                            run_one,
                            client,
                            tracker,
                            args.base_url,
                            api_token,
                            index,
                            item,
                        ): index
                        for index, item in enumerate(queries)
                    }
                    for completed, future in enumerate(as_completed(futures), start=1):
                        result = future.result()
                        results.append(result)
                        details_handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                        details_handle.flush()
                        print(
                            f"[{completed:02d}/{len(queries)}] idx={result['index']:02d} "
                            f"status={result['status'] or 'CLIENT_ERROR'} "
                            f"elapsed={result['elapsed_seconds']:.3f}s "
                            f"channel={result['channel']} mode={result['mode']}",
                            flush=True,
                        )
                    workload_wall_seconds = perf_counter() - workload_started
        if args.post_observation_seconds:
            sleep(args.post_observation_seconds)
    finally:
        sampler.stop()

    results.sort(key=lambda item: item["index"])
    request_metrics = summarize_http_records(
        results, workload_wall_seconds, tracker.peak
    )
    request_metrics["groups"] = grouped_summary(results)
    request_metrics["slowest"] = [
        {
            key: item.get(key)
            for key in (
                "index",
                "channel",
                "mode",
                "status",
                "server_code",
                "elapsed_seconds",
                "server_took_ms",
                "total_hits",
                "request_id",
                "query",
            )
        }
        for item in sorted(
            results, key=lambda value: value["elapsed_seconds"], reverse=True
        )[:15]
    ]
    opensearch_metrics = summarize_opensearch_samples(sampler.samples)
    decision = evaluate_capacity_run(
        request_metrics,
        opensearch_metrics,
        policy,
        previous_summary=previous,
    )
    summary = {
        "run": {
            "run_id": current_run_id,
            "started_at": run_started_at,
            "workload_started_at": workload_started_at,
            "finished_at": now_iso(),
            "base_url": args.base_url,
            "concurrency": args.concurrency,
            "client_timeout_seconds": args.timeout,
            "metrics_interval_seconds": args.metrics_interval,
            "post_observation_seconds": args.post_observation_seconds,
            "plan_version": plan.get("version"),
            "plan_sha256": plan_sha256,
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
            "previous_summary": (
                str(args.previous_summary.resolve()) if args.previous_summary else None
            ),
            "previous_heap_guardrail_override": (
                {
                    "reason": args.previous_heap_guardrail_override_reason.strip(),
                    "previous_stop_reasons": previous["decision"]["stop_reasons"],
                }
                if previous is not None
                and not previous["decision"].get("safe_to_continue")
                else None
            ),
        },
        "request_metrics": request_metrics,
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
    print(f"SAFE_TO_CONTINUE={str(decision['safe_to_continue']).lower()}", flush=True)
    return 0 if decision["safe_to_continue"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
