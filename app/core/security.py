from typing import Optional

from fastapi import Header, HTTPException

from app.core.config import Settings, get_settings


def require_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    settings: Settings = get_settings(),
) -> None:
    if not settings.enable_auth:
        return None

    if settings.api_token and x_api_key == settings.api_token:
        return None

    raise HTTPException(
        status_code=401,
        detail={
            "success": False,
            "code": 40101,
            "message": "missing or invalid X-API-Key",
            "data": None,
        },
    )
