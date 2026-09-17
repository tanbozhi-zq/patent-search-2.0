"""所有对外错误都先在这里登记，再由 error_handlers 统一生成 HTTP 响应。
这样路由、Repository 和 MCP 可以抛出同一套领域错误，而不会各自拼装消息。
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Final


class ErrorCode(IntEnum):
    """服务对外稳定的业务错误码枚举。

    数值是客户端、HTTP handler、MCP 与指标共同使用的机器契约；人类消息、HTTP
    状态、可重试性和 Retry-After 均由 ``ERROR_REGISTRY`` 集中定义，调用方不能
    依据消息文本推断处理策略。
    """

    # 数值按 HTTP 语义分段，但调用方应依赖 code/status/retryable，不要解析 message。
    QUERY_SYNTAX = 40001
    INVALID_REQUEST = 40002
    PAGINATION_OUT_OF_RANGE = 40003
    QUERY_COMPLEXITY_LIMIT = 40004
    AUTHENTICATION_FAILED = 40101
    ROUTE_NOT_FOUND = 40400
    PATENT_NOT_FOUND = 40401
    METHOD_NOT_ALLOWED = 40500
    CONFIG_BASELINE_CONFLICT = 40901
    REQUEST_TOO_LARGE = 41301
    RATE_LIMITED = 42901
    SEARCH_DEPENDENCY_ERROR = 50001
    INTERNAL_ERROR = 50002
    SERVICE_BUSY = 50301
    SEARCH_DEPENDENCY_UNAVAILABLE = 50302
    SEARCH_DEPENDENCY_TIMEOUT = 50401


@dataclass(frozen=True)
class ErrorDefinition:
    """一个错误码对应的安全对外响应策略。

    该对象不包含底层异常、连接地址或索引等诊断细节；它只提供 handler 能稳定公开
    的 HTTP 状态、人类消息和重试提示。
    """

    # 注册表中的定义同时决定 HTTP 状态、是否建议重试和 Retry-After 秒数。
    status_code: int
    message: str
    retryable: bool
    retry_after_seconds: int | None = None


ERROR_REGISTRY: Final[dict[ErrorCode, ErrorDefinition]] = {
    ErrorCode.QUERY_SYNTAX: ErrorDefinition(400, "查询语法错误", False),
    ErrorCode.INVALID_REQUEST: ErrorDefinition(400, "请求参数无效", False),
    ErrorCode.PAGINATION_OUT_OF_RANGE: ErrorDefinition(400, "分页参数或结果窗口无效", False),
    ErrorCode.QUERY_COMPLEXITY_LIMIT: ErrorDefinition(400, "查询复杂度超限", False),
    ErrorCode.AUTHENTICATION_FAILED: ErrorDefinition(401, "未认证或凭据无效", False),
    ErrorCode.ROUTE_NOT_FOUND: ErrorDefinition(404, "路由不存在", False),
    ErrorCode.PATENT_NOT_FOUND: ErrorDefinition(404, "专利不存在", False),
    ErrorCode.METHOD_NOT_ALLOWED: ErrorDefinition(405, "HTTP 方法不允许", False),
    ErrorCode.CONFIG_BASELINE_CONFLICT: ErrorDefinition(
        409,
        "配置基线已变化，请刷新后重新预检",
        False,
    ),
    ErrorCode.REQUEST_TOO_LARGE: ErrorDefinition(413, "请求体过大", False),
    ErrorCode.RATE_LIMITED: ErrorDefinition(429, "调用方触发限流", True, retry_after_seconds=60),
    ErrorCode.SEARCH_DEPENDENCY_ERROR: ErrorDefinition(502, "搜索依赖请求失败", False),
    ErrorCode.INTERNAL_ERROR: ErrorDefinition(500, "服务内部异常", False),
    ErrorCode.SERVICE_BUSY: ErrorDefinition(503, "服务繁忙，请稍后重试", True, retry_after_seconds=1),
    ErrorCode.SEARCH_DEPENDENCY_UNAVAILABLE: ErrorDefinition(503, "搜索依赖暂时不可用", True, retry_after_seconds=5),
    ErrorCode.SEARCH_DEPENDENCY_TIMEOUT: ErrorDefinition(504, "搜索依赖超时", True),
}


_HTTP_STATUS_DEFAULT_CODES: Final[dict[int, ErrorCode]] = {
    400: ErrorCode.INVALID_REQUEST,
    401: ErrorCode.AUTHENTICATION_FAILED,
    404: ErrorCode.ROUTE_NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    409: ErrorCode.CONFIG_BASELINE_CONFLICT,
    413: ErrorCode.REQUEST_TOO_LARGE,
    422: ErrorCode.INVALID_REQUEST,
    429: ErrorCode.RATE_LIMITED,
    500: ErrorCode.INTERNAL_ERROR,
    502: ErrorCode.SEARCH_DEPENDENCY_ERROR,
    503: ErrorCode.SERVICE_BUSY,
    504: ErrorCode.SEARCH_DEPENDENCY_TIMEOUT,
}


def error_definition(code: ErrorCode | int) -> ErrorDefinition:
    return ERROR_REGISTRY[ErrorCode(code)]


def error_code_for_http_status(status_code: int) -> ErrorCode:
    return _HTTP_STATUS_DEFAULT_CODES.get(status_code, ErrorCode.INTERNAL_ERROR)


class ServiceError(Exception):
    """由业务层显式抛出的、已登记错误码的安全异常。

    错误处理器可以直接根据 ``code`` 和 ``definition`` 生成统一响应；异常文本是
    注册表的稳定消息，不能用它承载下游响应原文或未经审查的调试信息。
    """

    # ServiceError 只携带已登记的错误码；异常文本使用稳定的人类消息，绝不附带
    # OpenSearch 原文、URL、索引或凭据。
    def __init__(self, code: ErrorCode | int):
        self.code = ErrorCode(code)
        self.definition = error_definition(self.code)
        super().__init__(self.definition.message)


class QuerySyntaxError(ValueError):
    pass


class QueryComplexityError(ValueError):
    pass


class PaginationOutOfRangeError(ValueError):
    pass


class RequestBodyTooLargeError(ValueError):
    pass


class InvalidPatentIdentifierError(ValueError):
    pass


class PatentNotFoundError(LookupError):
    pass


class SearchDependencyError(RuntimeError):
    """OpenSearch 及其传输层故障的共同基类。

    具体子类只通过 ``code`` 区分查询失败、不可用和超时；异常处理器据此转换为
    对应的公共错误，而调用路径无需了解底层 SDK 的异常类型。
    """

    # 下游错误保留在一个继承树内，错误处理器可以统一读取每个具体类型的 code。
    code: ErrorCode


class OpenSearchQueryError(SearchDependencyError):
    code = ErrorCode.SEARCH_DEPENDENCY_ERROR


class SearchDependencyUnavailableError(SearchDependencyError):
    code = ErrorCode.SEARCH_DEPENDENCY_UNAVAILABLE


class SearchDependencyTimeoutError(SearchDependencyError):
    code = ErrorCode.SEARCH_DEPENDENCY_TIMEOUT


class QueryVectorError(SearchDependencyError):
    """查询向量依赖故障的共同边界，不携带原始文本、向量或凭据。"""

    code = ErrorCode.SEARCH_DEPENDENCY_ERROR


class QueryVectorInvalidResponseError(QueryVectorError):
    pass


class QueryVectorUnavailableError(QueryVectorError):
    code = ErrorCode.SEARCH_DEPENDENCY_UNAVAILABLE


class QueryVectorTimeoutError(QueryVectorError):
    code = ErrorCode.SEARCH_DEPENDENCY_TIMEOUT


def service_error(code: ErrorCode | int) -> ServiceError:
    # 路由通常用这个小工厂把领域异常显式翻译成公共服务错误。
    return ServiceError(code)
