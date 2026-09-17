"""验证 OpenSearch 仓储的并发上限、重试、总截止时间与异常翻译边界。"""

import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest
from opensearchpy.exceptions import (
    AuthenticationException,
    ConnectionError as OpenSearchConnectionError,
    ConnectionTimeout,
    ImproperlyConfigured,
    NotFoundError,
    RequestError,
    SerializationError,
    TransportError,
)
from urllib3.util import Timeout

from app.core.config import Settings
from app.core.deadline import request_deadline
from app.core.exceptions import (
    OpenSearchQueryError,
    PatentNotFoundError,
    SearchDependencyTimeoutError,
    SearchDependencyUnavailableError,
)
from app.core.request_context import bind_request_id, reset_request_id
from app.repositories.deadline_connection import DeadlineUrllib3HttpConnection
from app.repositories.opensearch_repo import OpenSearchRepository
from app.services.detail_service import DetailService


EMPTY_SEARCH_RESPONSE = {"hits": {"total": {"value": 0}, "hits": []}}


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.advance(seconds)


class ScriptedClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.close_calls = 0

    def search(self, index, body, params=None):
        call = {"operation": "search", "index": index, "body": body, "params": params}
        self.calls.append(call)
        return self._resolve(call)

    def count(self, index, body, params=None):
        call = {"operation": "count", "index": index, "body": body, "params": params}
        self.calls.append(call)
        return self._resolve(call)

    def close(self):
        self.close_calls += 1

    def _resolve(self, call):
        outcome = self.outcomes.pop(0)
        if callable(outcome):
            return outcome(call)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class BlockingClient:
    def __init__(self, expected_active):
        self.expected_active = expected_active
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.pool_full = Event()
        self.release = Event()
        self.lock = Lock()

    def search(self, index, body, params=None):
        with self.lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active == self.expected_active:
                self.pool_full.set()
        try:
            self.release.wait(timeout=2)
            return EMPTY_SEARCH_RESPONSE
        finally:
            with self.lock:
                self.active -= 1


def _settings(**overrides):
    values = {
        "_env_file": None,
        "opensearch_max_retries": 0,
        "patent_search_deadline_seconds": 10,
    }
    values.update(overrides)
    return Settings(
        **values,
    )


def _timeout_total(call):
    request_timeout = call["params"]["request_timeout"]
    assert isinstance(request_timeout, Timeout)
    return request_timeout.total


def test_repository_accepts_injected_client_and_closes_it():
    client = ScriptedClient([EMPTY_SEARCH_RESPONSE])
    repository = OpenSearchRepository(settings=_settings(), client=client)

    assert repository.client is client
    assert repository.search({"query": {"match_all": {}}}) == EMPTY_SEARCH_RESPONSE

    repository.close()
    assert client.close_calls == 1


def test_native_client_has_bounded_pool_and_no_hidden_transport_retries():
    repository = OpenSearchRepository(settings=_settings(opensearch_pool_maxsize=7))
    try:
        transport = repository.client.transport
        connection = transport.connection_pool.connections[0]

        assert transport.max_retries == 0
        assert transport.retry_on_status == ()
        assert transport.retry_on_timeout is False
        assert isinstance(connection, DeadlineUrllib3HttpConnection)
        assert connection.pool.pool.maxsize == 7
    finally:
        repository.close()


def test_repository_never_exceeds_configured_concurrent_client_calls():
    client = BlockingClient(expected_active=2)
    repository = OpenSearchRepository(
        settings=_settings(
            opensearch_pool_maxsize=2,
            patent_search_bulkhead_capacity=2,
            patent_search_heavy_bulkhead_capacity=1,
        ),
        client=client,
    )

    with ThreadPoolExecutor(max_workers=5) as executor:
        active_calls = [
            executor.submit(repository.search, {"query": {"match_all": {}}})
            for _ in range(2)
        ]
        try:
            assert client.pool_full.wait(timeout=1)
            rejected_calls = [
                executor.submit(repository.search, {"query": {"match_all": {}}})
                for _ in range(3)
            ]
            for call in rejected_calls:
                with pytest.raises(SearchDependencyUnavailableError):
                    call.result(timeout=1)
        finally:
            client.release.set()

        assert [call.result(timeout=1) for call in active_calls] == [
            EMPTY_SEARCH_RESPONSE,
            EMPTY_SEARCH_RESPONSE,
        ]

    assert client.calls == 2
    assert client.max_active == 2


