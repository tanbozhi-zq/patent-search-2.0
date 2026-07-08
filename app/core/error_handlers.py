import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


_PAGINATION_FIELDS = {"page", "page_size"}
logger = logging.getLogger(__name__)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        return _http_exception_response(exc)

    @app.exception_handler(StarletteHTTPException)
    async def _starlette_http_exception_handler(
        _: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        return _http_exception_response(exc)

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

    @app.exception_handler(Exception)
    async def _unexpected_exception_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "Unhandled service exception",
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "code": 50002,
                "message": "服务内部异常",
                "data": None,
            },
        )


def _http_exception_response(exc: StarletteHTTPException) -> JSONResponse:
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
