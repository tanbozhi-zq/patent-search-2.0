"""查询向量边界只验证受控配置、绝对 deadline 和安全结果。"""

import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import struct
from threading import Thread
from time import monotonic, sleep

import httpx
import pytest

from app.core.exceptions import (
    ErrorCode,
    QueryVectorError,
    QueryVectorInvalidResponseError,
    QueryVectorTimeoutError,
    QueryVectorUnavailableError,
)
from app.integrations.query_vector import (
    ArkQueryVectorAdapter,
    QueryVectorConfig,
    QueryVectorResult,
    remaining_query_vector_seconds,
    validate_query_vector_result,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload or {}

    def json(self):
        return self.payload


class FakeHttpClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []
        self.closed = False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return self.response

    async def aclose(self):
        self.closed = True


@pytest.fixture
def ark_adapter_factory():
    adapters = []

    def factory(client, *, configured=True):
        adapter = ArkQueryVectorAdapter(
            api_url="https://ark.example/embeddings",
            api_key="key" if configured else "",
            model_endpoints={"model-v1": "endpoint"} if configured else {},
            max_connections=2,
            client=client,
        )
        adapters.append(adapter)
        return adapter

    yield factory

    for adapter in adapters:
        adapter.close()


def test_ark_query_vector_adapter_uses_base64_and_remaining_deadline(
    ark_adapter_factory,
):
    encoded = base64.b64encode(struct.pack("<2f", 1.0, -0.5)).decode()
    client = FakeHttpClient(
        FakeResponse(
            payload={
                "model": "model-v1",
                "data": {"embedding": encoded},
            }
        )
    )
    adapter = ark_adapter_factory(client)

    result = adapter.generate(
        "semantic input",
        config=QueryVectorConfig(model="model-v1", dimensions=2),
        deadline=monotonic() + 10,
    )

    assert result.vector == (1.0, -0.5)
    _, call = client.calls[0]
    assert call["json"]["encoding_format"] == "base64"
    assert call["json"]["dimensions"] == 2
    assert call["json"]["model"] == "endpoint"
    assert 0 < call["timeout"] <= 10
    adapter.close()
    assert client.closed is True


@pytest.mark.parametrize(
    ("client", "error_type"),
    [
        (FakeHttpClient(FakeResponse(status_code=429)), QueryVectorUnavailableError),
        (FakeHttpClient(FakeResponse(status_code=500)), QueryVectorUnavailableError),
        (FakeHttpClient(FakeResponse(status_code=400)), QueryVectorError),
        (FakeHttpClient(FakeResponse(status_code=302)), QueryVectorError),
        (
            FakeHttpClient(error=httpx.ReadTimeout("timeout")),
            QueryVectorTimeoutError,
        ),
        (
            FakeHttpClient(error=httpx.ConnectError("unavailable")),
            QueryVectorUnavailableError,
        ),
    ],
)
def test_ark_query_vector_adapter_classifies_provider_failures(
    client,
    error_type,
    ark_adapter_factory,
):
    with pytest.raises(error_type):
        ark_adapter_factory(client).generate(
            "semantic input",
            config=QueryVectorConfig(model="model-v1", dimensions=2),
            deadline=monotonic() + 10,
        )


def test_ark_query_vector_adapter_fails_closed_when_unconfigured_or_malformed(
    ark_adapter_factory,
):
    unconfigured = FakeHttpClient()
    with pytest.raises(QueryVectorUnavailableError):
        ark_adapter_factory(unconfigured, configured=False).generate(
            "semantic input",
            config=QueryVectorConfig(model="model-v1", dimensions=2),
            deadline=monotonic() + 10,
        )
    assert unconfigured.calls == []

    missing_route = FakeHttpClient()
    with pytest.raises(QueryVectorUnavailableError):
        ark_adapter_factory(missing_route).generate(
            "semantic input",
            config=QueryVectorConfig(model="model-v2", dimensions=2),
            deadline=monotonic() + 10,
        )
    assert missing_route.calls == []

    malformed = FakeHttpClient(FakeResponse(payload={"model": "model-v1"}))
    with pytest.raises(QueryVectorInvalidResponseError):
        ark_adapter_factory(malformed).generate(
            "semantic input",
            config=QueryVectorConfig(model="model-v1", dimensions=2),
            deadline=monotonic() + 10,
        )

    numeric_model = FakeHttpClient(
        FakeResponse(payload={"model": 1, "data": {"embedding": [1.0, 0.0]}})
    )
    with pytest.raises(QueryVectorInvalidResponseError):
        ark_adapter_factory(numeric_model).generate(
            "semantic input",
            config=QueryVectorConfig(model="model-v1", dimensions=2),
            deadline=monotonic() + 10,
        )


def test_ark_query_vector_adapter_releases_pool_slot_after_repeated_deadlines():
    response_body = json.dumps(
        {"model": "model-v1", "data": {"embedding": [1.0, 0.0]}}
    ).encode()
    provider_calls = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            payload = json.loads(
                self.rfile.read(int(self.headers.get("content-length", "0")))
            )
            text = payload["input"][0]["text"]
            provider_calls.append(text)
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(response_body)))
            self.end_headers()
            chunks = response_body if text == "slow" else [response_body]
            for chunk in chunks:
                try:
                    self.wfile.write(
                        bytes([chunk]) if isinstance(chunk, int) else chunk
                    )
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
                if text == "slow":
                    sleep(0.005)

        def log_message(self, *args):
            pass

    class QuietServer(ThreadingHTTPServer):
        def handle_error(self, request, client_address):
            pass

    server = QuietServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    adapter = ArkQueryVectorAdapter(
        api_url=f"http://127.0.0.1:{server.server_port}/embeddings",
        api_key="key",
        model_endpoints={"model-v1": "endpoint"},
        max_connections=1,
    )
    config = QueryVectorConfig(model="model-v1", dimensions=2)

    try:
        for _ in range(12):
            with pytest.raises(QueryVectorTimeoutError):
                adapter.generate(
                    "slow",
                    config=config,
                    deadline=monotonic() + 0.03,
                )
            result = adapter.generate(
                "fast",
                config=config,
                deadline=monotonic() + 0.2,
            )
            assert result.vector == (1.0, 0.0)
    finally:
        adapter.close()
        server.shutdown()
        server.server_close()
        server_thread.join()

    assert provider_calls == ["slow", "fast"] * 12


