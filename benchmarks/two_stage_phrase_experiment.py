from __future__ import annotations

import argparse
import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

from dotenv import load_dotenv
from opensearchpy import OpenSearch

from app.mappings.query_field_mapping import TEXT_FIELD_MAPPING
from app.query.ast import AndNode, FieldQuery, NotNode, OrNode, PhraseNode, QueryNode
from app.query.dsl_builder import build_search_dsl
from app.query.parser import parse_query
from app.schemas.search import SearchRequest


DEFAULT_QUERY_INDEXES = (1, 20, 0)
DEFAULT_WINDOWS = (100, 300, 1000)
MAX_QUERIES = 6
MAX_WINDOW = 1000
RELEVANCE_SORTS = {"relation", "rank", "relevance", "score"}
TSCD_FIELDS = tuple(TEXT_FIELD_MAPPING["tscd"])


def validate_experiment_query(query: str) -> int:
    """Return the positive tscd phrase count or reject an unsafe PoC query."""
    phrase_count = _validate_node(parse_query(query), field=None)
    if phrase_count == 0:
        raise ValueError("experiment query must contain at least one quoted tscd phrase")
    return phrase_count


def _validate_node(node: QueryNode, *, field: str | None) -> int:
    if isinstance(node, NotNode):
        raise ValueError("experiment does not support NOT because it reverses candidate-set inclusion")
    if isinstance(node, PhraseNode):
        if field != "tscd":
            raise ValueError("experiment only rewrites explicitly quoted tscd phrases")
        return 1
    if isinstance(node, FieldQuery):
        return _validate_node(node.value, field=node.field)
    if isinstance(node, (AndNode, OrNode)):
        return _validate_node(node.left, field=field) + _validate_node(node.right, field=field)
    return 0


def build_candidate_body(exact_body: dict[str, Any], *, window: int) -> tuple[dict[str, Any], int]:
    if window < 1 or window > MAX_WINDOW:
        raise ValueError(f"candidate window must be between 1 and {MAX_WINDOW}")

    candidate = copy.deepcopy(exact_body)
    rewritten = _rewrite_positive_phrases(candidate, negative=False)
    if rewritten == 0:
        raise ValueError("exact query contains no phrase multi_match clauses")

    candidate["from"] = 0
    candidate["size"] = window
    candidate["_source"] = False
    candidate["track_total_hits"] = False
    candidate["sort"] = ["_score"]
    return candidate, rewritten


def _rewrite_positive_phrases(value: Any, *, negative: bool) -> int:
    if isinstance(value, list):
        return sum(_rewrite_positive_phrases(item, negative=negative) for item in value)
    if not isinstance(value, dict):
        return 0

    multi_match = value.get("multi_match")
    if isinstance(multi_match, dict) and multi_match.get("type") == "phrase":
        if negative:
            raise ValueError("experiment does not rewrite phrase clauses under must_not")
        if tuple(multi_match.get("fields", ())) != TSCD_FIELDS:
            raise ValueError("experiment only rewrites the current tscd phrase field set")
        multi_match["type"] = "best_fields"
        multi_match["operator"] = "and"
        multi_match["fuzziness"] = 0
        multi_match.pop("slop", None)
        return 1

    rewritten = 0
    for key, child in value.items():
        rewritten += _rewrite_positive_phrases(
            child,
            negative=negative or key == "must_not",
        )
    return rewritten


def build_verification_body(
    exact_body: dict[str, Any],
    *,
    candidate_ids: list[str],
    page_size: int,
    source: bool | list[str] = False,
) -> dict[str, Any]:
    if not candidate_ids:
        raise ValueError("candidate_ids must not be empty")
    if page_size < 1:
        raise ValueError("page_size must be positive")

    return {
        "from": 0,
        "size": page_size,
        "_source": copy.deepcopy(source),
        "track_total_hits": True,
        "query": {
            "bool": {
                "must": [copy.deepcopy(exact_body["query"])],
                "filter": [{"ids": {"values": list(candidate_ids)}}],
            }
        },
        "sort": copy.deepcopy(exact_body.get("sort", ["_score"])),
    }


