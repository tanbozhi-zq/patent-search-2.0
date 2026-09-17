"""验证管理日志的有界读取、筛选、分页游标、降级与最小审计契约。"""

import asyncio
from datetime import datetime, timezone
import json
import logging
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.core.admin_logs as admin_logs
from app.api.admin import get_admin_log_reader
from app.api.admin_config import _audit as audit_admin_config
from app.core.admin_logs import (
    ADMIN_LOG_JOURNAL_NAMESPACE,
    ADMIN_LOG_MAX_MESSAGE_BYTES,
    AdminLogFilters,
    ProcessAdminLogReader,
    SystemdJournalAdminLogReader,
    UnavailableAdminLogReader,
    create_systemd_journal_reader,
)
from app.core.config import Settings, get_settings
from app.core.logging import (
    StructuredLogBuffer,
    StructuredLogBufferHandler,
    log_event,
)
from app.core.request_context import bind_request_id, reset_request_id
from app.core.security import AdminPrincipal
from app.main import app
from app.schemas.admin import AdminLogsResponse


def _buffered_logger(capacity=10):
    buffer = StructuredLogBuffer(capacity=capacity)
    handler = StructuredLogBufferHandler(buffer)
    logger = logging.getLogger(f"test.admin.logs.{id(buffer)}")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return buffer, logger


def test_process_log_reader_filters_paginates_and_ignores_unstructured_messages():
    buffer, logger = _buffered_logger(capacity=4)
    logger.info("SENSITIVE_UNSTRUCTURED_MESSAGE")
    for index in range(5):
        log_event(
            logger,
            logging.INFO,
            "http_request_completed",
            request_id=f"request-{index}",
            route="/api/patent/search",
            method="POST",
            status=200 if index < 4 else 503,
            code=0 if index < 4 else 50301,
            elapsed_ms=float(index),
        )
    reader = ProcessAdminLogReader(buffer)

    first = asyncio.run(
        reader.read(
            window_seconds=3600,
            limit=1,
            cursor=None,
            filters=AdminLogFilters(route="/api/patent/search"),
        )
    )
    second = asyncio.run(
        reader.read(
            window_seconds=3600,
            limit=10,
            cursor=first.next_cursor,
            filters=AdminLogFilters(route="/api/patent/search"),
        )
    )

    assert first.truncated is True
    assert first.next_cursor is not None
    assert [item.request_id for item in first.items] == ["request-4"]
    assert [item.request_id for item in second.items] == [
        "request-3",
        "request-2",
        "request-1",
    ]
    assert all("SENSITIVE_UNSTRUCTURED_MESSAGE" not in item.model_dump_json() for item in first.items + second.items)


def test_process_log_reader_filters_system_activity_before_pagination():
    buffer, logger = _buffered_logger(capacity=10)
    log_event(
        logger,
        logging.INFO,
        "http_request_completed",
        request_id="business-older",
        route="/api/patent/search",
        method="POST",
        status=200,
        code=0,
    )
    log_event(
        logger,
        logging.INFO,
        "readiness_check_completed",
        operation="readiness",
        outcome="success",
    )
    log_event(
        logger,
        logging.INFO,
        "http_request_completed",
        request_id="business-newer",
        route="/api/patent/detail/{patent_id}",
        method="GET",
        status=200,
        code=0,
    )
    log_event(
        logger,
        logging.INFO,
        "http_request_completed",
        request_id="metrics-scrape",
        route="/metrics",
        method="GET",
        status=200,
        code=0,
    )
    reader = ProcessAdminLogReader(buffer)
    filters = AdminLogFilters(include_system=False)

    first = asyncio.run(
        reader.read(
            window_seconds=3600,
            limit=1,
            cursor=None,
            filters=filters,
        )
    )
    second = asyncio.run(
        reader.read(
            window_seconds=3600,
            limit=10,
            cursor=first.next_cursor,
            filters=filters,
        )
    )

    assert [item.request_id for item in first.items] == ["business-newer"]
    assert first.next_cursor is not None
    assert [item.request_id for item in second.items] == ["business-older"]
    assert first.scanned_count > len(first.items)


