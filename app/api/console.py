from fastapi import APIRouter, Depends, Request

from app.core.exceptions import OpenSearchQueryError, QuerySyntaxError, service_error
from app.schemas.search import SearchRequest, TargetRankRequest
from app.services.citation_service import CitationService
from app.services.detail_service import DetailService
from app.services.legal_history_service import LegalHistoryService
from app.services.search_service import SearchService


router = APIRouter(prefix="/console-api", tags=["patent-console"])


def get_search_service() -> SearchService:
    return SearchService()


def get_detail_service() -> DetailService:
    return DetailService()


def get_citation_service() -> CitationService:
    return CitationService()


def get_legal_history_service() -> LegalHistoryService:
    return LegalHistoryService()


@router.post("/search")
async def console_search(
    request: SearchRequest,
    service: SearchService = Depends(get_search_service),
):
    try:
        return service.search(request)
    except QuerySyntaxError as exc:
        raise service_error(400, 40001, str(exc)) from exc
    except OpenSearchQueryError as exc:
        raise service_error(502, 50001, str(exc)) from exc


@router.post("/test/target-rank")
async def console_target_rank(
    request: TargetRankRequest,
    service: SearchService = Depends(get_search_service),
):
    try:
        return service.target_rank(request)
    except QuerySyntaxError as exc:
        raise service_error(400, 40001, str(exc)) from exc
    except OpenSearchQueryError as exc:
        raise service_error(502, 50001, str(exc)) from exc


@router.get("/detail/{patent_id}")
async def console_detail(
    request: Request,
    patent_id: str,
    include_description: bool = False,
    service: DetailService = Depends(get_detail_service),
):
    try:
        return service.get_detail(
            patent_id=patent_id,
            include_description=include_description,
        )
    except QuerySyntaxError as exc:
        raise service_error(400, 40001, str(exc)) from exc
    except OpenSearchQueryError as exc:
        raise service_error(502, 50001, str(exc)) from exc


@router.get("/citations/{patent_id}")
async def console_citations(
    patent_id: str,
    service: CitationService = Depends(get_citation_service),
):
    try:
        return service.get_citations(patent_id)
    except QuerySyntaxError as exc:
        raise service_error(400, 40001, str(exc)) from exc
    except OpenSearchQueryError as exc:
        raise service_error(502, 50001, str(exc)) from exc


@router.get("/legal-history/{patent_id}")
async def console_legal_history(
    patent_id: str,
    service: LegalHistoryService = Depends(get_legal_history_service),
):
    try:
        return service.get_legal_history(patent_id)
    except QuerySyntaxError as exc:
        raise service_error(400, 40001, str(exc)) from exc
    except OpenSearchQueryError as exc:
        raise service_error(502, 50001, str(exc)) from exc
