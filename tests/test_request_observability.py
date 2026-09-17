"""验证请求关联 ID、结构化日志、路由模板和 Uvicorn 异常抑制的观测契约。"""

from io import StringIO
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_detail_service
from app.core.error_handlers import register_error_handlers
from app.core.exceptions import ErrorCode, service_error
from app.core.logging import JsonLogFormatter, log_event
from app.core.request_context import (
    REQUEST_ID_HEADER,
    REQUEST_ID_MAX_LENGTH,
    UNMATCHED_ROUTE,
    current_request_id,
    request_id_from_scope,
)
from app.core.security import require_api_key, require_console_access
from app.main import app as service_app


ROOT = Path(__file__).resolve().parents[1]


def _test_app() -> FastAPI:
    app = FastAPI()

    @app.get("/api/patent/detail/{patent_id}")
    def api_success(patent_id: str):
        return {"id": patent_id}

    @app.get("/console-api/detail/{patent_id}")
    def console_success(patent_id: str):
        return {"id": patent_id}

    @app.get("/known-error")
    def known_error():
        raise service_error(ErrorCode.SEARCH_DEPENDENCY_TIMEOUT)

    @app.get("/unexpected-error")
    def unexpected_error():
        raise RuntimeError("secret exception detail")

    register_error_handlers(app)
    return app


def _completion_records(caplog):
    return [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "http_request_completed"
    ]


def test_valid_request_id_is_reused_for_api_and_route_log_uses_template(caplog):
    request_id = "caller.Request_123-abc"
    sensitive_patent_id = "CN-SECRET-123"
    caplog.set_level(logging.INFO)

    response = TestClient(_test_app()).get(
        f"/api/patent/detail/{sensitive_patent_id}",
        headers={REQUEST_ID_HEADER: request_id},
    )

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == request_id
    completion = _completion_records(caplog)[-1]
    assert completion.request_id == request_id
    assert completion.route == "/api/patent/detail/{patent_id}"
    assert completion.method == "GET"
    assert completion.status == 200
    assert completion.code == 0
    assert completion.elapsed_ms >= 0
    assert sensitive_patent_id not in completion.getMessage()


def test_console_uses_the_same_request_id_contract(caplog):
    request_id = "console-123"
    caplog.set_level(logging.INFO)

    response = TestClient(_test_app()).get(
        "/console-api/detail/CN-HIDDEN",
        headers={REQUEST_ID_HEADER: request_id},
    )

    assert response.headers[REQUEST_ID_HEADER] == request_id
    completion = _completion_records(caplog)[-1]
    assert completion.request_id == request_id
    assert completion.route == "/console-api/detail/{patent_id}"


def test_api_and_console_workers_preserve_request_context(client):
    class ContextRecordingDetailService:
        def __init__(self):
            self.request_ids = []

        def get_detail(self, patent_id, include_description=False):
            self.request_ids.append(current_request_id())
            return {"id": patent_id}

    service = ContextRecordingDetailService()
    service_app.dependency_overrides[get_detail_service] = lambda: service
    service_app.dependency_overrides[require_api_key] = lambda: None
    service_app.dependency_overrides[require_console_access] = lambda: None
    try:
        test_client = client()
        api_response = test_client.get(
            "/api/patent/detail/CN-API",
            headers={REQUEST_ID_HEADER: "api-worker-123"},
        )
        console_response = test_client.get(
            "/console-api/detail/CN-CONSOLE",
            headers={REQUEST_ID_HEADER: "console-worker-123"},
        )
    finally:
        service_app.dependency_overrides.clear()

    assert api_response.status_code == 200
    assert console_response.status_code == 200
    assert service.request_ids == ["api-worker-123", "console-worker-123"]


def test_missing_or_invalid_request_id_is_replaced_with_safe_random_id(caplog):
    caplog.set_level(logging.INFO)
    client = TestClient(_test_app())

    for supplied in (None, "contains space", "x" * (REQUEST_ID_MAX_LENGTH + 1)):
        headers = {} if supplied is None else {REQUEST_ID_HEADER: supplied}
        response = client.get("/api/patent/detail/CN-HIDDEN", headers=headers)
        request_id = response.headers[REQUEST_ID_HEADER]

        assert len(request_id) == 32
        assert request_id.isascii()
        assert request_id.isalnum()
        assert request_id != supplied
        assert _completion_records(caplog)[-1].request_id == request_id


def test_control_unicode_and_duplicate_request_ids_are_never_trusted():
    invalid_values = (
        [(b"x-request-id", b"safe\nforged")],
        [(b"x-request-id", "请求".encode())],
        [(b"x-request-id", b"first"), (b"x-request-id", b"second")],
    )

    for headers in invalid_values:
        request_id = request_id_from_scope({"type": "http", "headers": headers})
        assert len(request_id) == 32
        assert request_id.isalnum()
        assert "\n" not in request_id


def test_known_error_uses_one_request_id_in_header_body_and_completion_log(caplog):
    request_id = "known-error-123"
    caplog.set_level(logging.INFO)

    response = TestClient(_test_app()).get(
        "/known-error",
        headers={REQUEST_ID_HEADER: request_id},
    )

    assert response.status_code == 504
    assert response.headers[REQUEST_ID_HEADER] == request_id
    assert response.json()["request_id"] == request_id
    completion = _completion_records(caplog)[-1]
    assert completion.request_id == request_id
    assert completion.status == 504
    assert completion.code == 50401