def test_process_log_reader_rejects_invalid_opaque_cursor():
    buffer, _logger = _buffered_logger()
    reader = ProcessAdminLogReader(buffer)

    with pytest.raises(ValueError, match="invalid log cursor"):
        asyncio.run(
            reader.read(
                window_seconds=3600,
                limit=10,
                cursor="not-a-valid-cursor",
                filters=AdminLogFilters(),
            )
        )


@pytest.mark.parametrize(
    ("window_seconds", "filters"),
    [
        (900, AdminLogFilters(route="/api/patent/search")),
        (3600, AdminLogFilters(request_id="different-request")),
        (3600, AdminLogFilters(route="__unmatched__")),
        (3600, AdminLogFilters(route="/api/patent/search", code=50301)),
        (
            3600,
            AdminLogFilters(
                route="/api/patent/search",
                include_system=False,
            ),
        ),
    ],
)
def test_process_log_cursor_is_bound_to_window_and_filters(window_seconds, filters):
    buffer, logger = _buffered_logger(capacity=4)
    for index in range(3):
        log_event(
            logger,
            logging.INFO,
            "http_request_completed",
            request_id=f"request-{index}",
            route="/api/patent/search",
            method="POST",
            status=200,
            code=0,
        )
    reader = ProcessAdminLogReader(buffer)
    original_filters = AdminLogFilters(route="/api/patent/search")
    first = asyncio.run(
        reader.read(
            window_seconds=3600,
            limit=1,
            cursor=None,
            filters=original_filters,
        )
    )

    with pytest.raises(ValueError, match="invalid log cursor"):
        asyncio.run(
            reader.read(
                window_seconds=window_seconds,
                limit=10,
                cursor=first.next_cursor,
                filters=filters,
            )
        )


def test_admin_polling_and_denials_do_not_evict_business_correlation_events():
    buffer, logger = _buffered_logger(capacity=2)
    log_event(
        logger,
        logging.INFO,
        "http_request_completed",
        request_id="business-request",
        route="/api/patent/search",
        status=200,
        code=0,
    )
    for _ in range(5):
        log_event(
            logger,
            logging.INFO,
            "admin_read_completed",
            actor="viewer",
            action="metrics.read",
            result="ok",
        )
        log_event(
            logger,
            logging.WARNING,
            "http_request_completed",
            request_id="admin-request",
            route="/admin-api/v1/metrics",
            status=401,
            code=40101,
        )

    records = buffer.snapshot()
    assert len(records) == 1
    assert records[0]["request_id"] == "business-request"


def _journal_entry(
    cursor,
    *,
    request_id,
    code=50301,
    event="http_request_completed",
    route="/api/patent/search",
    message_overrides=None,
):
    payload = {
        "event": event,
        "request_id": request_id,
        "route": route,
        "method": "POST",
        "status": 503,
        "code": code,
        "elapsed_ms": 12.5,
    }
    payload.update(message_overrides or {})
    return {
        "__CURSOR": cursor,
        "__REALTIME_TIMESTAMP": datetime.now(timezone.utc),
        "MESSAGE": json.dumps(payload),
    }


class _FakeJournalReader:
    def __init__(self, entries, namespace, calls):
        self.entries = entries
        self.namespace = namespace
        self.calls = calls
        self.index = len(entries)
        self.data_threshold = None
        calls.append(("open", namespace))

    def add_match(self, **kwargs):
        self.calls.append(("match", kwargs))

    def add_disjunction(self):
        self.calls.append(("or",))

    def seek_tail(self):
        self.index = len(self.entries)

    def seek_cursor(self, cursor):
        self.index = next(
            index
            for index, entry in enumerate(self.entries)
            if entry["__CURSOR"] == cursor
        )

    def get_previous(self):
        if self.index <= 0:
            return {}
        self.index -= 1
        return self.entries[self.index]

    def close(self):
        self.calls.append(("close",))