@pytest.mark.parametrize(
    ("error_factory", "expected_error"),
    [
        (
            lambda: OpenSearchConnectionError("N/A", "connection failed", OSError("node-secret")),
            SearchDependencyUnavailableError,
        ),
        (
            lambda: ConnectionTimeout("TIMEOUT", "read timed out", TimeoutError("node-secret")),
            SearchDependencyTimeoutError,
        ),
        (lambda: TransportError(429, "too many requests", {"secret": "raw"}), SearchDependencyUnavailableError),
        (lambda: TransportError(500, "server error", {"secret": "raw"}), SearchDependencyUnavailableError),
        (lambda: TransportError(502, "bad gateway", {"secret": "raw"}), SearchDependencyUnavailableError),
        (lambda: TransportError(503, "unavailable", {"secret": "raw"}), SearchDependencyUnavailableError),
        (lambda: TransportError(504, "gateway timeout", {"secret": "raw"}), SearchDependencyTimeoutError),
        (lambda: RequestError(400, "bad request", {"secret": "raw"}), OpenSearchQueryError),
        (lambda: AuthenticationException(401, "bad auth", {"secret": "raw"}), OpenSearchQueryError),
        (lambda: NotFoundError(404, "missing index", {"secret": "raw"}), OpenSearchQueryError),
        (lambda: SerializationError("raw-secret"), OpenSearchQueryError),
        (lambda: ImproperlyConfigured("raw-secret"), OpenSearchQueryError),
    ],
)
def test_repository_translates_known_opensearch_failures(error_factory, expected_error):
    client = ScriptedClient([error_factory()])
    repository = OpenSearchRepository(settings=_settings(), client=client)

    with pytest.raises(expected_error) as captured:
        repository.search({"query": {"match_all": {}}})

    assert len(client.calls) == 1
    assert "secret" not in str(captured.value)


def test_repository_does_not_translate_unknown_programming_failure():
    error = RuntimeError("program-secret")
    repository = OpenSearchRepository(
        settings=_settings(),
        client=ScriptedClient([error]),
    )

    with pytest.raises(RuntimeError) as captured:
        repository.search({"query": {"match_all": {}}})

    assert captured.value is error


def test_empty_hits_remain_business_not_found_instead_of_dependency_error():
    repository = OpenSearchRepository(
        settings=_settings(),
        client=ScriptedClient([EMPTY_SEARCH_RESPONSE] * 3),
    )

    with pytest.raises(PatentNotFoundError):
        DetailService(repository).get_detail("missing")


def test_repository_retries_transient_failure_inside_one_deadline(caplog):
    clock = FakeClock()
    client = ScriptedClient(
        [
            OpenSearchConnectionError("N/A", "connection failed", OSError("node-secret")),
            EMPTY_SEARCH_RESPONSE,
        ]
    )
    repository = OpenSearchRepository(
        settings=_settings(opensearch_max_retries=1, opensearch_retry_backoff_seconds=0.5),
        client=client,
        clock=clock,
        sleeper=clock.sleep,
    )
    caplog.set_level(logging.INFO, logger="app.repositories.opensearch_repo")

    token = bind_request_id("dependency-request-123")
    try:
        result = repository.search({"query": {"match_all": {}}})
    finally:
        reset_request_id(token)

    assert result == EMPTY_SEARCH_RESPONSE
    assert len(client.calls) == 2
    assert clock.sleeps == [0.5]
    assert _timeout_total(client.calls[0]) == 10.0
    assert _timeout_total(client.calls[1]) == 9.5
    assert "retry_count=1" in caplog.text
    assert "node-secret" not in caplog.text
    retry_record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "dependency_retry"
    )
    assert retry_record.request_id == "dependency-request-123"
    assert retry_record.dependency == "opensearch"
    assert retry_record.operation == "search"
    assert retry_record.outcome == "retrying"
    assert retry_record.retry_count == 1
    completion = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "dependency_call_completed"
    )
    assert completion.request_id == "dependency-request-123"
    assert completion.outcome == "success"
    assert completion.elapsed_ms == 500.0
    assert completion.retry_count == 1