def phrase_fields(body: dict[str, Any]) -> set[str]:
    fields: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return
        multi_match = value.get("multi_match")
        if isinstance(multi_match, dict) and multi_match.get("type") == "phrase":
            fields.update(str(field).split("^", 1)[0] for field in multi_match.get("fields", []))
        for child in value.values():
            visit(child)

    visit(body)
    return fields


def validate_mapping_analyzers(
    mapping_response: dict[str, Any],
    *,
    fields: set[str],
) -> dict[str, dict[str, str]]:
    if not mapping_response:
        raise ValueError("OpenSearch mapping response is empty")

    summary: dict[str, dict[str, str]] = {}
    for index_name, index_body in mapping_response.items():
        properties = index_body.get("mappings", {}).get("properties", {})
        for field in sorted(fields):
            field_mapping = properties.get(field)
            if not isinstance(field_mapping, dict):
                raise ValueError(f"mapping is missing phrase field {field} in {index_name}")
            analyzer = str(field_mapping.get("analyzer") or "standard")
            search_analyzer = str(field_mapping.get("search_analyzer") or analyzer)
            quote_analyzer = str(
                field_mapping.get("search_quote_analyzer")
                or field_mapping.get("search_analyzer")
                or analyzer
            )
            if search_analyzer != quote_analyzer:
                raise ValueError(
                    f"unsafe analyzer mismatch for {index_name}.{field}: "
                    f"search={search_analyzer}, quote={quote_analyzer}"
                )
            summary[f"{index_name}.{field}"] = {
                "search_analyzer": search_analyzer,
                "search_quote_analyzer": quote_analyzer,
            }
    return summary


def _hit_ids(response: dict[str, Any]) -> list[str]:
    hits = response.get("hits", {}).get("hits", [])
    if not isinstance(hits, list):
        raise ValueError("OpenSearch response has invalid hits")
    return [str(hit["_id"]) for hit in hits]


def _total(response: dict[str, Any]) -> tuple[int | None, str | None]:
    total = response.get("hits", {}).get("total")
    if isinstance(total, int):
        return total, "eq"
    if isinstance(total, dict):
        value = total.get("value")
        relation = total.get("relation")
        return (int(value) if isinstance(value, int) else None), (str(relation) if relation else None)
    return None, None


def timed_search(
    client: OpenSearch,
    *,
    index: str,
    body: dict[str, Any],
    timeout_seconds: float,
) -> tuple[dict[str, Any], dict[str, float | int]]:
    started = monotonic()
    response = client.search(
        index=index,
        body=body,
        request_timeout=timeout_seconds,
        params={
            "request_cache": "false",
            "allow_partial_search_results": "false",
            "cancel_after_time_interval": f"{int(timeout_seconds * 1000)}ms",
        },
    )
    wall_ms = (monotonic() - started) * 1000.0
    if not isinstance(response, dict) or not isinstance(response.get("hits"), dict):
        raise ValueError("OpenSearch returned an invalid search response")
    if response.get("timed_out"):
        raise RuntimeError("OpenSearch search timed out")
    if int(response.get("_shards", {}).get("failed", 0)):
        raise RuntimeError("OpenSearch search returned failed shards")
    return response, {
        "wall_ms": round(wall_ms, 3),
        "opensearch_took_ms": int(response.get("took", 0)),
    }