def test_systemd_journal_reader_uses_fixed_namespace_matches_bounds_and_cursor():
    calls = []
    entries = [
        _journal_entry("cursor-1", request_id="request-1"),
        {
            "__CURSOR": "cursor-unstructured",
            "__REALTIME_TIMESTAMP": datetime.now(timezone.utc),
            "MESSAGE": "SENSITIVE_UNSTRUCTURED_MESSAGE",
        },
        _journal_entry("cursor-2", request_id="request-2"),
    ]

    def factory(*, namespace):
        return _FakeJournalReader(entries, namespace, calls)

    async def scenario():
        reader = SystemdJournalAdminLogReader(
            reader_factory=factory,
            allowed_routes=frozenset({"/api/patent/search"}),
        )
        try:
            first = await reader.read(
                window_seconds=3600,
                limit=1,
                cursor=None,
                filters=AdminLogFilters(),
            )
            second = await reader.read(
                window_seconds=3600,
                limit=10,
                cursor=first.next_cursor,
                filters=AdminLogFilters(),
            )
            return first, second
        finally:
            await reader.close()

    first, second = asyncio.run(scenario())
    assert first.scope == "journal_local"
    assert first.available is True
    assert first.truncated is True
    assert [item.request_id for item in first.items] == ["request-2"]
    assert [item.request_id for item in second.items] == ["request-1"]
    assert ("open", ADMIN_LOG_JOURNAL_NAMESPACE) in calls
    assert (
        "match",
        {
            "_SYSTEMD_UNIT": "patent-search-service.service",
            "SYSLOG_IDENTIFIER": "patent-search-service",
        },
    ) in calls
    assert ("or",) in calls
    assert "SENSITIVE_UNSTRUCTURED_MESSAGE" not in first.model_dump_json()


def test_systemd_journal_reader_filters_system_activity_before_pagination():
    calls = []
    entries = [
        _journal_entry("business-older", request_id="business-older"),
        _journal_entry(
            "ready",
            request_id="ready-request",
            route="/ready",
        ),
        _journal_entry("business-newer", request_id="business-newer"),
        _journal_entry(
            "metrics",
            request_id="metrics-request",
            route="/metrics",
        ),
    ]
    reader = SystemdJournalAdminLogReader(
        reader_factory=lambda *, namespace: _FakeJournalReader(
            entries,
            namespace,
            calls,
        ),
        allowed_routes=frozenset({"/api/patent/search", "/ready", "/metrics"}),
    )

    async def scenario():
        filters = AdminLogFilters(include_system=False)
        try:
            first = await reader.read(
                window_seconds=3600,
                limit=1,
                cursor=None,
                filters=filters,
            )
            second = await reader.read(
                window_seconds=3600,
                limit=10,
                cursor=first.next_cursor,
                filters=filters,
            )
            return first, second
        finally:
            await reader.close()

    first, second = asyncio.run(scenario())
    assert [item.request_id for item in first.items] == ["business-newer"]
    assert first.next_cursor is not None
    assert [item.request_id for item in second.items] == ["business-older"]


def test_systemd_journal_reader_keeps_runtime_config_audit_ids_without_sensitive_fields():
    operation_id = "11111111-1111-4111-8111-111111111111"
    reason = "SENTINEL_RUNTIME_AUDIT_REASON"
    idempotency_key = "SENTINEL_RUNTIME_IDEMPOTENCY_KEY"
    entries = [
        _journal_entry(
            "runtime-audit",
            request_id=None,
            code=None,
            event="admin_runtime_config_completed",
            route=None,
            message_overrides={
                "actor": "admin",
                "role": "admin",
                "action": "runtime_config.rollback",
                "result": "rolled_back",
                "operation_id": operation_id,
                "draft_id": "22222222-2222-4222-8222-222222222222",
                "reason": reason,
                "idempotency_key": idempotency_key,
                "values": {"opensearch.timeout_seconds": 3},
            },
        )
    ]
    reader = SystemdJournalAdminLogReader(
        reader_factory=lambda *, namespace: _FakeJournalReader(entries, namespace, []),
        allowed_routes=frozenset(),
    )

    async def scenario():
        try:
            included = await reader.read(
                window_seconds=3600,
                limit=10,
                cursor=None,
                filters=AdminLogFilters(include_system=True),
            )
            excluded = await reader.read(
                window_seconds=3600,
                limit=10,
                cursor=None,
                filters=AdminLogFilters(include_system=False),
            )
            return included, excluded
        finally:
            await reader.close()

    included, excluded = asyncio.run(scenario())

    [event] = included.items
    assert event.event == "admin_runtime_config_completed"
    assert event.operation_id == operation_id
    assert event.action == "runtime_config.rollback"
    assert event.result == "rolled_back"
    assert excluded.items == []
    rendered = included.model_dump_json()
    assert reason not in rendered
    assert idempotency_key not in rendered
    assert "opensearch.timeout_seconds" not in rendered


