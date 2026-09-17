"""这里是唯一的 HTTP 错误收口。底层异常在到达响应前被压缩成稳定错误码，
但 request_id 会保留用于日志关联；内部异常文本不会出现在响应或 Retry-After。
"""

from collections import defaultdict
from typing import Any, Mapping

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import (
    ERROR_REGISTRY,
    ErrorCode,
    PaginationOutOfRangeError,
    QueryComplexityError,
    RequestBodyTooLargeError,
    SearchDependencyError,
    ServiceError,
    error_code_for_http_status,
    error_definition,
)
from app.core.request_context import (
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
    current_request_id,
    log_unhandled_exception,
    new_request_id,
)
from app.schemas.response import ErrorResponse


_PAGINATION_FIELDS = {"page", "page_size"}


def register_error_handlers(app: FastAPI) -> None:
    """注册关联上下文和所有 HTTP 错误翻译，使每个失败路径返回同一公开信封。

    领域异常、框架异常、请求校验异常和未知异常都在这里收束；处理器不返回原始
    下游文本，调用方只能依赖稳定错误码、retryable 标记和 Request ID。
    """
    # Middleware 在这里注册而不是 main.py 单独注册，是为了让错误处理器和
    # RequestContextMiddleware 使用同一组 request-id/异常去重规则。
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(ServiceError)
    async def _service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
        return known_error_response(request, exc.code)

    @app.exception_handler(SearchDependencyError)
    async def _search_dependency_error_handler(
        request: Request,
        exc: SearchDependencyError,
    ) -> JSONResponse:
        return known_error_response(request, exc.code)

    @app.exception_handler(QueryComplexityError)
    async def _query_complexity_error_handler(
        request: Request,
        exc: QueryComplexityError,
    ) -> JSONResponse:
        return known_error_response(request, ErrorCode.QUERY_COMPLEXITY_LIMIT)

    @app.exception_handler(PaginationOutOfRangeError)
    async def _pagination_error_handler(
        request: Request,
        exc: PaginationOutOfRangeError,
    ) -> JSONResponse:
        return known_error_response(request, ErrorCode.PAGINATION_OUT_OF_RANGE)

    @app.exception_handler(RequestBodyTooLargeError)
    async def _request_body_too_large_handler(
        request: Request,
        exc: RequestBodyTooLargeError,
    ) -> JSONResponse:
        return known_error_response(request, ErrorCode.REQUEST_TOO_LARGE)

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return known_error_response(
            request,
            error_code_for_http_status(exc.status_code),
            extra_headers=exc.headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _starlette_http_exception_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        return known_error_response(
            request,
            error_code_for_http_status(exc.status_code),
            extra_headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return known_error_response(request, _validation_code(exc))

    @app.exception_handler(Exception)
    async def _unexpected_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # 未知异常只记录类型，不记录异常文本；响应固定为 50002。
        log_unhandled_exception(
            request.scope.setdefault("state", {}),
            _request_id(request),
            exc,
        )
        return known_error_response(request, ErrorCode.INTERNAL_ERROR)


def error_openapi_responses() -> dict[int, dict[str, Any]]:
    """从唯一错误注册表生成按 HTTP 状态聚合的 OpenAPI 响应声明。"""
    # OpenAPI 按 HTTP 状态合并可能的业务错误码，确保文档与运行时注册表同源。
    codes_by_status: dict[int, list[ErrorCode]] = defaultdict(list)
    for code, definition in ERROR_REGISTRY.items():
        codes_by_status[definition.status_code].append(code)

    responses: dict[int, dict[str, Any]] = {}
    for status_code, codes in codes_by_status.items():
        ordered_codes = sorted(codes, key=int)
        description = "；".join(
            f"{int(code)} {error_definition(code).message}" for code in ordered_codes
        )
        response: dict[str, Any] = {
            "model": ErrorResponse,
            "description": f"统一错误响应：{description}",
        }
        if any(error_definition(code).retry_after_seconds is not None for code in ordered_codes):
            response["headers"] = {
                "Retry-After": {
                    "description": "调用方应等待的秒数；由对应错误码的注册表定义。",
                    "schema": {"type": "integer", "minimum": 1},
                }
            }
        responses[status_code] = response
    return responses


def known_error_response(
    request: Request,
    code: ErrorCode | int,
    *,
    extra_headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """把一个已登记错误码渲染为公开 JSON 信封，并同步写入响应关联头。

    ``extra_headers`` 只用于保留框架的协议头（例如 WWW-Authenticate）；Retry-After、
    status、message 与 retryable 一律由错误注册表决定，调用方不能自由覆盖。
    """
    # 所有已知错误都在这里设置响应头和 body 的 request_id。调用方因此可以用
    # 一个 ID 同时定位服务日志和后端/MCP 转发链路。
    resolved_code = ErrorCode(code)
    definition = error_definition(resolved_code)
    request_id = _request_id(request)
    request.state.response_code = int(resolved_code)
    headers = dict(extra_headers or {})
    headers[REQUEST_ID_HEADER] = request_id
    if definition.retry_after_seconds is not None:
        headers["Retry-After"] = str(definition.retry_after_seconds)
    return JSONResponse(
        status_code=definition.status_code,
        content=ErrorResponse(
            code=resolved_code,
            message=definition.message,
            request_id=request_id,
            retryable=definition.retryable,
        ).model_dump(mode="json"),
        headers=headers,
    )


def _request_id(request: Request) -> str:
    # 正常请求由 RequestContextMiddleware 提供 ID；中间件之外触发的错误（例如
    # 请求体限制提前拒绝）也在这里补齐一个可关联的 ID。
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        return request_id
    request_id = current_request_id() or new_request_id()
    request.state.request_id = request_id
    return request_id


def _validation_code(exc: RequestValidationError) -> ErrorCode:
    """将 Pydantic 的细节错误压缩成项目对外承诺的少数稳定输入错误码。"""
    # Pydantic 的字段错误映射到项目的稳定码：分页优先于通用参数错误，布尔式
    # 和语义文本过长都归入同一字符复杂度限制，其余输入问题归入 40002。
    errors = exc.errors()
    for error in errors:
        loc = error.get("loc", ())
        if loc and loc[-1] in _PAGINATION_FIELDS:
            return ErrorCode.PAGINATION_OUT_OF_RANGE
    for error in errors:
        loc = error.get("loc", ())
        if (
            loc
            and loc[-1] in {"q", "semantic_text"}
            and error.get("type") == "string_too_long"
        ):
            return ErrorCode.QUERY_COMPLEXITY_LIMIT
    return ErrorCode.INVALID_REQUEST
