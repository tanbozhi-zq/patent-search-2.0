"""Run the fixed Issue 75 matrix through the public HTTP search endpoint."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import tempfile
from time import monotonic, sleep
from typing import Any, Iterator
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from app.query.budget import DEFAULT_QUERY_BUDGET
from benchmarks.capacity.benchmark_lib import file_sha256, latency_summary


QUERY_SET_SCHEMA = "issue75-semantic-performance-query-set.v1"
OUTPUT_SCHEMA = "issue75-semantic-performance-result.v1"
EXPECTED_CLUSTER_NAME = "issue75-semantic-fixture"
VECTOR_DIMENSIONS = 1024
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_ASSET = Path("deployment/opensearch/search_pipelines_v1.json")
IMPLEMENTATION_FILES = (
    Path("app/api/search.py"),
    Path("app/services/search_service.py"),
    Path("app/query/semantic_dsl_builder.py"),
    Path("app/repositories/opensearch_repo.py"),
    PIPELINE_ASSET,
    Path("benchmarks/semantic_search/controlled_app.py"),
    Path("benchmarks/semantic_search/semantic_search_performance.py"),
)
MODES = ("vector", "hybrid")
FIELD_SETS = (("abstract",), ("abstract", "main_claim"))
SORTS = ("relation", "!applicationDate")
TOP_K_VALUES = (20, 100)


def load_query_set(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != QUERY_SET_SCHEMA:
        raise ValueError(f"query set must use {QUERY_SET_SCHEMA}")
    queries = payload.get("queries")
    if not isinstance(queries, list) or len(queries) < 3:
        raise ValueError("query set must contain at least three queries")
    seen: set[str] = set()
    texts: set[str] = set()
    for item in queries:
        if not isinstance(item, dict):
            raise ValueError("every query must be an object")
        query_id = item.get("id")
        if not isinstance(query_id, str) or not query_id or query_id in seen:
            raise ValueError("query ids must be non-empty and unique")
        seen.add(query_id)
        q = item.get("q")
        semantic_text = item.get("semantic_text")
        if not isinstance(q, str) or not q or not isinstance(semantic_text, str) or not semantic_text:
            raise ValueError(f"query {query_id} must define q and semantic_text")
        if semantic_text in texts:
            raise ValueError("semantic_text values must be unique in the controlled query set")
        texts.add(semantic_text)
        anchor = item.get("vector_anchor")
        if (
            not isinstance(anchor, list)
            or len(anchor) != 3
            or not all(type(value) in (int, float) and math.isfinite(value) for value in anchor)
            or not any(value != 0 for value in anchor)
        ):
            raise ValueError(f"query {query_id} has an invalid controlled vector anchor")
    return queries


def controlled_vector(
    anchor: list[float], dimensions: int = VECTOR_DIMENSIONS
) -> tuple[float, ...]:
    """Expand a public three-value fixture anchor to the production dimension."""
    if dimensions < len(anchor):
        raise ValueError("controlled vector dimensions are too small")
    return tuple(float(value) for value in anchor) + (0.0,) * (dimensions - len(anchor))


def performance_cases() -> list[dict[str, Any]]:
    return [
        {
            "id": f"{mode}-{len(fields)}f-{'relevance' if sort == 'relation' else 'date'}-k{top_k}",
            "mode": mode,
            "vector_fields": list(fields),
            "sort": sort,
            "top_k": top_k,
        }
        for mode in MODES
        for fields in FIELD_SETS
        for sort in SORTS
        for top_k in TOP_K_VALUES
    ]


def public_case(case: dict[str, Any]) -> dict[str, Any]:
    """Return the low-cardinality case facts safe to persist in a result file."""
    return {
        "id": case["id"],
        "mode": case["mode"],
        "field_set": "single" if len(case["vector_fields"]) == 1 else "multi",
        "vector_field_count": len(case["vector_fields"]),
        "sort_type": "relevance" if case["sort"] == "relation" else "date",
        "top_k": case["top_k"],
    }


def rotated_cases(round_index: int, query_index: int) -> list[dict[str, Any]]:
    cases = performance_cases()
    offset = (round_index + query_index) % len(cases)
    return cases[offset:] + cases[:offset]


def summarize(observations: list[dict[str, Any]]) -> dict[str, Any]:
    by_case: dict[str, dict[str, Any]] = {}
    for case in performance_cases():
        items = [
            item
            for item in observations
            if item["case_id"] == case["id"]
            and item.get("phase", "measured") == "measured"
        ]
        successes = [item for item in items if item["outcome"] == "success"]
        wall = [float(item["wall_ms"]) for item in successes]
        took = [float(item["opensearch_took_ms"]) for item in successes]
        by_case[case["id"]] = {
            **{key: value for key, value in public_case(case).items() if key != "id"},
            "samples": len(items),
            "successes": len(successes),
            "failures": len(items) - len(successes),
            "wall_ms": latency_summary(wall),
            "opensearch_took_ms": latency_summary(took),
        }
    return {
        "by_case": by_case,
        "all_successful": bool(observations)
        and all(item["outcome"] == "success" for item in observations),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the fixed Issue 75 matrix through POST /api/patent/search "
            "against a disposable loopback OpenSearch 3.3 cluster."
        )
    )
    parser.add_argument("--opensearch-url", required=True)
    parser.add_argument(
        "--query-set",
        type=Path,
        default=Path("benchmarks/semantic_search/query_set_v1.json"),
    )
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--documents", type=int, default=600)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--service-port", type=int, default=18075)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-controlled-writes", action="store_true")
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    parsed = urlsplit(args.opensearch_url)
    if not args.allow_controlled_writes:
        raise ValueError("--allow-controlled-writes is required")
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("benchmark writes require a loopback HTTP OpenSearch endpoint")
    if not 3 <= args.rounds <= 10:
        raise ValueError("rounds must be between 3 and 10")
    if not 100 <= args.documents <= 10000:
        raise ValueError("documents must be between 100 and 10000")
    if not 1 <= args.timeout <= 300:
        raise ValueError("timeout must be between 1 and 300 seconds")
    if not 1024 <= args.service_port <= 65535:
        raise ValueError("service port must be between 1024 and 65535")
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {args.output}")


def main() -> int:
    args = parse_args()
    validate_args(args)
    queries = load_query_set(args.query_set)
    commit = _clean_git_commit()
    index = f"issue75-performance-{uuid4().hex[:12]}"
    pipeline_ids: list[str] = []
    observations: list[dict[str, Any]] = []
    cleanup_verified = False
    with _loopback_client(args.opensearch_url, args.timeout) as opensearch:
        cluster = _cluster_preflight(opensearch)
        pipelines = json.loads((REPOSITORY_ROOT / PIPELINE_ASSET).read_text())["pipelines"]
        try:
            _install_pipelines(opensearch, pipelines, pipeline_ids)
            _create_index(opensearch, index)
            _bulk_index(opensearch, index, args.documents, queries)
            with controlled_service(
                opensearch_url=args.opensearch_url,
                index=index,
                query_set=args.query_set.resolve(),
                commit=commit,
                port=args.service_port,
                timeout=args.timeout,
            ) as service_url:
                with _loopback_client(service_url, args.timeout) as service:
                    for query in queries:
                        for case in performance_cases():
                            observations.append(
                                _observation(
                                    service,
                                    query=query,
                                    case=case,
                                    round_index=0,
                                    phase="warmup",
                                )
                            )
                    for round_index in range(args.rounds):
                        for query_index, query in enumerate(queries):
                            for case in rotated_cases(round_index, query_index):
                                observations.append(
                                    _observation(
                                        service,
                                        query=query,
                                        case=case,
                                        round_index=round_index,
                                        phase="measured",
                                    )
                                )
        finally:
            cleanup_verified = _cleanup_resources(
                opensearch,
                index,
                pipeline_ids,
            )

    _assert_git_source_unchanged(commit)

    output = {
        "schema_version": OUTPUT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "commit": commit,
        "source_dirty_before_run": False,
        "environment": {
            "transport": "loopback HTTP to the repository FastAPI application",
            "query_vector_provider": "deterministic local fixture adapter",
            "opensearch_version": cluster["version"],
            "opensearch_distribution": cluster["distribution"],
            "opensearch_build_hash": cluster["build_hash"],
            "cluster_name": EXPECTED_CLUSTER_NAME,
            "index_kind": "ephemeral controlled fixture",
            "vector_dimensions": VECTOR_DIMENSIONS,
            "vector_engine": "lucene-hnsw-cosinesimil",
            "documents": args.documents,
            "shards": 1,
            "replicas": 0,
            "client_concurrency": 1,
            "service_workers": 1,
            "measured_rounds": args.rounds,
            "recorded_warmups_per_query_case": 1,
            "request_deadline_seconds": max(1, min(int(args.timeout), 240)),
            "opensearch_timeout_seconds": max(1, min(int(args.timeout), 240)),
            "query_budget": {
                "max_request_body_bytes": DEFAULT_QUERY_BUDGET.max_request_body_bytes,
                "max_query_chars": DEFAULT_QUERY_BUDGET.max_query_chars,
                "max_nesting_depth": DEFAULT_QUERY_BUDGET.max_nesting_depth,
                "max_tokens": DEFAULT_QUERY_BUDGET.max_tokens,
                "max_ast_nodes": DEFAULT_QUERY_BUDGET.max_ast_nodes,
                "max_boolean_clauses": DEFAULT_QUERY_BUDGET.max_boolean_clauses,
                "max_page_size": DEFAULT_QUERY_BUDGET.max_page_size,
                "max_result_window": DEFAULT_QUERY_BUDGET.max_result_window,
            },
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "matrix": [public_case(case) for case in performance_cases()],
        "query_ids": [query["id"] for query in queries],
        "fingerprints": {
            "query_set_sha256": file_sha256(args.query_set),
            "source_files_sha256": {
                str(path): file_sha256(REPOSITORY_ROOT / path)
                for path in IMPLEMENTATION_FILES
            },
        },
        "summary": summarize(observations),
        "observations": observations,
        "cleanup_verified": cleanup_verified,
        "correctness_claim": False,
        "latency_threshold": None,
        "limitations": [
            "The fixture vector adapter removes external provider variance and is not an Ark latency measurement.",
            "The fixture uses Lucene HNSW rather than the production DiskANN/RaBitQ mapping.",
            "The serial controlled run is directional evidence, not a production capacity or SLA claim.",
            "Performance results do not replace the separate OpenSearch correctness integration test.",
        ],
    }
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output_file:
        json.dump(output, output_file, ensure_ascii=False, indent=2)
        output_file.write("\n")
    args.output.chmod(0o600)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "result_sha256": file_sha256(args.output),
                "all_successful": output["summary"]["all_successful"],
                "cleanup_verified": cleanup_verified,
            },
            sort_keys=True,
        )
    )
    return 0 if output["summary"]["all_successful"] and cleanup_verified else 1


def _cluster_preflight(client: httpx.Client) -> dict[str, str]:
    response = client.get("/")
    response.raise_for_status()
    cluster = response.json()
    if cluster.get("cluster_name") != EXPECTED_CLUSTER_NAME:
        raise RuntimeError(f"benchmark requires cluster_name={EXPECTED_CLUSTER_NAME}")
    version = cluster.get("version", {})
    version_number = str(version.get("number", ""))
    if not version_number.startswith("3.3."):
        raise RuntimeError("benchmark requires OpenSearch 3.3.x")
    return {
        "version": version_number,
        "distribution": str(version.get("distribution", "opensearch")),
        "build_hash": str(version.get("build_hash", "unknown")),
    }


def _install_pipelines(
    client: httpx.Client,
    pipelines: dict[str, Any],
    installed: list[str],
) -> None:
    # Finish the collision check before the first write.  The caller owns the
    # mutable cleanup list, and each ID enters it before PUT: the server may
    # create a pipeline even if the client loses the response.  DELETE 404 is
    # safe when the PUT did not reach OpenSearch.
    for pipeline_id, definition in pipelines.items():
        if client.get(f"/_search/pipeline/{pipeline_id}").status_code != 404:
            raise RuntimeError(f"controlled pipeline already exists: {pipeline_id}")
    for pipeline_id, definition in pipelines.items():
        installed.append(pipeline_id)
        response = client.put(f"/_search/pipeline/{pipeline_id}", json=definition)
        response.raise_for_status()


def _cleanup_resources(
    client: httpx.Client,
    index: str,
    pipeline_ids: list[str],
) -> bool:
    paths = [f"/{index}"] + [
        f"/_search/pipeline/{pipeline_id}" for pipeline_id in pipeline_ids
    ]
    for path in paths:
        try:
            client.delete(path)
        except httpx.RequestError:
            # Continue so one transport failure cannot prevent attempts on the
            # remaining resources.  The final readback decides success.
            pass
    try:
        return _resources_absent(client, index, pipeline_ids)
    except httpx.RequestError:
        return False


@contextmanager
def controlled_service(
    *,
    opensearch_url: str,
    index: str,
    query_set: Path,
    commit: str,
    port: int,
    timeout: float,
) -> Iterator[str]:
    parsed = urlsplit(opensearch_url)
    _ensure_service_port_available(port)
    environment = os.environ.copy()
    environment.update(
        {
            "ISSUE75_CONTROLLED_BENCHMARK": "1",
            "ISSUE75_QUERY_SET_PATH": str(query_set),
            "ENABLE_AUTH": "false",
            "ADMIN_ENABLED": "false",
            "ADMIN_CONFIG_DRAFTS_ENABLED": "false",
            "ADMIN_RUNTIME_CONFIG_ENABLED": "false",
            "OPENSEARCH_HOST": str(parsed.hostname),
            "OPENSEARCH_PORT": str(parsed.port or 80),
            "OPENSEARCH_USE_HTTPS": "false",
            "OPENSEARCH_VERIFY_CERTS": "false",
            "OPENSEARCH_USER": "",
            "OPENSEARCH_PASS": "",
            "OPENSEARCH_INDEX": index,
            "OPENSEARCH_TIMEOUT_SECONDS": str(max(1, min(int(timeout), 240))),
            "OPENSEARCH_POOL_MAXSIZE": "4",
            "OPENSEARCH_MAX_RETRIES": "0",
            "OPENSEARCH_RETRY_BACKOFF_SECONDS": "0",
            "PATENT_SEARCH_BULKHEAD_CAPACITY": "4",
            "PATENT_SEARCH_HEAVY_BULKHEAD_CAPACITY": "3",
            "PATENT_SEARCH_BULKHEAD_ACQUIRE_TIMEOUT_SECONDS": "0.01",
            "PATENT_SEARCH_DEADLINE_SECONDS": str(max(1, min(int(timeout), 240))),
            "QUERY_VECTOR_API_URL": "https://controlled.invalid/v1/embeddings",
            "QUERY_VECTOR_API_KEY": "",
            "QUERY_VECTOR_MODEL_ENDPOINT": "",
            "QUERY_VECTOR_MODEL_ENDPOINTS": "{}",
            "QUERY_MAX_REQUEST_BODY_BYTES": str(
                DEFAULT_QUERY_BUDGET.max_request_body_bytes
            ),
            "QUERY_MAX_CHARS": str(DEFAULT_QUERY_BUDGET.max_query_chars),
            "QUERY_MAX_NESTING_DEPTH": str(
                DEFAULT_QUERY_BUDGET.max_nesting_depth
            ),
            "QUERY_MAX_TOKENS": str(DEFAULT_QUERY_BUDGET.max_tokens),
            "QUERY_MAX_AST_NODES": str(DEFAULT_QUERY_BUDGET.max_ast_nodes),
            "QUERY_MAX_BOOLEAN_CLAUSES": str(
                DEFAULT_QUERY_BUDGET.max_boolean_clauses
            ),
            "QUERY_MAX_PAGE_SIZE": str(DEFAULT_QUERY_BUDGET.max_page_size),
            "QUERY_MAX_RESULT_WINDOW": str(
                DEFAULT_QUERY_BUDGET.max_result_window
            ),
            "WEB_CONCURRENCY": "1",
            "SERVICE_RELEASE_COMMIT": commit,
            "SERVICE_RELEASE_TAG": "issue75-benchmark",
            "SERVICE_INSTANCE_ID": "issue75-benchmark-local",
        }
    )
    service_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryFile(mode="w+") as log_file:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "benchmarks.semantic_search.controlled_app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
                "--workers",
                "1",
            ],
            cwd=REPOSITORY_ROOT,
            env=environment,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            _wait_for_service(service_url, process, timeout=min(timeout, 30))
            yield service_url
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _wait_for_service(url: str, process: subprocess.Popen, *, timeout: float) -> None:
    deadline = monotonic() + timeout
    with _loopback_client(url, 0.5) as client:
        while monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("controlled HTTP service exited during startup")
            try:
                response = client.get("/live")
                if response.status_code == 200:
                    sleep(0.05)
                    if process.poll() is not None:
                        raise RuntimeError(
                            "controlled HTTP service exited after the live probe"
                        )
                    return
            except httpx.RequestError:
                pass
            sleep(0.1)
    raise TimeoutError("controlled HTTP service did not become live")


def _loopback_client(base_url: str, timeout: float) -> httpx.Client:
    return httpx.Client(base_url=base_url, timeout=timeout, trust_env=False)


def _ensure_service_port_available(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(f"controlled service port {port} is already in use") from exc


def _request_payload(query: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "mode": case["mode"],
        "semantic_text": query["semantic_text"],
        "vector_fields": case["vector_fields"],
        "top_k": case["top_k"],
        "ds": "cn",
        "sort": case["sort"],
        "page": 1,
        "page_size": 20,
    }
    if case["mode"] == "hybrid":
        payload["q"] = query["q"]
    return payload


def _observation(
    client: httpx.Client,
    *,
    query: dict[str, Any],
    case: dict[str, Any],
    round_index: int,
    phase: str,
) -> dict[str, Any]:
    started = monotonic()
    status = None
    server_code = None
    try:
        response = client.post("/api/patent/search", json=_request_payload(query, case))
        status = response.status_code
        payload = response.json()
        if status != 200:
            server_code = payload.get("code") if isinstance(payload, dict) else None
            raise RuntimeError("controlled search returned a non-success response")
        _validate_success_payload(payload, case)
        wall_ms = round((monotonic() - started) * 1000, 3)
        return {
            "case_id": case["id"],
            "query_id": query["id"],
            "phase": phase,
            "round": round_index,
            "outcome": "success",
            "status": status,
            "server_code": None,
            "wall_ms": wall_ms,
            "opensearch_took_ms": float(payload["took_ms"]),
            "returned": len(payload["records"]),
            "total": payload["total"],
            "null_score_count": sum(
                record["score"] is None for record in payload["records"]
            ),
        }
    except Exception as exc:  # pragma: no cover - live dependency boundary
        return {
            "case_id": case["id"],
            "query_id": query["id"],
            "phase": phase,
            "round": round_index,
            "outcome": "failure",
            "status": status,
            "server_code": server_code,
            "wall_ms": round((monotonic() - started) * 1000, 3),
            "error_type": type(exc).__name__,
        }


def _validate_success_payload(payload: Any, case: dict[str, Any]) -> None:
    if not isinstance(payload, dict) or type(payload.get("took_ms")) is not int:
        raise ValueError("search response is missing an integer took_ms")
    if not isinstance(payload.get("records"), list) or type(payload.get("total")) is not int:
        raise ValueError("search response shape is invalid")
    context = payload.get("search_context")
    if not isinstance(context, dict):
        raise ValueError("semantic search response is missing search_context")
    if (
        context.get("mode") != case["mode"]
        or context.get("vector_fields") != case["vector_fields"]
        or context.get("top_k") != case["top_k"]
        or context.get("sort") != case["sort"]
    ):
        raise ValueError("search_context does not match the request")
    scores = [
        record.get("score")
        for record in payload["records"]
        if isinstance(record, dict)
    ]
    if case["sort"] == "!applicationDate" and any(score is not None for score in scores):
        raise ValueError("date-sorted semantic results must preserve null scores")


def _create_index(client: httpx.Client, index: str) -> None:
    vector = {
        "type": "knn_vector",
        "dimension": VECTOR_DIMENSIONS,
        "method": {"name": "hnsw", "engine": "lucene", "space_type": "cosinesimil"},
    }
    response = client.put(
        f"/{index}",
        json={
            "settings": {
                "index.knn": True,
                "number_of_shards": 1,
                "number_of_replicas": 0,
            },
            "mappings": {
                "properties": {
                    "patent_id": {"type": "keyword"},
                    "Title": {"type": "text"},
                    "TitleCN": {"type": "text"},
                    "PublicationCountry": {"type": "keyword"},
                    "ApplicationDate": {"type": "date"},
                    "PublicationDate": {"type": "date"},
                    "AbstractVector1024": vector,
                    "MainClaimVector1024": vector,
                }
            },
        },
    )
    response.raise_for_status()


def _bulk_index(
    client: httpx.Client,
    index: str,
    count: int,
    queries: list[dict[str, Any]],
) -> None:
    anchors = [controlled_vector(query["vector_anchor"]) for query in queries]
    lines: list[str] = []
    for item in range(count):
        category = item % len(queries)
        abstract = list(anchors[category])
        main_claim = list(anchors[(category + 1) % len(queries)])
        noise_position = 3 + item % 127
        abstract[noise_position] = (item % 17 + 1) / 1000
        main_claim[noise_position] = (item % 13 + 1) / 1000
        title_term = queries[category]["q"].split(":", 1)[-1]
        document = {
            "patent_id": f"fixture-{item:05d}",
            "Title": f"{title_term} 装置 {item}",
            "TitleCN": f"{title_term} 装置 {item}",
            "PublicationCountry": "US" if item % 10 == 0 else "CN",
            "ApplicationDate": (
                f"{2010 + item % 15:04d}-{1 + item % 12:02d}-{1 + item % 28:02d}"
            ),
            "PublicationDate": (
                f"{2011 + item % 14:04d}-{1 + item % 12:02d}-{1 + item % 28:02d}"
            ),
            "AbstractVector1024": abstract,
            "MainClaimVector1024": main_claim,
        }
        lines.extend(
            [
                json.dumps({"index": {"_index": index, "_id": document["patent_id"]}}),
                json.dumps(document, ensure_ascii=False, separators=(",", ":")),
            ]
        )
    response = client.post(
        "/_bulk",
        params={"refresh": "true"},
        content="\n".join(lines) + "\n",
        headers={"Content-Type": "application/x-ndjson"},
    )
    response.raise_for_status()
    if response.json().get("errors") is not False:
        raise RuntimeError("controlled bulk indexing failed")


def _resources_absent(client: httpx.Client, index: str, pipeline_ids: list[str]) -> bool:
    return client.head(f"/{index}").status_code == 404 and all(
        client.get(f"/_search/pipeline/{pipeline_id}").status_code == 404
        for pipeline_id in pipeline_ids
    )


def _git_commit() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _git_dirty() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _clean_git_commit() -> str:
    commit = _git_commit()
    if _git_dirty():
        raise RuntimeError("benchmark requires a clean Git worktree")
    return commit


def _assert_git_source_unchanged(expected_commit: str) -> None:
    if _git_commit() != expected_commit or _git_dirty():
        raise RuntimeError("Git source changed during the benchmark run")


if __name__ == "__main__":
    raise SystemExit(main())