def test_config_audit_carries_request_id_and_is_safe_for_journal_readback(caplog):
    request_id = "issue47-config-audit-001"
    draft_id = "22222222-2222-4222-8222-222222222222"
    token = bind_request_id(request_id)
    try:
        caplog.set_level(logging.INFO)
        audit_admin_config(
            AdminPrincipal(subject="admin"),
            action="config_draft.create",
            result="validated",
            draft_id=draft_id,
            changed_parameter_count=1,
            validation_error_count=0,
        )
    finally:
        reset_request_id(token)

    record = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "admin_config_completed"
    )
    assert getattr(record, "request_id") == request_id
    assert getattr(record, "draft_id") == draft_id

    entries = [
        _journal_entry(
            "config-audit",
            request_id=request_id,
            code=None,
            event="admin_config_completed",
            route=None,
            message_overrides={
                "actor": "admin",
                "role": "admin",
                "action": "config_draft.create",
                "result": "validated",
                "draft_id": draft_id,
                "changed_parameter_count": 1,
                "validation_error_count": 0,
                "reason": "SENTINEL_CONFIG_REASON",
                "candidate_values": {"opensearch.max_retries": 0},
            },
        )
    ]
    reader = SystemdJournalAdminLogReader(
        reader_factory=lambda *, namespace: _FakeJournalReader(entries, namespace, []),
        allowed_routes=frozenset(),
    )

    async def scenario():
        try:
            included = await reader.read(
                window_seconds=3600,
                limit=10,
                cursor=None,
                filters=AdminLogFilters(request_id=request_id),
            )
            excluded = await reader.read(
                window_seconds=3600,
                limit=10,
                cursor=None,
                filters=AdminLogFilters(
                    request_id=request_id,
                    include_system=False,
                ),
            )
            return included, excluded
        finally:
            await reader.close()

    response, excluded = asyncio.run(scenario())
    [event] = response.items
    assert event.event == "admin_config_completed"
    assert event.request_id == request_id
    assert event.draft_id == draft_id
    assert event.action == "config_draft.create"
    assert event.result == "validated"
    assert excluded.items == []
    rendered = response.model_dump_json()
    assert "SENTINEL_CONFIG_REASON" not in rendered
    assert "opensearch.max_retries" not in rendered


def test_systemd_journal_reader_keeps_config_store_failures_without_sensitive_fields():
    request_id = "issue47-config-store-failure-001"
    entries = [
        _journal_entry(
            "config-store-failure",
            request_id=request_id,
            code=None,
            event="admin_config_store_failed",
            route=None,
            message_overrides={
                "actor": "admin",
                "role": "admin",
                "action": "config_draft.create",
                "result": "failed",
                "reason": "SENTINEL_CONFIG_STORE_REASON",
                "candidate_values": {"opensearch.max_retries": 0},
                "database_path": "SENTINEL_PRIVATE_DATABASE_PATH",
            },
        )
    ]
    reader = SystemdJournalAdminLogReader(
        reader_factory=lambda *, namespace: _FakeJournalReader(entries, namespace, []),
        allowed_routes=frozenset(),
    )

    async def scenario():
        try:
            included = await reader.read(
                window_seconds=3600,
                limit=10,
                cursor=None,
                filters=AdminLogFilters(request_id=request_id),
            )
            excluded = await reader.read(
                window_seconds=3600,
                limit=10,
                cursor=None,
                filters=AdminLogFilters(
                    request_id=request_id,
                    include_system=False,
                ),
            )
            return included, excluded
        finally:
            await reader.close()

    included, excluded = asyncio.run(scenario())
    [event] = included.items
    assert event.event == "admin_config_store_failed"
    assert event.request_id == request_id
    assert event.action == "config_draft.create"
    assert event.result == "failed"
    assert excluded.items == []
    rendered = included.model_dump_json()
    assert "SENTINEL_CONFIG_STORE_REASON" not in rendered
    assert "opensearch.max_retries" not in rendered
    assert "SENTINEL_PRIVATE_DATABASE_PATH" not in rendered


