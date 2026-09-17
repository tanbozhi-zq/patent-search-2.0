"""Fixed-path administrator API for single-instance runtime configuration."""

from __future__ import annotations

import asyncio
from hashlib import sha256
import logging
import re
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from app.api.admin_config import (
    get_admin_config_draft_store,
    require_admin_config_drafts,
    validate_admin_config_write_request,
)
from app.core.admin_config import (
    AdminConfigDraftStore,
    AdminConfigStoreBusyError,
    AdminConfigStoreError,
    RuntimeConfigApplyError,
    RuntimeConfigConflictError,
    RuntimeConfigController,
    RuntimeConfigProvider,
    RuntimeConfigRateLimitedError,
    canonical_json,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import ErrorCode, service_error
from app.core.logging import log_event
from app.core.request_context import current_request_id
from app.core.security import AdminPrincipal
from app.schemas.admin import (
    RuntimeConfigApplyRequest,
    RuntimeConfigOperationResponse,
    RuntimeConfigResponse,
    RuntimeConfigRollbackRequest,
)


router = APIRouter(tags=["admin"])
logger = logging.getLogger(__name__)
_APPLY_INTENT = "apply-runtime-config"
_ROLLBACK_INTENT = "rollback-runtime-config"
_IDEMPOTENCY_KEY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{15,127}\Z")


def get_runtime_config_provider(request: Request) -> RuntimeConfigProvider:
    return request.app.state.runtime_config_provider


def get_runtime_config_controller(request: Request) -> RuntimeConfigController:
    return request.app.state.runtime_config_controller


def require_admin_runtime_config(
    principal: AdminPrincipal = Depends(require_admin_config_drafts),
    settings: Settings = Depends(get_settings),
) -> AdminPrincipal:
    if not settings.admin_runtime_config_enabled:
        raise HTTPException(status_code=404)
    return principal


def require_admin_runtime_config_apply(
    request: Request,
    principal: AdminPrincipal = Depends(require_admin_runtime_config),
    x_admin_intent: str | None = Header(default=None, alias="X-Admin-Intent"),
) -> AdminPrincipal:
    validate_admin_config_write_request(
        request,
        x_admin_intent=x_admin_intent,
        expected_intent=_APPLY_INTENT,
    )
    return principal


def require_admin_runtime_config_rollback(
    request: Request,
    principal: AdminPrincipal = Depends(require_admin_runtime_config),
    x_admin_intent: str | None = Header(default=None, alias="X-Admin-Intent"),
) -> AdminPrincipal:
    validate_admin_config_write_request(
        request,
        x_admin_intent=x_admin_intent,
        expected_intent=_ROLLBACK_INTENT,
    )
    return principal


@router.get(
    "/admin-api/v1/runtime-config",
    response_model=RuntimeConfigResponse,
    include_in_schema=False,
)
async def read_runtime_config(
    http_response: Response,
    principal: AdminPrincipal = Depends(require_admin_config_drafts),
    settings: Settings = Depends(get_settings),
    provider: RuntimeConfigProvider = Depends(get_runtime_config_provider),
    store: AdminConfigDraftStore = Depends(get_admin_config_draft_store),
) -> RuntimeConfigResponse:
    _no_store(http_response)
    try:
        response = await _runtime_response(
            provider=provider,
            store=store,
            writes_enabled=settings.admin_runtime_config_enabled,
        )
    except AdminConfigStoreBusyError:
        _audit(principal, action="runtime_config.read", result="busy")
        raise service_error(ErrorCode.SERVICE_BUSY)
    except AdminConfigStoreError:
        _store_failure(principal, action="runtime_config.read")
        raise service_error(ErrorCode.INTERNAL_ERROR)
    _audit(principal, action="runtime_config.read", result="ok")
    return response


@router.post(
    "/admin-api/v1/runtime-config/apply",
    response_model=RuntimeConfigResponse,
    include_in_schema=False,
)
async def apply_runtime_config(
    payload: RuntimeConfigApplyRequest,
    http_response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: AdminPrincipal = Depends(require_admin_runtime_config_apply),
    settings: Settings = Depends(get_settings),
    provider: RuntimeConfigProvider = Depends(get_runtime_config_provider),
    controller: RuntimeConfigController = Depends(get_runtime_config_controller),
    store: AdminConfigDraftStore = Depends(get_admin_config_draft_store),
) -> RuntimeConfigResponse:
    _no_store(http_response)
    try:
        UUID(payload.draft_id)
        key_hash = _idempotency_hash(idempotency_key)
    except (ValueError, TypeError):
        _audit(principal, action="runtime_config.apply", result="rejected")
        raise service_error(ErrorCode.INVALID_REQUEST)
    request_fingerprint = _request_fingerprint(
        kind="apply",
        draft_id=payload.draft_id,
        expected_version=payload.expected_version,
    )
    operation = None
    try:
        operation = await controller.apply(
            draft_id=payload.draft_id,
            expected_version=payload.expected_version,
            idempotency_key_hash=key_hash,
            request_fingerprint=request_fingerprint,
            actor=principal.subject,
        )
        response = await _runtime_response(
            provider=provider,
            store=store,
            writes_enabled=settings.admin_runtime_config_enabled,
        )
    except asyncio.CancelledError as exc:
        _audit(
            principal,
            action="runtime_config.apply",
            result=getattr(exc, "result", None)
            or (operation.status if operation is not None else "cancelled"),
            operation_id=getattr(exc, "operation_id", None)
            or (operation.operation_id if operation is not None else None),
            draft_id=_correlated_draft_id(
                exc,
                operation=operation,
                fallback=payload.draft_id,
            ),
        )
        raise
    except RuntimeConfigConflictError as exc:
        _audit(
            principal,
            action="runtime_config.apply",
            result="conflict",
            operation_id=exc.operation_id,
            draft_id=_correlated_draft_id(exc, fallback=payload.draft_id),
        )
        raise service_error(ErrorCode.CONFIG_BASELINE_CONFLICT)
    except RuntimeConfigRateLimitedError as exc:
        _audit(
            principal,
            action="runtime_config.apply",
            result="rate_limited",
            operation_id=exc.operation_id,
            draft_id=_correlated_draft_id(exc, fallback=payload.draft_id),
        )
        raise service_error(ErrorCode.RATE_LIMITED)
    except RuntimeConfigApplyError as exc:
        _raise_runtime_failure(principal, action="runtime_config.apply", error=exc)
    except AdminConfigStoreBusyError:
        _audit(
            principal,
            action="runtime_config.apply",
            result="busy",
            operation_id=(operation.operation_id if operation is not None else None),
            draft_id=(operation.draft_id if operation is not None else payload.draft_id),
        )
        raise service_error(ErrorCode.SERVICE_BUSY)
    except AdminConfigStoreError:
        _store_failure(
            principal,
            action="runtime_config.apply",
            operation_id=(operation.operation_id if operation is not None else None),
            draft_id=(operation.draft_id if operation is not None else payload.draft_id),
        )
        raise service_error(ErrorCode.INTERNAL_ERROR)
    _audit(
        principal,
        action="runtime_config.apply",
        result=operation.status,
        operation_id=operation.operation_id,
        draft_id=payload.draft_id,
    )
    return response


@router.post(
    "/admin-api/v1/runtime-config/rollback",
    response_model=RuntimeConfigResponse,
    include_in_schema=False,
)
async def rollback_runtime_config(
    payload: RuntimeConfigRollbackRequest,
    http_response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    principal: AdminPrincipal = Depends(require_admin_runtime_config_rollback),
    settings: Settings = Depends(get_settings),
    provider: RuntimeConfigProvider = Depends(get_runtime_config_provider),
    controller: RuntimeConfigController = Depends(get_runtime_config_controller),
    store: AdminConfigDraftStore = Depends(get_admin_config_draft_store),
) -> RuntimeConfigResponse:
    _no_store(http_response)
    try:
        key_hash = _idempotency_hash(idempotency_key)
    except (ValueError, TypeError):
        _audit(principal, action="runtime_config.rollback", result="rejected")
        raise service_error(ErrorCode.INVALID_REQUEST)
    request_fingerprint = _request_fingerprint(
        kind="rollback",
        expected_version=payload.expected_version,
        target_version=payload.target_version,
        reason=payload.reason,
    )
    operation = None
    try:
        operation = await controller.rollback(
            expected_version=payload.expected_version,
            target_version=payload.target_version,
            reason=payload.reason,
            idempotency_key_hash=key_hash,
            request_fingerprint=request_fingerprint,
            actor=principal.subject,
        )
        response = await _runtime_response(
            provider=provider,
            store=store,
            writes_enabled=settings.admin_runtime_config_enabled,
        )
    except asyncio.CancelledError as exc:
        _audit(
            principal,
            action="runtime_config.rollback",
            result=getattr(exc, "result", None)
            or (operation.status if operation is not None else "cancelled"),
            operation_id=getattr(exc, "operation_id", None)
            or (operation.operation_id if operation is not None else None),
            draft_id=_correlated_draft_id(exc, operation=operation),
        )
        raise
    except RuntimeConfigConflictError as exc:
        _audit(
            principal,
            action="runtime_config.rollback",
            result="conflict",
            operation_id=exc.operation_id,
            draft_id=exc.draft_id,
        )
        raise service_error(ErrorCode.CONFIG_BASELINE_CONFLICT)
    except RuntimeConfigRateLimitedError as exc:
        _audit(
            principal,
            action="runtime_config.rollback",
            result="rate_limited",
            operation_id=exc.operation_id,
            draft_id=exc.draft_id,
        )
        raise service_error(ErrorCode.RATE_LIMITED)
    except RuntimeConfigApplyError as exc:
        _raise_runtime_failure(principal, action="runtime_config.rollback", error=exc)
    except AdminConfigStoreBusyError:
        _audit(
            principal,
            action="runtime_config.rollback",
            result="busy",
            operation_id=(operation.operation_id if operation is not None else None),
            draft_id=(operation.draft_id if operation is not None else None),
        )
        raise service_error(ErrorCode.SERVICE_BUSY)
    except AdminConfigStoreError:
        _store_failure(
            principal,
            action="runtime_config.rollback",
            operation_id=(operation.operation_id if operation is not None else None),
            draft_id=(operation.draft_id if operation is not None else None),
        )
        raise service_error(ErrorCode.INTERNAL_ERROR)
    _audit(
        principal,
        action="runtime_config.rollback",
        result=operation.status,
        operation_id=operation.operation_id,
    )
    return response


async def _runtime_response(
    *,
    provider: RuntimeConfigProvider,
    store: AdminConfigDraftStore,
    writes_enabled: bool,
) -> RuntimeConfigResponse:
    snapshot = provider.snapshot()
    previous = provider.previous_snapshot()
    operations = await run_in_threadpool(store.list_runtime_operations, limit=20)
    return RuntimeConfigResponse(
        version=snapshot.version,
        fingerprint=snapshot.fingerprint,
        source=snapshot.source,
        created_at=snapshot.created_at,
        rollback_version=previous.version if previous is not None else None,
        writes_enabled=writes_enabled,
        values=snapshot.as_dict(),
        recent_operations=[_operation_response(operation) for operation in operations],
    )


def _operation_response(operation) -> RuntimeConfigOperationResponse:
    return RuntimeConfigOperationResponse(
        operation_id=operation.operation_id,
        kind=operation.kind,
        status=operation.status,
        created_at=operation.created_at,
        actor=operation.actor,
        reason=operation.reason,
        expected_version=operation.expected_version,
        previous_version=operation.previous_version,
        current_version=operation.current_version,
        draft_id=operation.draft_id,
        failure_code=operation.failure_code,
    )


def _idempotency_hash(value: str | None) -> str:
    if value is None or _IDEMPOTENCY_KEY_PATTERN.fullmatch(value) is None:
        raise ValueError("invalid idempotency key")
    return sha256(value.encode("utf-8")).hexdigest()


def _request_fingerprint(**fields: str) -> str:
    return sha256(canonical_json(fields).encode("utf-8")).hexdigest()


def _correlated_draft_id(
    error: BaseException,
    *,
    operation=None,
    fallback: str | None = None,
) -> str | None:
    """Prefer durable correlation; retain the request draft before reservation."""

    error_operation_id = getattr(error, "operation_id", None)
    error_draft_id = getattr(error, "draft_id", None)
    if error_operation_id is not None or error_draft_id is not None:
        return error_draft_id
    if operation is not None:
        return operation.draft_id
    return fallback


def _raise_runtime_failure(
    principal: AdminPrincipal,
    *,
    action: str,
    error: RuntimeConfigApplyError,
) -> None:
    correlation = {
        "operation_id": error.operation_id,
        "draft_id": error.draft_id,
    }
    if error.failure_code == "version_conflict":
        _audit(principal, action=action, result="conflict", **correlation)
        raise service_error(ErrorCode.CONFIG_BASELINE_CONFLICT)
    if error.failure_code == "rate_limited":
        _audit(principal, action=action, result="rate_limited", **correlation)
        raise service_error(ErrorCode.RATE_LIMITED)
    if error.failure_code == "audit_store_failed":
        _store_failure(principal, action=action, **correlation)
        raise service_error(ErrorCode.INTERNAL_ERROR)
    if error.failure_code == "audit_store_busy":
        _audit(principal, action=action, result="busy", **correlation)
        raise service_error(ErrorCode.SERVICE_BUSY)
    if error.failure_code in {
        "draft_not_found",
        "draft_not_validated",
        "unsupported_apply_mode",
    }:
        _audit(principal, action=action, result="rejected", **correlation)
        raise service_error(ErrorCode.INVALID_REQUEST)
    _audit(principal, action=action, result="failed", **correlation)
    raise service_error(ErrorCode.INTERNAL_ERROR)


def _audit(
    principal: AdminPrincipal,
    *,
    action: str,
    result: str,
    operation_id: str | None = None,
    draft_id: str | None = None,
) -> None:
    log_event(
        logger,
        logging.INFO,
        "admin_runtime_config_completed",
        request_id=current_request_id(),
        actor=principal.subject,
        role=principal.role,
        action=action,
        result=result,
        operation_id=operation_id,
        draft_id=draft_id,
    )


def _store_failure(
    principal: AdminPrincipal,
    *,
    action: str,
    operation_id: str | None = None,
    draft_id: str | None = None,
) -> None:
    log_event(
        logger,
        logging.ERROR,
        "admin_runtime_config_store_failed",
        request_id=current_request_id(),
        actor=principal.subject,
        role=principal.role,
        action=action,
        result="failed",
        operation_id=operation_id,
        draft_id=draft_id,
    )


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
