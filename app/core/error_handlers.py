from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


_PAGINATION_FIELDS = {"page", "page_size"}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and {"success", "code", "message", "data"} <= detail.keys():
            return JSONResponse(status_code=exc.status_code, content=detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "code": exc.status_code * 100,
                "message": detail if isinstance(detail, str) else "internal error",
                "data": None,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        code = _validation_code(exc)
        message = _validation_message(exc)
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "code": code,
                "message": message,
                "data": None,
            },
        )


def _validation_code(exc: RequestValidationError) -> int:
    for error in exc.errors():
        loc = error.get("loc", ())
        if loc and loc[-1] in _PAGINATION_FIELDS:
            return 40003
    return 40002


def _validation_message(exc: RequestValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        loc = error.get("loc", ())
        field = ".".join(str(part) for part in loc if part not in ("body",))
        msg = error.get("msg", "invalid")
        parts.append(f"{field or '参数'} {msg}".strip())
    return "参数非法：" + "; ".join(parts) if parts else "参数非法"
