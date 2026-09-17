"""Validate the fixed Issue 75 performance matrix without live dependencies."""

from pathlib import Path
import socket

import httpx
import pytest

from benchmarks.semantic_search.semantic_search_performance import (
    _assert_git_source_unchanged,
    _cleanup_resources,
    _clean_git_commit,
    _ensure_service_port_available,
    _install_pipelines,
    _loopback_client,
    controlled_vector,
    load_query_set,
    performance_cases,
    public_case,
    rotated_cases,
    summarize,
)


QUERY_SET = Path("benchmarks/semantic_search/query_set_v1.json")


class _Response:
    def __init__(self, status_code):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _PipelineClient:
    def __init__(self, *, existing=(), failed_put=None):
        self.existing = set(existing)
        self.failed_put = failed_put
        self.gets = []
        self.puts = []

    def get(self, path):
        self.gets.append(path)
        pipeline_id = path.rsplit("/", 1)[-1]
        return _Response(200 if pipeline_id in self.existing else 404)

    def put(self, path, *, json):
        self.puts.append((path, json))
        pipeline_id = path.rsplit("/", 1)[-1]
        return _Response(500 if pipeline_id == self.failed_put else 200)


def test_query_set_and_matrix_cover_every_required_dimension():
    queries = load_query_set(QUERY_SET)
    cases = performance_cases()

    assert len(queries) == 3
    assert len(cases) == 16
    assert {case["mode"] for case in cases} == {"vector", "hybrid"}
    assert {len(case["vector_fields"]) for case in cases} == {1, 2}
    assert {case["sort"] for case in cases} == {"relation", "!applicationDate"}
    assert {case["top_k"] for case in cases} == {20, 100}
    assert len({case["id"] for case in cases}) == len(cases)
    assert all(len(controlled_vector(query["vector_anchor"])) == 1024 for query in queries)


def test_persisted_case_does_not_include_exact_vector_field_combinations():
    persisted = [public_case(case) for case in performance_cases()]

    assert {case["field_set"] for case in persisted} == {"single", "multi"}
    assert all("vector_fields" not in case and "sort" not in case for case in persisted)


def test_rotated_matrix_preserves_cases_and_changes_first_position():
    original = performance_cases()
    rotated = rotated_cases(1, 1)

    assert {case["id"] for case in rotated} == {case["id"] for case in original}
    assert rotated[0]["id"] != original[0]["id"]


def test_summary_reports_samples_percentiles_and_failures_without_thresholds():
    observations = []
    for case in performance_cases():
        for sample in (1.0, 2.0, 3.0):
            observations.append(
                {
                    "case_id": case["id"],
                    "outcome": "success",
                    "wall_ms": sample,
                    "opensearch_took_ms": sample / 2,
                }
            )
    observations[-1] = {"case_id": performance_cases()[-1]["id"], "outcome": "failure"}

    result = summarize(observations)

    assert result["all_successful"] is False
    first = result["by_case"][performance_cases()[0]["id"]]
    assert first["samples"] == 3
    assert first["wall_ms"] == {
        "min": 1.0,
        "p50": 2.0,
        "p95": 3.0,
        "p99": 3.0,
        "max": 3.0,
    }
    last = result["by_case"][performance_cases()[-1]["id"]]
    assert last["failures"] == 1


def test_summary_excludes_recorded_warmups_but_requires_them_to_succeed():
    case = performance_cases()[0]
    observations = [
        {
            "case_id": case["id"],
            "phase": "warmup",
            "outcome": "failure",
            "wall_ms": 100.0,
        },
        {
            "case_id": case["id"],
            "phase": "measured",
            "outcome": "success",
            "wall_ms": 1.0,
            "opensearch_took_ms": 0.5,
        },
    ]

    result = summarize(observations)

    assert result["all_successful"] is False
    assert result["by_case"][case["id"]]["samples"] == 1
    assert result["by_case"][case["id"]]["wall_ms"]["max"] == 1.0


def test_pipeline_collision_preflight_completes_before_any_write():
    client = _PipelineClient(existing={"second"})
    installed = []

    with pytest.raises(RuntimeError, match="already exists: second"):
        _install_pipelines(
            client,
            {"first": {"description": "one"}, "second": {"description": "two"}},
            installed,
        )

    assert client.gets == [
        "/_search/pipeline/first",
        "/_search/pipeline/second",
    ]
    assert client.puts == []
    assert installed == []


def test_pipeline_install_records_partial_success_for_finally_cleanup():
    client = _PipelineClient(failed_put="second")
    installed = []

    with pytest.raises(RuntimeError, match="HTTP 500"):
        _install_pipelines(
            client,
            {"first": {"description": "one"}, "second": {"description": "two"}},
            installed,
        )

    assert [path for path, _definition in client.puts] == [
        "/_search/pipeline/first",
        "/_search/pipeline/second",
    ]
    assert installed == ["first", "second"]


def test_pipeline_is_cleanup_candidate_when_put_response_is_lost():
    class LostResponseClient(_PipelineClient):
        def put(self, path, *, json):
            self.puts.append((path, json))
            raise httpx.ReadError("response lost after server write")

    client = LostResponseClient()
    cleanup_candidates = []

    with pytest.raises(httpx.ReadError):
        _install_pipelines(
            client,
            {"first": {"description": "one"}},
            cleanup_candidates,
        )

    assert cleanup_candidates == ["first"]


def test_cleanup_attempts_every_resource_after_a_transport_failure():
    class CleanupClient:
        def __init__(self):
            self.deletes = []

        def delete(self, path):
            self.deletes.append(path)
            if path == "/controlled-index":
                raise httpx.ConnectError("controlled disconnect")
            return _Response(200)

        def head(self, path):
            return _Response(200)

        def get(self, path):
            return _Response(404)

    client = CleanupClient()

    assert _cleanup_resources(client, "controlled-index", ["first", "second"]) is False
    assert client.deletes == [
        "/controlled-index",
        "/_search/pipeline/first",
        "/_search/pipeline/second",
    ]


def test_benchmark_refuses_dirty_or_changing_git_source(monkeypatch):
    monkeypatch.setattr(
        "benchmarks.semantic_search.semantic_search_performance._git_commit",
        lambda: "a" * 40,
    )
    monkeypatch.setattr(
        "benchmarks.semantic_search.semantic_search_performance._git_dirty",
        lambda: True,
    )
    with pytest.raises(RuntimeError, match="clean Git worktree"):
        _clean_git_commit()

    monkeypatch.setattr(
        "benchmarks.semantic_search.semantic_search_performance._git_dirty",
        lambda: False,
    )
    assert _clean_git_commit() == "a" * 40
    monkeypatch.setattr(
        "benchmarks.semantic_search.semantic_search_performance._git_commit",
        lambda: "b" * 40,
    )
    with pytest.raises(RuntimeError, match="changed during"):
        _assert_git_source_unchanged("a" * 40)


def test_loopback_client_ignores_proxy_environment(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")

    with _loopback_client("http://127.0.0.1:9", 0.1) as client:
        assert client.trust_env is False


def test_controlled_service_port_must_be_unused():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        with pytest.raises(RuntimeError, match="already in use"):
            _ensure_service_port_available(port)

    _ensure_service_port_available(port)
