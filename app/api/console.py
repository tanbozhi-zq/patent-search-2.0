"""Console API 与正式 API 复用 Service、Schema 和错误码，但采用独立的浏览器
Basic 凭据，并把同步 OpenSearch 调用移到线程池，不能阻塞事件循环中的探针。
"""

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import (
    acquire_heavy_search_request_slot,
    acquire_search_request_slot,
    get_citation_service,
    get_detail_service,
    get_legal_history_service,
    get_search_service,
)
from app.core.exceptions import (
    ErrorCode,
    InvalidPatentIdentifierError,
    PatentNotFoundError,
    QuerySyntaxError,
    service_error,
)
from app.core.security import require_console_access
from app.schemas.response import (
    CitationResponse,
    LegalHistoryResponse,
    PatentDetailResponse,
    SearchResponse,
    TargetRankResponse,
)
from app.schemas.search import SearchRequest, TargetRankRequest
from app.services.citation_service import CitationService
from app.services.detail_service import DetailService
from app.services.legal_history_service import LegalHistoryService
from app.services.search_service import SearchService


router = APIRouter(
    prefix="/console-api",
    tags=["patent-console"],
    dependencies=[Depends(require_console_access)],
)


@router.post(
    "/search",
    dependencies=[Depends(acquire_heavy_search_request_slot)],
    response_model=SearchResponse,
    response_model_exclude_unset=True,
)
async def console_search(
    request: SearchRequest,
    service: SearchService = Depends(get_search_service),
):
    # Console 的查询语义必须和正式搜索接口相同；区别仅在认证入口和执行线程。
    try:
        return await run_in_threadpool(service.search, request)
    except QuerySyntaxError as exc:
        raise service_error(ErrorCode.QUERY_SYNTAX) from exc


@router.post(
    "/test/target-rank",
    dependencies=[Depends(acquire_heavy_search_request_slot)],
    response_model=TargetRankResponse,
)
async def console_target_rank(
    request: TargetRankRequest,
    service: SearchService = Depends(get_search_service),
):
    # 目标排名是重查询，和普通 Console 搜索共用重请求舱壁。
    try:
        return await run_in_threadpool(service.target_rank, request)
    except QuerySyntaxError as exc:
        raise service_error(ErrorCode.QUERY_SYNTAX) from exc


@router.get(
    "/detail/{patent_id}",
    dependencies=[Depends(acquire_search_request_slot)],
    response_model=PatentDetailResponse,
    response_model_exclude_none=True,
)
async def console_detail(
    patent_id: str,
    include_description: bool = False,
    service: DetailService = Depends(get_detail_service),
):
    # run_in_threadpool 是有意的：OpenSearch 客户端为同步 API，不能在 async
    # 路由中直接调用，否则一个慢详情会阻塞同一 worker 的健康检查。
    try:
        return await run_in_threadpool(
            service.get_detail,
            patent_id=patent_id,
            include_description=include_description,
        )
    except InvalidPatentIdentifierError as exc:
        raise service_error(ErrorCode.INVALID_REQUEST) from exc
    except PatentNotFoundError as exc:
        raise service_error(ErrorCode.PATENT_NOT_FOUND) from exc
    except QuerySyntaxError as exc:
        raise service_error(ErrorCode.QUERY_SYNTAX) from exc


@router.get(
    "/citations/{patent_id}",
    dependencies=[Depends(acquire_search_request_slot)],
    response_model=CitationResponse,
)
async def console_citations(
    patent_id: str,
    service: CitationService = Depends(get_citation_service),
):
    try:
        return await run_in_threadpool(service.get_citations, patent_id)
    except InvalidPatentIdentifierError as exc:
        raise service_error(ErrorCode.INVALID_REQUEST) from exc
    except PatentNotFoundError as exc:
        raise service_error(ErrorCode.PATENT_NOT_FOUND) from exc
    except QuerySyntaxError as exc:
        raise service_error(ErrorCode.QUERY_SYNTAX) from exc


@router.get(
    "/legal-history/{patent_id}",
    dependencies=[Depends(acquire_search_request_slot)],
    response_model=LegalHistoryResponse,
)
async def console_legal_history(
    patent_id: str,
    service: LegalHistoryService = Depends(get_legal_history_service),
):
    try:
        return await run_in_threadpool(service.get_legal_history, patent_id)
    except InvalidPatentIdentifierError as exc:
        raise service_error(ErrorCode.INVALID_REQUEST) from exc
    except PatentNotFoundError as exc:
        raise service_error(ErrorCode.PATENT_NOT_FOUND) from exc
    except QuerySyntaxError as exc:
        raise service_error(ErrorCode.QUERY_SYNTAX) from exc
