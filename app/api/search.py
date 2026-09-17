"""正式检索入口只做三件事：声明鉴权/舱壁、让 Pydantic 校验请求、把查询语法
异常翻译成公共错误码。查询解析和 OpenSearch 调用都留在 Service 及其下层。
"""

from fastapi import APIRouter, Depends

from app.api.dependencies import (
    acquire_heavy_search_request_slot,
    get_search_service,
)
from app.core.exceptions import ErrorCode, QuerySyntaxError, service_error
from app.core.security import require_api_key
from app.schemas.response import SearchResponse
from app.schemas.search import SearchRequest
from app.services.search_service import SearchService


router = APIRouter(prefix="/api/patent", tags=["patent-search"])


@router.post(
    "/search",
    dependencies=[
        Depends(require_api_key),
        Depends(acquire_heavy_search_request_slot),
    ],
    response_model=SearchResponse,
    response_model_exclude_unset=True,
)
def search_patents(
    request: SearchRequest,
    service: SearchService = Depends(get_search_service),
):
    # FastAPI 会先执行依赖和请求模型校验；只有通过鉴权、容量和字段边界后，
    # 才会进入 SearchService。查询语法错误在这里统一落到 40001。
    try:
        return service.search(request)
    except QuerySyntaxError as exc:
        raise service_error(ErrorCode.QUERY_SYNTAX) from exc
