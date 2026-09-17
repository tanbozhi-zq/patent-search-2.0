"""Benchmark-only app wiring for the controlled Issue 75 HTTP run."""

from __future__ import annotations

import os
from pathlib import Path
from time import monotonic

from app.api.dependencies import get_query_vector_adapter
from app.integrations.query_vector import QueryVectorConfig, QueryVectorResult
from app.main import app, settings
from benchmarks.semantic_search.semantic_search_performance import controlled_vector, load_query_set


if os.environ.get("ISSUE75_CONTROLLED_BENCHMARK") != "1":
    raise RuntimeError("controlled benchmark app requires ISSUE75_CONTROLLED_BENCHMARK=1")
if settings.opensearch_host not in {"127.0.0.1", "localhost", "::1"}:
    raise RuntimeError("controlled benchmark app requires loopback OpenSearch")
if not settings.opensearch_index.startswith("issue75-performance-"):
    raise RuntimeError("controlled benchmark app requires an ephemeral Issue 75 index")

query_set_path = Path(os.environ["ISSUE75_QUERY_SET_PATH"])
_anchors = {
    query["semantic_text"]: query["vector_anchor"]
    for query in load_query_set(query_set_path)
}


class ControlledQueryVectorAdapter:
    """Return a deterministic fixture vector without external provider I/O."""

    def generate(
        self,
        semantic_text: str,
        *,
        config: QueryVectorConfig,
        deadline: float,
    ) -> QueryVectorResult:
        if deadline <= monotonic():
            raise TimeoutError("controlled query-vector deadline expired")
        anchor = _anchors.get(semantic_text)
        if anchor is None:
            raise ValueError("semantic text is not in the controlled query set")
        return QueryVectorResult(
            model=config.model,
            vector=controlled_vector(anchor, config.dimensions),
        )


_adapter = ControlledQueryVectorAdapter()
app.dependency_overrides[get_query_vector_adapter] = lambda: _adapter
