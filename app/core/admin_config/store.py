"""Private SQLite persistence for immutable drafts and runtime audit events."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat
from threading import Lock
from typing import Callable, Mapping
from uuid import uuid4

from app.core.admin_config.fingerprint import (
    baseline_fingerprint,
    canonical_json,
    format_datetime,
)
from app.core.admin_config.models import (
    AdminConfigDraft,
    AdminConfigStoreBusyError,
    AdminConfigStoreError,
    ConfigDiffValue,
    ConfigValidationIssue,
    ConfigValue,
    RuntimeConfigOperation,
    RuntimeConfigConflictError,
    RuntimeConfigSnapshot,
    UnknownConfigKeyError,
)
from app.core.admin_config.registry import (
    ADMIN_CONFIG_DRAFT_TTL_SECONDS,
    ADMIN_CONFIG_REGISTRY_VERSION,
    current_config_values,
)
from app.core.admin_config.validation import validate_config_candidate
from app.core.config import Settings
from app.version import __version__


_DATABASE_SCHEMA_VERSION = 2


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AdminConfigDraftStore:
    """SQLite store shared by the administrator control-plane only.

    Every persistent record is append-only. Runtime values are never loaded from
    this database during startup, so a process restart intentionally returns to
    the deployment baseline rather than replaying an old override.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        busy_timeout_seconds: float = 1.0,
        clock: Callable[[], datetime] = _utc_now,
    ):
        self.database_path = Path(database_path)
        self.busy_timeout_seconds = busy_timeout_seconds
        self._clock = clock
        self._initialized = False
        self._initialization_lock = Lock()

    def create(
        self,
        *,
        settings: Settings,
        operator: str,
        reason: str,
        candidate_values: Mapping[str, object],
        current_values: Mapping[str, ConfigValue] | None = None,
        current_fingerprint: str | None = None,
    ) -> AdminConfigDraft:
        values = (
            dict(current_values)
            if current_values is not None
            else current_config_values(settings)
        )
        validation = validate_config_candidate(
            settings,
            candidate_values,
            current_values=values,
        )
        now = self._now()
        created_at = now.replace(microsecond=(now.microsecond // 1000) * 1000)
        draft = AdminConfigDraft(
            draft_id=str(uuid4()),
            created_at=created_at,
            expires_at=created_at + timedelta(seconds=ADMIN_CONFIG_DRAFT_TTL_SECONDS),
            operator=operator,
            reason=reason,
            registry_version=ADMIN_CONFIG_REGISTRY_VERSION,
            service_version=__version__,
            release_commit=settings.service_release_commit,
            baseline_fingerprint=current_fingerprint
            or baseline_fingerprint(settings, values=values),
            validation_status=validation.status,
            status=validation.status,
            candidate_values=validation.candidate_values,
            diff=validation.diff,
            validation_errors=validation.errors,
        )
        self._ensure_schema()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO config_change_drafts (
                        draft_id, created_at, expires_at, operator, reason,
                        registry_version, service_version, release_commit,
                        baseline_fingerprint, baseline_values_json,
                        candidate_values_json, diff_json, validation_status,
                        validation_result_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        draft.draft_id,
                        format_datetime(draft.created_at),
                        format_datetime(draft.expires_at),
                        draft.operator,
                        draft.reason,
                        draft.registry_version,
                        draft.service_version,
                        draft.release_commit,
                        draft.baseline_fingerprint,
                        canonical_json(values),
                        canonical_json(draft.candidate_values),
                        canonical_json(_serialized_diff(draft.diff)),
                        draft.validation_status,
                        canonical_json(_serialized_errors(draft.validation_errors)),
                    ),
                )
                connection.commit()
        except sqlite3.OperationalError as exc:
            raise _translate_store_error(exc) from exc
        except sqlite3.DatabaseError as exc:
            raise AdminConfigStoreError("configuration draft database failed") from exc
        return draft

    def list(
        self,
        *,
        settings: Settings,
        limit: int = 50,
        current_fingerprint: str | None = None,
    ) -> list[AdminConfigDraft]:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT draft_id, created_at, expires_at, operator, reason,
                           registry_version, service_version, release_commit,
                           baseline_fingerprint, candidate_values_json, diff_json,
                           validation_status, validation_result_json
                    FROM config_change_drafts
                    ORDER BY created_at DESC, draft_id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        except sqlite3.OperationalError as exc:
            raise _translate_store_error(exc) from exc
        except sqlite3.DatabaseError as exc:
            raise AdminConfigStoreError("configuration draft database failed") from exc

        fingerprint = current_fingerprint or baseline_fingerprint(settings)
        now = self._now()
        return [
            _draft_from_row(row, current_fingerprint=fingerprint, now=now)
            for row in rows
        ]

    def get(
        self,
        *,
        settings: Settings,
        draft_id: str,
        current_fingerprint: str | None = None,
    ) -> AdminConfigDraft | None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT draft_id, created_at, expires_at, operator, reason,
                           registry_version, service_version, release_commit,
                           baseline_fingerprint, candidate_values_json, diff_json,
                           validation_status, validation_result_json
                    FROM config_change_drafts
                    WHERE draft_id = ?
                    """,
                    (draft_id,),
                ).fetchone()
        except sqlite3.OperationalError as exc:
            raise _translate_store_error(exc) from exc
        except sqlite3.DatabaseError as exc:
            raise AdminConfigStoreError("configuration draft database failed") from exc
        if row is None:
            return None
        return _draft_from_row(
            row,
            current_fingerprint=current_fingerprint or baseline_fingerprint(settings),
            now=self._now(),
        )

    def reserve_runtime_operation(
        self,
        *,
        idempotency_key_hash: str,
        request_fingerprint: str,
        kind: str,
        actor: str,
        reason: str,
        expected_version: str,
        draft_id: str | None,
    ) -> tuple[RuntimeConfigOperation, bool]:
        """Reserve one request key or return its previously recorded outcome."""

        self._ensure_schema()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    """
                    SELECT operation_id, request_fingerprint
                    FROM config_runtime_operation_requests
                    WHERE idempotency_key_hash = ?
                    """,
                    (idempotency_key_hash,),
                ).fetchone()
                if existing is not None:
                    operation = self._runtime_operation_from_connection(
                        connection,
                        str(existing["operation_id"]),
                    )
                    if operation is None:
                        raise AdminConfigStoreError("runtime operation audit is incomplete")
                    if str(existing["request_fingerprint"]) != request_fingerprint:
                        raise RuntimeConfigConflictError(
                            "runtime idempotency key conflicts",
                            operation_id=operation.operation_id,
                            draft_id=operation.draft_id,
                        )
                    connection.commit()
                    return operation, False

                operation_id = str(uuid4())
                now = self._now()
                created_at = now.replace(microsecond=(now.microsecond // 1000) * 1000)
                connection.execute(
                    """
                    INSERT INTO config_runtime_operation_requests (
                        operation_id, idempotency_key_hash, request_fingerprint,
                        kind, created_at, actor, reason, expected_version, draft_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        operation_id,
                        idempotency_key_hash,
                        request_fingerprint,
                        kind,
                        format_datetime(created_at),
                        actor,
                        reason,
                        expected_version,
                        draft_id,
                    ),
                )
                self._insert_runtime_event(
                    connection,
                    operation_id=operation_id,
                    event_type="started",
                    previous_version=None,
                    current_version=None,
                    failure_code=None,
                )
                connection.commit()
        except sqlite3.OperationalError as exc:
            raise _translate_store_error(exc) from exc
        except sqlite3.DatabaseError as exc:
            raise AdminConfigStoreError("runtime configuration audit database failed") from exc
        return RuntimeConfigOperation(
            operation_id=operation_id,
            kind="apply" if kind == "apply" else "rollback",
            status="incomplete",
            created_at=created_at,
            actor=actor,
            reason=reason,
            expected_version=expected_version,
            previous_version=None,
            current_version=None,
            draft_id=draft_id,
        ), True

    def find_runtime_operation(
        self,
        *,
        idempotency_key_hash: str,
        request_fingerprint: str,
    ) -> RuntimeConfigOperation | None:
        """Read an existing idempotency result without creating audit rows."""

        self._ensure_schema()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT operation_id, request_fingerprint
                    FROM config_runtime_operation_requests
                    WHERE idempotency_key_hash = ?
                    """,
                    (idempotency_key_hash,),
                ).fetchone()
                if row is None:
                    return None
                operation = self._runtime_operation_from_connection(
                    connection,
                    str(row["operation_id"]),
                )
                if operation is None:
                    raise AdminConfigStoreError("runtime operation audit is incomplete")
                if str(row["request_fingerprint"]) != request_fingerprint:
                    raise RuntimeConfigConflictError(
                        "runtime idempotency key conflicts",
                        operation_id=operation.operation_id,
                        draft_id=operation.draft_id,
                    )
                return operation
        except sqlite3.OperationalError as exc:
            raise _translate_store_error(exc) from exc
        except sqlite3.DatabaseError as exc:
            raise AdminConfigStoreError("runtime configuration audit database failed") from exc

    def record_runtime_version(self, snapshot: RuntimeConfigSnapshot) -> None:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT OR IGNORE INTO config_runtime_versions (
                        version, created_at, source, parent_version, values_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.version,
                        format_datetime(snapshot.created_at),
                        snapshot.source,
                        snapshot.parent_version,
                        canonical_json(snapshot.as_dict()),
                    ),
                )
                connection.commit()
        except sqlite3.OperationalError as exc:
            raise _translate_store_error(exc) from exc
        except sqlite3.DatabaseError as exc:
            raise AdminConfigStoreError("runtime configuration audit database failed") from exc

    def append_runtime_event(
        self,
        *,
        operation_id: str,
        event_type: str,
        previous_version: str | None,
        current_version: str | None,
        failure_code: str | None = None,
    ) -> RuntimeConfigOperation:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._insert_runtime_event(
                    connection,
                    operation_id=operation_id,
                    event_type=event_type,
                    previous_version=previous_version,
                    current_version=current_version,
                    failure_code=failure_code,
                )
                operation = self._runtime_operation_from_connection(connection, operation_id)
                connection.commit()
        except sqlite3.OperationalError as exc:
            raise _translate_store_error(exc) from exc
        except sqlite3.DatabaseError as exc:
            raise AdminConfigStoreError("runtime configuration audit database failed") from exc
        if operation is None:
            raise AdminConfigStoreError("runtime operation audit is incomplete")
        return operation

    def list_runtime_operations(self, *, limit: int = 20) -> list[RuntimeConfigOperation]:
        self._ensure_schema()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT operation_id
                    FROM config_runtime_operation_requests
                    ORDER BY created_at DESC, operation_id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                return [
                    operation
                    for row in rows
                    if (operation := self._runtime_operation_from_connection(
                        connection, str(row["operation_id"])
                    )) is not None
                ]
        except sqlite3.OperationalError as exc:
            raise _translate_store_error(exc) from exc
        except sqlite3.DatabaseError as exc:
            raise AdminConfigStoreError("runtime configuration audit database failed") from exc

    def _runtime_operation_from_connection(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
    ) -> RuntimeConfigOperation | None:
        request = connection.execute(
            """
            SELECT operation_id, kind, created_at, actor, reason, expected_version, draft_id
            FROM config_runtime_operation_requests
            WHERE operation_id = ?
            """,
            (operation_id,),
        ).fetchone()
        if request is None:
            return None
        event = connection.execute(
            """
            SELECT event_type, previous_version, current_version, failure_code
            FROM config_runtime_operation_events
            WHERE operation_id = ?
            ORDER BY event_sequence DESC
            LIMIT 1
            """,
            (operation_id,),
        ).fetchone()
        event_type = str(event["event_type"]) if event is not None else "started"
        status = {
            "applied": "applied",
            "rolled_back": "rolled_back",
            "failed": "failed",
        }.get(event_type, "incomplete")
        return RuntimeConfigOperation(
            operation_id=str(request["operation_id"]),
            kind="apply" if str(request["kind"]) == "apply" else "rollback",
            status=status,  # type: ignore[arg-type]
            created_at=_parse_datetime(str(request["created_at"])),
            actor=str(request["actor"]),
            reason=str(request["reason"]),
            expected_version=str(request["expected_version"]),
            previous_version=(str(event["previous_version"]) if event and event["previous_version"] else None),
            current_version=(str(event["current_version"]) if event and event["current_version"] else None),
            draft_id=(str(request["draft_id"]) if request["draft_id"] else None),
            failure_code=(str(event["failure_code"]) if event and event["failure_code"] else None),
        )

    def _insert_runtime_event(
        self,
        connection: sqlite3.Connection,
        *,
        operation_id: str,
        event_type: str,
        previous_version: str | None,
        current_version: str | None,
        failure_code: str | None,
    ) -> None:
        current_time = self._now()
        now = current_time.replace(microsecond=(current_time.microsecond // 1000) * 1000)
        connection.execute(
            """
            INSERT INTO config_runtime_operation_events (
                event_id, operation_id, event_type, created_at, previous_version,
                current_version, failure_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                operation_id,
                event_type,
                format_datetime(now),
                previous_version,
                current_version,
                failure_code,
            ),
        )

    def _ensure_schema(self) -> None:
        if self._initialized:
            return
        with self._initialization_lock:
            if self._initialized:
                return
            try:
                self.database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                _validate_state_directory(self.database_path.parent)
                _prepare_database_file(self.database_path)
                with self._connect() as connection:
                    schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
                    if schema_version not in (0, 1, _DATABASE_SCHEMA_VERSION):
                        raise AdminConfigStoreError(
                            "configuration draft database schema is unsupported"
                        )
                    connection.execute("PRAGMA journal_mode = WAL")
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS config_change_drafts (
                            draft_id TEXT PRIMARY KEY,
                            created_at TEXT NOT NULL,
                            expires_at TEXT NOT NULL,
                            operator TEXT NOT NULL,
                            reason TEXT NOT NULL,
                            registry_version TEXT NOT NULL,
                            service_version TEXT NOT NULL,
                            release_commit TEXT NOT NULL,
                            baseline_fingerprint TEXT NOT NULL,
                            baseline_values_json TEXT NOT NULL,
                            candidate_values_json TEXT NOT NULL,
                            diff_json TEXT NOT NULL,
                            validation_status TEXT NOT NULL
                                CHECK (validation_status IN ('validated', 'invalid')),
                            validation_result_json TEXT NOT NULL
                        );
                        CREATE INDEX IF NOT EXISTS idx_config_change_drafts_created
                        ON config_change_drafts (created_at DESC, draft_id DESC);
                        CREATE TRIGGER IF NOT EXISTS config_change_drafts_no_update
                        BEFORE UPDATE ON config_change_drafts
                        BEGIN SELECT RAISE(ABORT, 'config drafts are immutable'); END;
                        CREATE TRIGGER IF NOT EXISTS config_change_drafts_no_delete
                        BEFORE DELETE ON config_change_drafts
                        BEGIN SELECT RAISE(ABORT, 'config drafts are immutable'); END;

                        CREATE TABLE IF NOT EXISTS config_runtime_versions (
                            version TEXT PRIMARY KEY,
                            created_at TEXT NOT NULL,
                            source TEXT NOT NULL
                                CHECK (source IN ('deployment_baseline', 'runtime_override')),
                            parent_version TEXT,
                            values_json TEXT NOT NULL
                        );
                        CREATE TRIGGER IF NOT EXISTS config_runtime_versions_no_update
                        BEFORE UPDATE ON config_runtime_versions
                        BEGIN SELECT RAISE(ABORT, 'runtime versions are immutable'); END;
                        CREATE TRIGGER IF NOT EXISTS config_runtime_versions_no_delete
                        BEFORE DELETE ON config_runtime_versions
                        BEGIN SELECT RAISE(ABORT, 'runtime versions are immutable'); END;

                        CREATE TABLE IF NOT EXISTS config_runtime_operation_requests (
                            operation_id TEXT PRIMARY KEY,
                            idempotency_key_hash TEXT NOT NULL UNIQUE,
                            request_fingerprint TEXT NOT NULL,
                            kind TEXT NOT NULL CHECK (kind IN ('apply', 'rollback')),
                            created_at TEXT NOT NULL,
                            actor TEXT NOT NULL,
                            reason TEXT NOT NULL,
                            expected_version TEXT NOT NULL,
                            draft_id TEXT
                        );
                        CREATE INDEX IF NOT EXISTS idx_config_runtime_operations_created
                        ON config_runtime_operation_requests (created_at DESC, operation_id DESC);
                        CREATE TRIGGER IF NOT EXISTS config_runtime_operation_requests_no_update
                        BEFORE UPDATE ON config_runtime_operation_requests
                        BEGIN SELECT RAISE(ABORT, 'runtime operation requests are immutable'); END;
                        CREATE TRIGGER IF NOT EXISTS config_runtime_operation_requests_no_delete
                        BEFORE DELETE ON config_runtime_operation_requests
                        BEGIN SELECT RAISE(ABORT, 'runtime operation requests are immutable'); END;

                        CREATE TABLE IF NOT EXISTS config_runtime_operation_events (
                            event_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                            event_id TEXT NOT NULL UNIQUE,
                            operation_id TEXT NOT NULL
                                REFERENCES config_runtime_operation_requests(operation_id),
                            event_type TEXT NOT NULL
                                CHECK (event_type IN ('started', 'applied', 'rolled_back', 'failed')),
                            created_at TEXT NOT NULL,
                            previous_version TEXT,
                            current_version TEXT,
                            failure_code TEXT
                        );
                        CREATE INDEX IF NOT EXISTS idx_config_runtime_operation_events
                        ON config_runtime_operation_events (operation_id, event_sequence DESC);
                        CREATE TRIGGER IF NOT EXISTS config_runtime_operation_events_no_update
                        BEFORE UPDATE ON config_runtime_operation_events
                        BEGIN SELECT RAISE(ABORT, 'runtime operation events are immutable'); END;
                        CREATE TRIGGER IF NOT EXISTS config_runtime_operation_events_no_delete
                        BEFORE DELETE ON config_runtime_operation_events
                        BEGIN SELECT RAISE(ABORT, 'runtime operation events are immutable'); END;

                        PRAGMA user_version = 2;
                        """
                    )
            except sqlite3.OperationalError as exc:
                raise _translate_store_error(exc) from exc
            except (OSError, sqlite3.DatabaseError) as exc:
                raise AdminConfigStoreError("configuration draft database failed") from exc
            self._initialized = True

    def _connect(self) -> sqlite3.Connection:
        try:
            _validate_state_directory(self.database_path.parent)
            _prepare_database_file(
                self.database_path,
                create_if_missing=not self._initialized,
            )
        except AdminConfigStoreError:
            raise
        except OSError as exc:
            raise AdminConfigStoreError("configuration draft database failed") from exc

        connection = sqlite3.connect(
            self.database_path,
            timeout=self.busy_timeout_seconds,
            isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute(
                "PRAGMA busy_timeout = "
                f"{max(1, int(self.busy_timeout_seconds * 1000))}"
            )
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA foreign_keys = ON")
        except BaseException:
            connection.close()
            raise
        return connection

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise AdminConfigStoreError("configuration draft clock must be timezone-aware")
        return value.astimezone(timezone.utc)


def _validate_state_directory(path: Path) -> None:
    metadata = os.lstat(path)
    if not stat.S_ISDIR(metadata.st_mode):
        raise AdminConfigStoreError(
            "configuration draft state directory must not be a symbolic link"
        )
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise AdminConfigStoreError(
            "configuration draft state directory permissions are too broad"
        )


def _prepare_database_file(
    path: Path,
    *,
    create_if_missing: bool = True,
) -> None:
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    for _attempt in range(2):
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            if not create_if_missing:
                raise AdminConfigStoreError(
                    "configuration draft database disappeared after initialization"
                )
            try:
                descriptor = os.open(
                    path,
                    os.O_CREAT | os.O_EXCL | os.O_RDWR | no_follow,
                    0o600,
                )
            except FileExistsError:
                continue
        else:
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise AdminConfigStoreError(
                    "configuration draft database must be a regular private file"
                )
            descriptor = os.open(path, os.O_RDWR | no_follow)
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                raise AdminConfigStoreError(
                    "configuration draft database must be a regular private file"
                )
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        return
    raise AdminConfigStoreError("configuration draft database path changed during setup")


def _translate_store_error(exc: sqlite3.OperationalError) -> AdminConfigStoreError:
    message = str(exc).lower()
    if "locked" in message or "busy" in message:
        return AdminConfigStoreBusyError("configuration draft database is busy")
    return AdminConfigStoreError("configuration draft database failed")


def _draft_from_row(
    row: sqlite3.Row,
    *,
    current_fingerprint: str,
    now: datetime,
) -> AdminConfigDraft:
    validation_status = str(row["validation_status"])
    stored_fingerprint = str(row["baseline_fingerprint"])
    expires_at = _parse_datetime(str(row["expires_at"]))
    status = (
        "expired"
        if stored_fingerprint != current_fingerprint or now >= expires_at
        else validation_status
    )
    diff_payload = json.loads(row["diff_json"])
    errors_payload = json.loads(row["validation_result_json"])
    return AdminConfigDraft(
        draft_id=str(row["draft_id"]),
        created_at=_parse_datetime(str(row["created_at"])),
        expires_at=expires_at,
        operator=str(row["operator"]),
        reason=str(row["reason"]),
        registry_version=str(row["registry_version"]),
        service_version=str(row["service_version"]),
        release_commit=str(row["release_commit"]),
        baseline_fingerprint=stored_fingerprint,
        validation_status=validation_status,
        status=status,
        candidate_values=json.loads(row["candidate_values_json"]),
        diff={
            key: ConfigDiffValue(
                old=value["old"], new=value["new"], apply_mode=value["apply_mode"]
            )
            for key, value in diff_payload.items()
        },
        validation_errors=tuple(
            ConfigValidationIssue(
                code=value["code"], key=value.get("key"), message=value["message"]
            )
            for value in errors_payload
        ),
    )


def _serialized_diff(diff: Mapping[str, ConfigDiffValue]) -> dict[str, dict[str, object]]:
    return {
        key: {"old": value.old, "new": value.new, "apply_mode": value.apply_mode}
        for key, value in diff.items()
    }


def _serialized_errors(
    errors: tuple[ConfigValidationIssue, ...],
) -> list[dict[str, str | None]]:
    return [
        {"code": error.code, "key": error.key, "message": error.message}
        for error in errors
    ]


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