def test_systemd_journal_reader_rejects_oversized_unknown_and_invalid_events():
    calls = []
    entries = [
        _journal_entry("invalid-code", request_id="request-1", code=49999),
        _journal_entry("unknown-event", request_id="request-2", event="unknown"),
        {
            "__CURSOR": "oversized",
            "__REALTIME_TIMESTAMP": datetime.now(timezone.utc),
            "MESSAGE": "x" * (ADMIN_LOG_MAX_MESSAGE_BYTES + 1),
        },
    ]
    reader = SystemdJournalAdminLogReader(
        reader_factory=lambda *, namespace: _FakeJournalReader(
            entries,
            namespace,
            calls,
        ),
        allowed_routes=frozenset({"/api/patent/search"}),
    )

    async def scenario():
        try:
            return await reader.read(
                window_seconds=3600,
                limit=10,
                cursor=None,
                filters=AdminLogFilters(),
            )
        finally:
            await reader.close()

    response = asyncio.run(scenario())
    assert response.available is True
    assert response.items == []
    assert response.scanned_count == 3


def test_systemd_journal_reader_scan_limit_is_paginated(monkeypatch):
    monkeypatch.setattr(admin_logs, "ADMIN_LOG_MAX_JOURNAL_SCAN", 2)
    calls = []
    entries = [
        _journal_entry(f"cursor-{index}", request_id=f"request-{index}")
        for index in range(4)
    ]
    reader = SystemdJournalAdminLogReader(
        reader_factory=lambda *, namespace: _FakeJournalReader(
            entries,
            namespace,
            calls,
        ),
        allowed_routes=frozenset({"/api/patent/search"}),
    )

    async def scenario():
        try:
            return await reader.read(
                window_seconds=3600,
                limit=10,
                cursor=None,
                filters=AdminLogFilters(request_id="not-present"),
            )
        finally:
            await reader.close()

    response = asyncio.run(scenario())
    assert response.scanned_count == 2
    assert response.truncated is True
    assert response.next_cursor is not None


def test_systemd_journal_reader_has_one_worker_and_no_request_queue():
    started = Event()
    release = Event()
    calls = []

    class BlockingReader(_FakeJournalReader):
        def get_previous(self):
            started.set()
            release.wait(timeout=1)
            return {}

    reader = SystemdJournalAdminLogReader(
        reader_factory=lambda *, namespace: BlockingReader([], namespace, calls),
        allowed_routes=frozenset(),
    )

    async def scenario():
        first_task = asyncio.create_task(
            reader.read(
                window_seconds=3600,
                limit=10,
                cursor=None,
                filters=AdminLogFilters(),
            )
        )
        await asyncio.to_thread(started.wait, 1)
        second = await reader.read(
            window_seconds=3600,
            limit=10,
            cursor=None,
            filters=AdminLogFilters(),
        )
        release.set()
        first = await first_task
        await reader.close()
        return first, second

    first, second = asyncio.run(scenario())
    assert first.available is True
    assert second.scope == "unavailable"
    assert second.available is False
    assert sum(call[0] == "open" for call in calls) == 1