def test_repository_does_not_retry_non_transient_failure():
    client = ScriptedClient(
        [
            RequestError(400, "bad request", {"secret": "raw"}),
            EMPTY_SEARCH_RESPONSE,
        ]
    )
    repository = OpenSearchRepository(
        settings=_settings(opensearch_max_retries=1),
        client=client,
    )

    with pytest.raises(OpenSearchQueryError):
        repository.search({"query": {"match_all": {}}})

    assert len(client.calls) == 1


def test_shared_deadline_stops_later_identifier_queries():
    clock = FakeClock()

    def consume_deadline(call):
        clock.advance(5)
        return EMPTY_SEARCH_RESPONSE

    client = ScriptedClient([consume_deadline, EMPTY_SEARCH_RESPONSE])
    repository = OpenSearchRepository(
        settings=_settings(),
        client=client,
        clock=clock,
        sleeper=clock.sleep,
    )

    with request_deadline(5, clock=clock):
        with pytest.raises(SearchDependencyTimeoutError):
            repository.get_patent_by_identifier("missing")

    assert len(client.calls) == 1
    assert _timeout_total(client.calls[0]) == 5.0


def test_repository_rejects_success_returned_after_deadline():
    clock = FakeClock()

    def return_after_deadline(call):
        clock.advance(5)
        return EMPTY_SEARCH_RESPONSE

    client = ScriptedClient([return_after_deadline])
    repository = OpenSearchRepository(
        settings=_settings(),
        client=client,
        clock=clock,
    )

    with request_deadline(5, clock=clock):
        with pytest.raises(SearchDependencyTimeoutError):
            repository.search({"query": {"match_all": {}}})

    assert len(client.calls) == 1


def test_repository_does_not_retry_when_backoff_exceeds_remaining_budget():
    clock = FakeClock()

    def fail_near_deadline(call):
        clock.advance(4.9)
        raise OpenSearchConnectionError("N/A", "connection failed", OSError("node-secret"))

    client = ScriptedClient([fail_near_deadline, EMPTY_SEARCH_RESPONSE])
    repository = OpenSearchRepository(
        settings=_settings(opensearch_max_retries=1, opensearch_retry_backoff_seconds=0.2),
        client=client,
        clock=clock,
        sleeper=clock.sleep,
    )

    with request_deadline(5, clock=clock):
        with pytest.raises(SearchDependencyTimeoutError):
            repository.search({"query": {"match_all": {}}})

    assert len(client.calls) == 1
    assert clock.sleeps == []


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"hits": []},
        {"hits": {"hits": {}}},
        {"hits": {"hits": [None]}},
        {"hits": {"hits": [{"_source": []}]}},
        {"hits": {"hits": [{"_source": {}, "_score": "1.0"}]}},
        {"hits": {"total": "1", "hits": []}},
        {"hits": {"total": {}, "hits": []}},
        {"took": "1", "hits": {"hits": []}},
    ],
)
def test_malformed_search_response_is_non_retryable_50001(response):
    client = ScriptedClient([response, EMPTY_SEARCH_RESPONSE])
    repository = OpenSearchRepository(
        settings=_settings(opensearch_max_retries=1),
        client=client,
    )

    with pytest.raises(OpenSearchQueryError):
        repository.search({"query": {"match_all": {}}})

    assert len(client.calls) == 1


@pytest.mark.parametrize("response", [None, {}, {"count": "1"}, {"count": True}])
def test_malformed_count_response_is_50001(response):
    repository = OpenSearchRepository(
        settings=_settings(),
        client=ScriptedClient([response]),
    )

    with pytest.raises(OpenSearchQueryError):
        repository.count({"query": {"match_all": {}}})