def run_query_experiment(
    client: OpenSearch,
    *,
    index: str,
    spec: dict[str, Any],
    windows: list[int],
    page_size: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    query = str(spec["query"])
    sort = str(spec.get("sort") or "relation")
    if sort not in RELEVANCE_SORTS:
        raise ValueError("experiment only supports relevance sorting")
    phrase_count = validate_experiment_query(query)

    request = SearchRequest(
        q=query,
        ds=str(spec.get("ds") or "all"),
        sort=sort,
        page=1,
        page_size=page_size,
    )
    exact_body = build_search_dsl(request)

    service_shape_response, service_shape_timing = timed_search(
        client,
        index=index,
        body=exact_body,
        timeout_seconds=timeout_seconds,
    )
    service_shape_ids = _hit_ids(service_shape_response)
    service_total, service_relation = _total(service_shape_response)

    exact_topk_body = copy.deepcopy(exact_body)
    exact_topk_body["from"] = 0
    exact_topk_body["size"] = min(page_size + 1, 100)
    exact_topk_body["_source"] = False
    exact_topk_body["track_total_hits"] = False
    exact_topk_response, exact_topk_timing = timed_search(
        client,
        index=index,
        body=exact_topk_body,
        timeout_seconds=timeout_seconds,
    )
    exact_topk_ids_with_boundary = _hit_ids(exact_topk_response)
    baseline_ids = exact_topk_ids_with_boundary[:page_size]
    exact_topk_hits = exact_topk_response.get("hits", {}).get("hits", [])
    boundary_tie = False
    if len(exact_topk_hits) > page_size:
        boundary_tie = exact_topk_hits[page_size - 1].get("_score") == exact_topk_hits[
            page_size
        ].get("_score")
    window_results = []

    for window in windows:
        candidate_body, rewritten = build_candidate_body(exact_body, window=window)
        if rewritten != phrase_count:
            raise ValueError(
                f"AST phrase count {phrase_count} differs from rewritten DSL count {rewritten}"
            )
        candidate_response, candidate_timing = timed_search(
            client,
            index=index,
            body=candidate_body,
            timeout_seconds=timeout_seconds,
        )
        candidate_ids = _hit_ids(candidate_response)

        if candidate_ids:
            verification_body = build_verification_body(
                exact_body,
                candidate_ids=candidate_ids,
                page_size=min(page_size + 1, 100),
            )
            verified_response, verification_timing = timed_search(
                client,
                index=index,
                body=verification_body,
                timeout_seconds=timeout_seconds,
            )
            verified_ids = _hit_ids(verified_response)
            verified_in_window, verified_total_relation = _total(verified_response)
        else:
            verification_timing = {"wall_ms": 0.0, "opensearch_took_ms": 0}
            verified_ids = []
            verified_in_window, verified_total_relation = 0, "eq"

        verified_ids = verified_ids[:page_size]

        baseline_set = set(baseline_ids)
        candidate_set = set(candidate_ids)
        verified_set = set(verified_ids)
        denominator = len(baseline_ids)
        candidate_coverage = (
            len(baseline_set & candidate_set) / denominator if denominator else None
        )
        verified_coverage = (
            len(baseline_set & verified_set) / denominator if denominator else None
        )
        total_wall_ms = float(candidate_timing["wall_ms"]) + float(
            verification_timing["wall_ms"]
        )
        exact_topk_wall_ms = float(exact_topk_timing["wall_ms"])
        exact_to_two_stage_ratio = (
            exact_topk_wall_ms / total_wall_ms if total_wall_ms > 0 else None
        )

        window_results.append(
            {
                "window": window,
                "candidate_count": len(candidate_ids),
                "verified_count": len(verified_ids),
                "verified_in_window": verified_in_window,
                "verified_total_relation": verified_total_relation,
                "candidate_timing": candidate_timing,
                "verification_timing": verification_timing,
                "two_stage_wall_ms": round(total_wall_ms, 3),
                "single_observation_exact_to_two_stage_ratio": (
                    round(exact_to_two_stage_ratio, 3)
                    if exact_to_two_stage_ratio is not None
                    else None
                ),
                "baseline_top_coverage_in_candidates": (
                    round(candidate_coverage, 6) if candidate_coverage is not None else None
                ),
                "baseline_top_coverage_after_verification": (
                    round(verified_coverage, 6) if verified_coverage is not None else None
                ),
                "baseline_top_missing_from_candidates": [
                    patent_id for patent_id in baseline_ids if patent_id not in candidate_set
                ],
                "baseline_top_missing_after_verification": [
                    patent_id for patent_id in baseline_ids if patent_id not in verified_set
                ],
                "verified_top_ids": verified_ids,
                "is_exact_baseline_prefix": verified_ids == baseline_ids[: len(verified_ids)],
            }
        )

    return {
        "query": query,
        "channel": spec.get("channel"),
        "element_id": spec.get("element_id"),
        "phrase_count": phrase_count,
        "phrase_field_paths": phrase_count * len(TSCD_FIELDS),
        "baseline_empty": not baseline_ids,
        "current_service_shape": {
            "timing": service_shape_timing,
            "total": service_total,
            "total_relation": service_relation,
            "returned": len(service_shape_ids),
            "top_ids": service_shape_ids,
        },
        "single_exact_topk_observation": {
            "timing": exact_topk_timing,
            "returned": len(baseline_ids),
            "top_ids": baseline_ids,
            "top_ids_match_service_shape": baseline_ids == service_shape_ids,
            "score_tie_at_topk_boundary": boundary_tie,
        },
        "windows": window_results,
    }


def build_client_from_env(*, timeout_seconds: float) -> tuple[OpenSearch, str]:
    host = os.environ.get("OPENSEARCH_HOST", "").strip()
    port = int(os.environ.get("OPENSEARCH_PORT", "9200"))
    username = os.environ.get("OPENSEARCH_USER", "")
    password = os.environ.get("OPENSEARCH_PASS", "")
    index = os.environ.get("OPENSEARCH_INDEX", "patent_search_read").strip()
    if not host or not index:
        raise ValueError("OPENSEARCH_HOST and OPENSEARCH_INDEX are required")
    if bool(username) != bool(password):
        raise ValueError("OPENSEARCH_USER and OPENSEARCH_PASS must be provided together")

    use_https = os.environ.get("OPENSEARCH_USE_HTTPS", "true").lower() not in {
        "0",
        "false",
        "no",
    }
    verify_certs = os.environ.get("OPENSEARCH_VERIFY_CERTS", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    auth = (username, password) if username else None
    client = OpenSearch(
        hosts=[{"host": host, "port": port}],
        http_auth=auth,
        use_ssl=use_https,
        verify_certs=verify_certs,
        ssl_show_warn=verify_certs,
        timeout=timeout_seconds,
        max_retries=0,
        retry_on_status=(),
        retry_on_timeout=False,
    )
    return client, index


def load_plan(path: Path, query_indexes: list[int]) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    queries = payload.get("queries")
    if not isinstance(queries, list):
        raise ValueError("query plan must contain a queries array")
    if len(query_indexes) > MAX_QUERIES:
        raise ValueError(f"at most {MAX_QUERIES} queries may run in one pilot")
    selected = []
    for query_index in query_indexes:
        if query_index < 0 or query_index >= len(queries):
            raise ValueError(f"query index {query_index} is outside the plan")
        spec = queries[query_index]
        if not isinstance(spec, dict) or not spec.get("query"):
            raise ValueError(f"query index {query_index} is invalid")
        selected.append({"query_index": query_index, **spec})
    return selected


def preflight(client: OpenSearch, *, index: str, fields: set[str]) -> dict[str, Any]:
    health = client.cluster.health(index=index)
    if not isinstance(health, dict) or health.get("status") == "red":
        raise RuntimeError("OpenSearch index health is red or invalid")
    mappings = client.indices.get_mapping(index=index)
    if len(mappings) != 1:
        raise RuntimeError("read target must resolve to exactly one physical index")
    analyzer_summary = validate_mapping_analyzers(mappings, fields=fields)
    merge_stats = client.indices.stats(index=index, metric="merge")
    current_merges = 0
    for index_stats in merge_stats.get("indices", {}).values():
        current_merges += int(
            index_stats.get("primaries", {}).get("merges", {}).get("current", 0)
        )
    relocating_shards = int(health.get("relocating_shards", 0))
    initializing_shards = int(health.get("initializing_shards", 0))
    return {
        "health_status": health.get("status"),
        "relocating_shards": relocating_shards,
        "initializing_shards": initializing_shards,
        "active_primary_shards": int(health.get("active_primary_shards", 0)),
        "current_primary_merges": current_merges,
        "physical_indices": sorted(mappings),
        "analyzers": analyzer_summary,
        "timing_contaminated": bool(
            relocating_shards or initializing_shards or current_merges
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a bounded, read-only two-stage phrase-search pilot.",
    )
    parser.add_argument(
        "--plan",
        type=Path,
        default=Path("benchmarks/capacity/query_plan.json"),
    )
    parser.add_argument(
        "--query-index",
        type=int,
        action="append",
        dest="query_indexes",
        help="Zero-based query index in the plan; may be repeated.",
    )
    parser.add_argument("--window", type=int, action="append", dest="windows")
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.timeout <= 0 or args.timeout > 300:
        raise ValueError("timeout must be between 0 and 300 seconds")
    if args.page_size < 1 or args.page_size > 100:
        raise ValueError("page-size must be between 1 and 100")

    query_indexes = list(args.query_indexes or DEFAULT_QUERY_INDEXES)
    windows = sorted(set(args.windows or DEFAULT_WINDOWS))
    if not windows or any(window < 1 or window > MAX_WINDOW for window in windows):
        raise ValueError(f"each window must be between 1 and {MAX_WINDOW}")
    selected = load_plan(args.plan, query_indexes)
    for spec in selected:
        if str(spec.get("sort") or "relation") not in RELEVANCE_SORTS:
            raise ValueError("selected query must use relevance sorting")
        validate_experiment_query(str(spec["query"]))

    load_dotenv(args.env_file, override=False)
    client, index = build_client_from_env(timeout_seconds=args.timeout)
    try:
        exact_bodies = [
            build_search_dsl(
                SearchRequest(
                    q=str(spec["query"]),
                    ds=str(spec.get("ds") or "all"),
                    sort=str(spec.get("sort") or "relation"),
                    page=1,
                    page_size=args.page_size,
                )
            )
            for spec in selected
        ]
        fields = set().union(*(phrase_fields(body) for body in exact_bodies))
        preflight_result = preflight(client, index=index, fields=fields)
        physical_index = preflight_result["physical_indices"][0]
        results = []
        for spec in selected:
            results.append(
                {
                    "query_index": spec["query_index"],
                    **run_query_experiment(
                        client,
                        index=physical_index,
                        spec=spec,
                        windows=windows,
                        page_size=args.page_size,
                        timeout_seconds=args.timeout,
                    ),
                }
            )
    finally:
        client.close()

    output = {
        "schema_version": "2",
        "kind": "single_serial_read_only_pilot",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "plan": str(args.plan),
        "index_alias": index,
        "query_indexes": query_indexes,
        "windows": windows,
        "page_size": args.page_size,
        "preflight": preflight_result,
        "results": results,
        "limitations": [
            "Top-N candidate coverage is empirical, not a mathematical guarantee.",
            "This pilot compares against current Top-K behavior, not human relevance judgments.",
            "Approximate fast mode does not provide an exhaustive total or deep-pagination equivalence.",
            "Single serial timings are directional evidence, not an SLA benchmark.",
            "An empty exact baseline has null coverage and cannot validate fidelity.",
            (
                "The service-shape query runs first, so its timing is order-sensitive "
                "and is not a fair algorithmic speedup baseline."
            ),
        ],
    }
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.output.exists():
        raise FileExistsError(f"refusing to overwrite existing output: {args.output}")
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.output.chmod(0o600)

    compact = {
        "output": str(args.output.resolve()),
        "physical_indices": preflight_result["physical_indices"],
        "health": preflight_result["health_status"],
        "relocating_shards": preflight_result["relocating_shards"],
        "current_primary_merges": preflight_result["current_primary_merges"],
        "queries": [
            {
                "query_index": result["query_index"],
                "service_shape_wall_ms": result["current_service_shape"]["timing"]["wall_ms"],
                "single_exact_topk_wall_ms": result["single_exact_topk_observation"]["timing"][
                    "wall_ms"
                ],
                "windows": [
                    {
                        "window": window["window"],
                        "two_stage_wall_ms": window["two_stage_wall_ms"],
                        "single_observation_exact_to_two_stage_ratio": window[
                            "single_observation_exact_to_two_stage_ratio"
                        ],
                        "coverage": window["baseline_top_coverage_after_verification"],
                    }
                    for window in result["windows"]
                ],
            }
            for result in results
        ],
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