def test_unknown_exception_logs_only_safe_type_and_request_id(caplog):
    request_id = "unexpected-123"
    caplog.set_level(logging.INFO)

    response = TestClient(
        _test_app(),
        raise_server_exceptions=False,
    ).get(
        "/unexpected-error",
        headers={REQUEST_ID_HEADER: request_id},
    )

    assert response.status_code == 500
    assert response.json()["request_id"] == request_id
    exception_record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "unhandled_exception"
    )
    assert exception_record.request_id == request_id
    assert exception_record.exception_type == "RuntimeError"
    assert "secret exception detail" not in caplog.text
    completion = _completion_records(caplog)[-1]
    assert completion.code == 50002


def test_unmatched_route_uses_a_fixed_low_cardinality_template(caplog):
    caplog.set_level(logging.INFO)

    response = TestClient(_test_app()).get("/missing/CN-SECRET-123")

    assert response.status_code == 404
    completion = _completion_records(caplog)[-1]
    assert completion.route == UNMATCHED_ROUTE
    assert "CN-SECRET-123" not in completion.getMessage()


def test_json_formatter_emits_one_parseable_object_with_allowlisted_fields():
    output = StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(JsonLogFormatter())
    test_logger = logging.getLogger("test.issue53.json")
    test_logger.handlers = [handler]
    test_logger.propagate = False
    test_logger.setLevel(logging.INFO)
    try:
        log_event(
            test_logger,
            logging.INFO,
            "dependency_call_completed",
            request_id="request-123",
            dependency="opensearch",
            operation="search",
            outcome="success",
            elapsed_ms=1.25,
            retry_count=0,
            operation_id="11111111-1111-4111-8111-111111111111",
        )
    finally:
        test_logger.handlers = []
        test_logger.propagate = True

    payload = json.loads(output.getvalue())
    assert payload["event"] == "dependency_call_completed"
    assert payload["request_id"] == "request-123"
    assert payload["dependency"] == "opensearch"
    assert payload["operation"] == "search"
    assert payload["outcome"] == "success"
    assert payload["elapsed_ms"] == 1.25
    assert payload["retry_count"] == 0
    assert payload["operation_id"] == "11111111-1111-4111-8111-111111111111"
    assert "message" not in payload


def test_deployment_uses_bounded_journal_instead_of_unrotated_files():
    service_unit = (ROOT / "deployment/patent-search-service.service").read_text()
    mcp_unit = (ROOT / "deployment/patent-mcp.service").read_text()
    makefile = (ROOT / "Makefile").read_text()
    retention = (
        ROOT / "deployment/journald/60-patent-search-retention.conf"
    ).read_text()

    assert "--no-access-log" in service_unit
    assert "--no-access-log" in makefile
    assert "StandardOutput=journal" in service_unit
    assert "StandardError=journal" in service_unit
    assert "StandardOutput=journal" in mcp_unit
    assert "StandardError=journal" in mcp_unit
    assert "LogNamespace=patent-search" in service_unit
    assert "LogNamespace=patent-search" in mcp_unit
    assert "StandardOutput=append:" not in service_unit + mcp_unit
    assert "SystemMaxUse=1G" in retention
    assert "RuntimeMaxUse=256M" in retention
    assert "MaxRetentionSec=14day" in retention
    assert "MaxFileSec=1day" in retention


def test_real_uvicorn_process_suppresses_handled_exception_traceback(tmp_path):
    app_module = tmp_path / "uvicorn_failure_app.py"
    app_module.write_text(
        """
from fastapi import FastAPI

from app.core.error_handlers import register_error_handlers
from app.core.logging import configure_logging

configure_logging()
app = FastAPI()

@app.get("/unexpected")
def unexpected():
    raise RuntimeError("secret downstream body at http://private-node:9200")

register_error_handlers(app)
""".lstrip()
    )
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]

    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "uvicorn_failure_app:app",
            "--app-dir",
            str(tmp_path),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-access-log",
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    response_body = None
    response_headers = None
    deadline = monotonic() + 10
    try:
        while monotonic() < deadline:
            try:
                urlopen(
                    Request(
                        f"http://127.0.0.1:{port}/unexpected",
                        headers={REQUEST_ID_HEADER: "uvicorn-process-123"},
                    ),
                    timeout=0.5,
                )
            except HTTPError as exc:
                if exc.code == 500:
                    response_body = json.loads(exc.read())
                    response_headers = exc.headers
                    break
                raise
            except URLError:
                if process.poll() is not None:
                    break
                sleep(0.05)
            else:
                raise AssertionError("unexpected route returned success")
        assert response_body is not None, "Uvicorn did not return the controlled 500"
    finally:
        process.terminate()
        try:
            output = process.communicate(timeout=5)[0]
        except subprocess.TimeoutExpired:
            process.kill()
            output = process.communicate(timeout=5)[0]

    assert response_headers[REQUEST_ID_HEADER] == "uvicorn-process-123"
    assert response_body["request_id"] == "uvicorn-process-123"
    assert response_body["code"] == 50002
    assert "Traceback" not in output
    assert "secret downstream body" not in output
    assert "private-node" not in output

    payloads = [json.loads(line) for line in output.splitlines() if line.strip()]
    unhandled = [
        payload
        for payload in payloads
        if payload.get("event") == "unhandled_exception"
    ]
    assert len(unhandled) == 1
    assert unhandled[0]["request_id"] == "uvicorn-process-123"
    assert unhandled[0]["exception_type"] == "RuntimeError"
    assert "message" not in unhandled[0]
    assert not any(
        payload.get("message", "").strip() == "Exception in ASGI application"
        for payload in payloads
    )
