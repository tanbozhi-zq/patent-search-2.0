"""管理配置草稿的受控读写 API。

本模块只负责 schema、草稿创建、读取与导出，以及浏览器写请求的同源/意图校验；
它不会直接把草稿应用到运行时，实际热更新由独立的 runtime-config 路由和控制器处理。
"""

from functools import lru_cache
import logging
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response

from app.core.admin_config import (
    ADMIN_CONFIG_APPLY_MODE_CONTRACT,
    ADMIN_CONFIG_REGISTRY_VERSION,
    ADMIN_CONFIG_DRAFT_TTL_SECONDS,
    CONFIG_PARAMETER_REGISTRY,
    AdminConfigDraft,
    AdminConfigDraftStore,
    AdminConfigStoreBusyError,
    AdminConfigStoreError,
    RuntimeConfigProvider,
    UnknownConfigKeyError,
    baseline_fingerprint,
    current_config_values,
    runtime_snapshot_from_settings,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import ErrorCode, service_error
from app.core.logging import log_event
from app.core.request_context import current_request_id
from app.core.security import AdminPrincipal, require_admin
from app.schemas.admin import (
    AdminConfigDefinition,
    AdminConfigDiffItem,
    AdminConfigDraftCreateRequest,
    AdminConfigDraftExportResponse,
    AdminConfigDraftListResponse,
    AdminConfigDraftResponse,
    AdminConfigSchemaResponse,
    AdminConfigValidationError,
)
from app.version import __version__


router = APIRouter(tags=["admin"])
logger = logging.getLogger(__name__)
_ADMIN_CONFIG_INTENT = "create-config-draft"


def require_admin_config_drafts(
    principal: AdminPrincipal = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> AdminPrincipal:
    if not settings.admin_config_drafts_enabled:
        raise HTTPException(status_code=404)
    return principal


def require_admin_config_write(
    request: Request,
    principal: AdminPrincipal = Depends(require_admin_config_drafts),
    x_admin_intent: str | None = Header(default=None, alias="X-Admin-Intent"),
) -> AdminPrincipal:
    validate_admin_config_write_request(
        request,
        x_admin_intent=x_admin_intent,
        expected_intent=_ADMIN_CONFIG_INTENT,
    )
    return principal


def validate_admin_config_write_request(
    request: Request,
    *,
    x_admin_intent: str | None,
    expected_intent: str,
) -> None:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if content_type.lower() != "application/json" or x_admin_intent != expected_intent:
        raise service_error(ErrorCode.INVALID_REQUEST)

    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site not in {"same-origin", "same-site", "none"}:
        raise service_error(ErrorCode.INVALID_REQUEST)

    origin = request.headers.get("origin")
    if origin is not None:
        parsed = urlsplit(origin)
        if parsed.scheme != request.url.scheme or parsed.netloc != request.url.netloc:
            raise service_error(ErrorCode.INVALID_REQUEST)


@lru_cache(maxsize=8)
def _draft_store(database_path: str) -> AdminConfigDraftStore:
    return AdminConfigDraftStore(database_path)


def get_admin_config_draft_store(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> AdminConfigDraftStore:
    active_store = getattr(request.app.state, "admin_config_store", None)
    if isinstance(active_store, AdminConfigDraftStore) and (
        str(active_store.database_path) == settings.admin_config_database_path
    ):
        return active_store
    return _draft_store(settings.admin_config_database_path)


def get_runtime_config_provider(request: Request) -> RuntimeConfigProvider:
    return request.app.state.runtime_config_provider


def _snapshot_for_settings(
    *,
    settings: Settings,
    runtime_config: RuntimeConfigProvider,
):
    """Return the live snapshot, with a narrow dependency-injection fallback.

    The deployed process constructs both ``Settings`` and the provider during
    lifespan, so they share one deployment baseline. Test applications (and
    explicit dependency overrides) can substitute only ``get_settings``. When
    that happens before any runtime override, retain #59's isolated draft
    semantics instead of treating the test-only mismatch as a live CAS change.
    A real runtime override is never replaced by this fallback.
    """

    snapshot, _previous = _snapshot_pair_for_settings(
        settings=settings,
        runtime_config=runtime_config,
    )
    return snapshot


def _snapshot_pair_for_settings(
    *,
    settings: Settings,
    runtime_config: RuntimeConfigProvider,
):
    """Return an atomic current/rollback pair with the test override fallback."""

    snapshot, previous = runtime_config.snapshots()
    if (
        snapshot.source == "deployment_baseline"
        and snapshot.fingerprint != baseline_fingerprint(settings)
    ):
        fallback = runtime_snapshot_from_settings(settings)
        return fallback, fallback
    return snapshot, previous or snapshot


@router.get(
    "/admin-api/v1/config-schema",
    response_model=AdminConfigSchemaResponse,
    include_in_schema=False,
)
def admin_config_schema(
    request: Request,
    http_response: Response,
    principal: AdminPrincipal = Depends(require_admin_config_drafts),
    settings: Settings = Depends(get_settings),
    runtime_config: RuntimeConfigProvider = Depends(get_runtime_config_provider),
) -> AdminConfigSchemaResponse:
    _no_store(http_response)
    snapshot, rollback_snapshot = _snapshot_pair_for_settings(
        settings=settings,
        runtime_config=runtime_config,
    )
    values = current_config_values(settings, runtime_values=snapshot.as_dict())
    rollback_values = current_config_values(
        settings,
        runtime_values=rollback_snapshot.as_dict(),
    )
    response = AdminConfigSchemaResponse(
        registry_version=ADMIN_CONFIG_REGISTRY_VERSION,
        service_version=__version__,
        release_commit=settings.service_release_commit,
        baseline_fingerprint=snapshot.version,
        draft_ttl_seconds=ADMIN_CONFIG_DRAFT_TTL_SECONDS,
        apply_mode_contract=ADMIN_CONFIG_APPLY_MODE_CONTRACT,
        runtime_version=snapshot.version,
        runtime_source=snapshot.source,
        items=[
            AdminConfigDefinition(
                key=definition.key,
                label=definition.label,
                purpose=definition.purpose,
                category=definition.category,
                value_type=definition.value_type,
                unit=definition.unit,
                default_value=definition.default_value,
                minimum=definition.minimum,
                maximum=definition.maximum,
                apply_mode=definition.apply_mode,
                risk=definition.risk,
                observation_metrics=list(definition.observation_metrics),
                constraints=list(definition.constraints),
                current_value=values[definition.key],
                rollback_value=rollback_values[definition.key],
                decrease_only=definition.decrease_only,
            )
            for definition in CONFIG_PARAMETER_REGISTRY
        ],
    )
    _audit(
        principal,
        action="config_schema.read",
        result="ok",
        returned_count=len(response.items),
    )
    return response


@router.post(
    "/admin-api/v1/config-drafts",
    response_model=AdminConfigDraftResponse,
    status_code=201,
    include_in_schema=False,
)
def create_admin_config_draft(
    payload: AdminConfigDraftCreateRequest,
    http_response: Response,
    principal: AdminPrincipal = Depends(require_admin_config_write),
    settings: Settings = Depends(get_settings),
    store: AdminConfigDraftStore = Depends(get_admin_config_draft_store),
    runtime_config: RuntimeConfigProvider = Depends(get_runtime_config_provider),
) -> AdminConfigDraftResponse:
    _no_store(http_response)
    snapshot = _snapshot_for_settings(
        settings=settings,
        runtime_config=runtime_config,
    )
    values = current_config_values(settings, runtime_values=snapshot.as_dict())
    if payload.baseline_fingerprint != snapshot.version:
        _audit(principal, action="config_draft.create", result="conflict")
        raise service_error(ErrorCode.CONFIG_BASELINE_CONFLICT)
    try:
        draft = store.create(
            settings=settings,
            operator=principal.subject,
            reason=payload.reason,
            candidate_values=payload.candidate_values,
            current_values=values,
            current_fingerprint=snapshot.version,
        )
    except UnknownConfigKeyError:
        _audit(principal, action="config_draft.create", result="rejected")
        raise service_error(ErrorCode.INVALID_REQUEST)
    except AdminConfigStoreBusyError:
        _audit(principal, action="config_draft.create", result="busy")
        raise service_error(ErrorCode.SERVICE_BUSY)
    except AdminConfigStoreError:
        _store_failure(principal, action="config_draft.create")
        raise service_error(ErrorCode.INTERNAL_ERROR)

    _audit(
        principal,
        action="config_draft.create",
        result=draft.status,
        draft_id=draft.draft_id,
        changed_parameter_count=len(draft.diff),
        validation_error_count=len(draft.validation_errors),
    )
    return _draft_response(draft)


@router.get(
    "/admin-api/v1/config-drafts",
    response_model=AdminConfigDraftListResponse,
    include_in_schema=False,
)
def list_admin_config_drafts(
    http_response: Response,
    limit: int = Query(default=50, ge=1, le=100),
    principal: AdminPrincipal = Depends(require_admin_config_drafts),
    settings: Settings = Depends(get_settings),
    store: AdminConfigDraftStore = Depends(get_admin_config_draft_store),
    runtime_config: RuntimeConfigProvider = Depends(get_runtime_config_provider),
) -> AdminConfigDraftListResponse:
    _no_store(http_response)
    snapshot = _snapshot_for_settings(
        settings=settings,
        runtime_config=runtime_config,
    )
    try:
        drafts = store.list(
            settings=settings,
            limit=limit,
            current_fingerprint=snapshot.version,
        )
    except AdminConfigStoreBusyError:
        _audit(principal, action="config_drafts.read", result="busy")
        raise service_error(ErrorCode.SERVICE_BUSY)
    except AdminConfigStoreError:
        _store_failure(principal, action="config_drafts.read")
        raise service_error(ErrorCode.INTERNAL_ERROR)

    response = AdminConfigDraftListResponse(
        current_baseline_fingerprint=snapshot.version,
        items=[_draft_response(draft) for draft in drafts],
    )
    _audit(
        principal,
        action="config_drafts.read",
        result="ok",
        returned_count=len(response.items),
    )
    return response


@router.get(
    "/admin-api/v1/config-drafts/export",
    response_model=AdminConfigDraftExportResponse,
    include_in_schema=False,
)
def export_admin_config_draft(
    request: Request,
    http_response: Response,
    draft_id: UUID = Query(alias="id"),
    principal: AdminPrincipal = Depends(require_admin_config_drafts),
    settings: Settings = Depends(get_settings),
    store: AdminConfigDraftStore = Depends(get_admin_config_draft_store),
    runtime_config: RuntimeConfigProvider = Depends(get_runtime_config_provider),
) -> AdminConfigDraftExportResponse:
    _no_store(http_response)
    snapshot = _snapshot_for_settings(
        settings=settings,
        runtime_config=runtime_config,
    )
    try:
        draft = store.get(
            settings=settings,
            draft_id=str(draft_id),
            current_fingerprint=snapshot.version,
        )
    except AdminConfigStoreBusyError:
        _audit(principal, action="config_draft.export", result="busy")
        raise service_error(ErrorCode.SERVICE_BUSY)
    except AdminConfigStoreError:
        _store_failure(principal, action="config_draft.export")
        raise service_error(ErrorCode.INTERNAL_ERROR)
    if draft is None:
        _audit(principal, action="config_draft.export", result="not_found")
        raise HTTPException(status_code=404)

    http_response.headers["Content-Disposition"] = (
        f'attachment; filename="config-draft-{draft.draft_id}.json"'
    )
    _audit(
        principal,
        action="config_draft.export",
        result="ok",
        draft_id=draft.draft_id,
        changed_parameter_count=len(draft.diff),
        validation_error_count=len(draft.validation_errors),
    )
    return AdminConfigDraftExportResponse(
        format_version="admin-config-draft.v1",
        draft=_draft_response(draft),
    )


def _draft_response(draft: AdminConfigDraft) -> AdminConfigDraftResponse:
    return AdminConfigDraftResponse(
        id=draft.draft_id,
        created_at=draft.created_at,
        expires_at=draft.expires_at,
        operator=draft.operator,
        reason=draft.reason,
        registry_version=draft.registry_version,
        service_version=draft.service_version,
        release_commit=draft.release_commit,
        baseline_fingerprint=draft.baseline_fingerprint,
        validation_status=draft.validation_status,
        status=draft.status,
        candidate_values=draft.candidate_values,
        diff={
            key: AdminConfigDiffItem(
                old=value.old,
                new=value.new,
                apply_mode=value.apply_mode,
            )
            for key, value in draft.diff.items()
        },
        validation_errors=[
            AdminConfigValidationError(
                code=error.code,
                key=error.key,
                message=error.message,
            )
            for error in draft.validation_errors
        ],
    )


def _audit(
    principal: AdminPrincipal,
    *,
    action: str,
    result: str,
    returned_count: int | None = None,
    draft_id: str | None = None,
    changed_parameter_count: int | None = None,
    validation_error_count: int | None = None,
) -> None:
    log_event(
        logger,
        logging.INFO,
        "admin_config_completed",
        request_id=current_request_id(),
        actor=principal.subject,
        role=principal.role,
        action=action,
        result=result,
        returned_count=returned_count,
        draft_id=draft_id,
        changed_parameter_count=changed_parameter_count,
        validation_error_count=validation_error_count,
    )


def _store_failure(
    principal: AdminPrincipal,
    *,
    action: str,
) -> None:
    log_event(
        logger,
        logging.ERROR,
        "admin_config_store_failed",
        request_id=current_request_id(),
        actor=principal.subject,
        role=principal.role,
        action=action,
        result="failed",
    )


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
