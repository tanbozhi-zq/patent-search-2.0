import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.core.security import require_api_key


def test_require_api_key_allows_requests_when_auth_disabled():
    settings = Settings(enable_auth=False, api_token="")

    assert require_api_key(x_api_key=None, settings=settings) is None


def test_require_api_key_allows_matching_token():
    settings = Settings(enable_auth=True, api_token="secret-token")

    assert require_api_key(x_api_key="secret-token", settings=settings) is None


def test_require_api_key_rejects_missing_token():
    settings = Settings(enable_auth=True, api_token="secret-token")

    with pytest.raises(HTTPException) as exc:
        require_api_key(x_api_key=None, settings=settings)

    assert exc.value.status_code == 401
    assert exc.value.detail == {
        "success": False,
        "code": 40101,
        "message": "missing or invalid X-API-Key",
        "data": None,
    }


def test_require_api_key_rejects_wrong_token():
    settings = Settings(enable_auth=True, api_token="secret-token")

    with pytest.raises(HTTPException) as exc:
        require_api_key(x_api_key="wrong-token", settings=settings)

    assert exc.value.status_code == 401
