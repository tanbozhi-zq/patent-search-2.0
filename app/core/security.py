"""三种入口各自有清晰的鉴权边界：正式 API 使用 X-API-Key，Console 使用独立
Basic 或程序兼容的 API Key，Admin 只接受独立 viewer Basic，不会被业务开关旁路。
"""

from dataclasses import dataclass
import logging
from secrets import compare_digest
from typing import Optional

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import APIKeyHeader, HTTPBasic, HTTPBasicCredentials

from app.core.config import Settings, get_settings
from app.core.exceptions import ErrorCode, service_error
from app.core.logging import log_event


api_key_scheme = APIKeyHeader(name="X-API-Key", scheme_name="ApiKeyAuth", auto_error=False)
console_basic_scheme = HTTPBasic(scheme_name="ConsoleBasicAuth", auto_error=False)
admin_basic_scheme = HTTPBasic(scheme_name="AdminBasicAuth", auto_error=False)
_CONSOLE_AUTH_CHALLENGE = 'Basic realm="patent-console"'
_ADMIN_AUTH_CHALLENGE = 'Basic realm="patent-admin"'
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AdminPrincipal:
    """已通过管理面认证的最小主体，用于审计事件而不是通用 RBAC 扩展。"""

    # 目前管理面板只有 viewer 角色，但保留主体/角色结构，审计事件可稳定记录。
    subject: str
    role: str = "admin"


def _matches(actual: str | None, expected: str) -> bool:
    # 认证比较使用 constant-time compare；空期望值也必须失败，避免未配置凭据
    # 退化成“双方都为空即可通过”。
    if actual is None or not expected:
        return False
    return compare_digest(actual.encode("utf-8"), expected.encode("utf-8"))


def require_api_key(
    x_api_key: Optional[str] = Security(api_key_scheme),
    settings: Settings = Depends(get_settings),
) -> None:
    """验证正式 API 的 ``X-API-Key``，或在受控的显式关闭模式下放行。"""
    # ENABLE_AUTH=false 只用于受控本地/测试环境；生产的默认路径必须验证 API Key。
    if not settings.enable_auth:
        return None

    if _matches(x_api_key, settings.api_token):
        return None

    raise service_error(ErrorCode.AUTHENTICATION_FAILED)


def require_console_access(
    x_api_key: Optional[str] = Security(api_key_scheme),
    basic_credentials: HTTPBasicCredentials | None = Security(console_basic_scheme),
    settings: Settings = Depends(get_settings),
) -> None:
    """验证 Console 的 API Key 或浏览器 Basic 凭据，并在失败时发起 Basic challenge。

    Console 可兼容程序调用的 API Key，但浏览器会话只能使用独立 Basic 凭据，不能因
    此把 API Token 写进前端页面或客户端存储。
    """
    # Console 同时接受程序调用的 X-API-Key 和浏览器 Basic，但两者都必须匹配
    # 各自的凭据；失败时返回挑战头，方便浏览器再次弹出登录框。
    if not settings.enable_auth:
        return None

    if _matches(x_api_key, settings.api_token):
        return None

    if basic_credentials is not None and _matches(
        basic_credentials.username,
        settings.console_username,
    ) and _matches(
        basic_credentials.password,
        settings.console_password,
    ):
        return None

    raise HTTPException(
        status_code=401,
        headers={"WWW-Authenticate": _CONSOLE_AUTH_CHALLENGE},
    )


def require_admin(
    request: Request,
    basic_credentials: HTTPBasicCredentials | None = Security(admin_basic_scheme),
    settings: Settings = Depends(get_settings),
) -> AdminPrincipal:
    """验证独立的管理 viewer 凭据，或在功能关闭时以 404 隐藏管理面存在性。"""
    # 管理入口关闭时故意返回 404，隐藏功能存在性；开启后匿名、业务 Token 和
    # Console 凭据都不能代替独立的 viewer 凭据。
    if not settings.admin_enabled:
        raise HTTPException(status_code=404)

    if basic_credentials is not None and _matches(
        basic_credentials.username,
        settings.admin_viewer_username,
    ) and _matches(
        basic_credentials.password,
        settings.admin_viewer_password,
    ):
        return AdminPrincipal(subject=settings.admin_viewer_username)

    log_event(
        logger,
        logging.WARNING,
        "admin_auth_denied",
        action=request.url.path,
        result="denied",
    )
    raise HTTPException(
        status_code=401,
        headers={"WWW-Authenticate": _ADMIN_AUTH_CHALLENGE},
    )