def test_missing_or_unreadable_systemd_journal_degrades_without_raising(monkeypatch):
    class DeniedReader:
        def __init__(self, **kwargs):
            raise PermissionError("private journal path")

    monkeypatch.setattr(
        admin_logs.importlib,
        "import_module",
        lambda name: SimpleNamespace(Reader=DeniedReader),
    )
    reader = create_systemd_journal_reader(allowed_routes=frozenset())

    assert isinstance(reader, UnavailableAdminLogReader)
    response = asyncio.run(
        reader.read(
            window_seconds=3600,
            limit=10,
            cursor=None,
            filters=AdminLogFilters(),
        )
    )
    assert response.scope == "unavailable"
    assert response.available is False


def _admin_settings() -> Settings:
    return Settings(
        _env_file=None,
        admin_enabled=True,
        admin_viewer_username="admin-viewer",
        admin_viewer_password="admin-viewer-password",
    )


@pytest.mark.parametrize(
    ("query", "expected_include_system"),
    [
        ("", True),
        ("?include_system=false", False),
        ("?include_system=true", True),
    ],
)
def test_admin_log_api_preserves_default_and_accepts_system_activity_filter(
    query,
    expected_include_system,
):
    captured = []

    class CapturingReader:
        scope = "current_process"

        async def read(self, *, window_seconds, limit, cursor, filters):
            del limit, cursor
            captured.append(filters.include_system)
            return AdminLogsResponse(
                scope=self.scope,
                available=True,
                window_seconds=window_seconds,
                scanned_count=0,
                truncated=False,
                items=[],
            )

    app.dependency_overrides[get_settings] = _admin_settings
    app.dependency_overrides[get_admin_log_reader] = CapturingReader
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/admin-api/v1/logs{query}",
                auth=("admin-viewer", "admin-viewer-password"),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert captured == [expected_include_system]


@pytest.mark.parametrize(
    "query",
    (
        "request_id=contains%20space",
        "route=/raw/patent/CN-SECRET",
        "cursor=not-a-valid-cursor",
        "window_seconds=59",
        "limit=101",
        "code=49999",
    ),
)
def test_admin_log_api_rejects_unbounded_or_unknown_filters(query):
    app.dependency_overrides[get_settings] = _admin_settings
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/admin-api/v1/logs?{query}",
                auth=("admin-viewer", "admin-viewer-password"),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["code"] == 40002
    assert "CN-SECRET" not in response.text


def test_admin_log_audit_does_not_record_filter_value(caplog):
    app.dependency_overrides[get_settings] = _admin_settings
    filter_value = "filter.Request-123"
    caplog.set_level(logging.INFO)
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/admin-api/v1/logs?request_id={filter_value}",
                auth=("admin-viewer", "admin-viewer-password"),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    audit = next(
        record
        for record in reversed(caplog.records)
        if getattr(record, "event", None) == "admin_read_completed"
        and getattr(record, "action", None) == "logs.read"
    )
    assert filter_value not in audit.getMessage()
    assert getattr(audit, "returned_count") == 0
    assert getattr(audit, "window_seconds") == 3600


def test_admin_log_api_accepts_the_existing_unmatched_route_template():
    app.dependency_overrides[get_settings] = _admin_settings
    try:
        with TestClient(app) as client:
            response = client.get(
                "/admin-api/v1/logs?route=__unmatched__",
                auth=("admin-viewer", "admin-viewer-password"),
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200


def test_failed_response_request_id_locates_the_same_completion_event():
    expected_request_id = "issue58.FixedFailure-123"
    app.dependency_overrides[get_settings] = _admin_settings
    try:
        with TestClient(app) as client:
            failed = client.get(
                "/does-not-exist",
                headers={"X-Request-ID": expected_request_id},
            )
            request_id = failed.headers["X-Request-ID"]
            located = client.get(
                f"/admin-api/v1/logs?request_id={request_id}",
                auth=("admin-viewer", "admin-viewer-password"),
            )
    finally:
        app.dependency_overrides.clear()

    assert failed.status_code == 404
    assert request_id == expected_request_id
    assert failed.json()["request_id"] == request_id
    assert located.status_code == 200
    matching = located.json()["items"]
    assert len(matching) == 1
    assert matching[0]["request_id"] == request_id
    assert matching[0]["route"] == "__unmatched__"
    assert matching[0]["status"] == 404
    assert matching[0]["code"] == 40400