def test_query_vector_result_is_validated_and_normalized_to_immutable_floats():
    config = QueryVectorConfig(model="model-v1", dimensions=3)

    result = validate_query_vector_result(
        QueryVectorResult(model="model-v1", vector=(1, 2.5, -3)),
        config=config,
    )

    assert result == QueryVectorResult(model="model-v1", vector=(1.0, 2.5, -3.0))
    assert isinstance(result.vector, tuple)


@pytest.mark.parametrize(
    "result",
    [
        QueryVectorResult(model="wrong-model", vector=(1.0, 2.0)),
        QueryVectorResult(model="model-v1", vector=(1.0,)),
        QueryVectorResult(model="model-v1", vector=(True, 2.0)),
        QueryVectorResult(model="model-v1", vector=("1.0", 2.0)),
        QueryVectorResult(model="model-v1", vector=(math.nan, 2.0)),
        QueryVectorResult(model="model-v1", vector=(math.inf, 2.0)),
        QueryVectorResult(model="model-v1", vector=(10**400, 2.0)),
        QueryVectorResult(model="model-v1", vector=None),
    ],
)
def test_query_vector_result_rejects_model_dimension_type_and_finite_value_errors(result):
    with pytest.raises(QueryVectorInvalidResponseError) as captured:
        validate_query_vector_result(
            result,
            config=QueryVectorConfig(model="model-v1", dimensions=2),
        )

    assert captured.value.code == ErrorCode.SEARCH_DEPENDENCY_ERROR


def test_query_vector_result_rejects_all_zero_cosine_vector():
    with pytest.raises(QueryVectorInvalidResponseError) as captured:
        validate_query_vector_result(
            QueryVectorResult(model="model-v1", vector=(0, -0.0)),
            config=QueryVectorConfig(model="model-v1", dimensions=2),
        )

    assert captured.value.code == ErrorCode.SEARCH_DEPENDENCY_ERROR


@pytest.mark.parametrize(
    "config",
    [
        {"model": "", "dimensions": 2048},
        {"model": "   ", "dimensions": 2048},
        {"model": 123, "dimensions": 2048},
        {"model": "model-v1", "dimensions": 0},
        {"model": "model-v1", "dimensions": True},
        {"model": "model-v1", "dimensions": 1.5},
    ],
)
def test_query_vector_config_rejects_incomplete_model_contract(config):
    with pytest.raises(ValueError):
        QueryVectorConfig(**config)


def test_query_vector_uses_remaining_absolute_deadline():
    assert remaining_query_vector_seconds(12.5, clock=lambda: 10.0) == 2.5

    with pytest.raises(QueryVectorTimeoutError) as captured:
        remaining_query_vector_seconds(10.0, clock=lambda: 10.0)

    assert captured.value.code == ErrorCode.SEARCH_DEPENDENCY_TIMEOUT
    assert QueryVectorUnavailableError.code == ErrorCode.SEARCH_DEPENDENCY_UNAVAILABLE


def test_query_vector_validation_errors_do_not_echo_sensitive_input():
    secret_text = "semantic-secret-7fbc"
    secret_vector_value = "vector-secret-91aa"

    with pytest.raises(QueryVectorInvalidResponseError) as captured:
        validate_query_vector_result(
            QueryVectorResult(
                model="model-v1",
                vector=(secret_vector_value, secret_text),
            ),
            config=QueryVectorConfig(model="model-v1", dimensions=2),
        )

    assert secret_text not in str(captured.value)
    assert secret_vector_value not in str(captured.value)


def test_query_vector_result_repr_does_not_expose_vector():
    secret_vector_value = "vector-secret-5ab2"

    result = QueryVectorResult(model="model-v1", vector=(secret_vector_value,))

    assert secret_vector_value not in repr(result)
