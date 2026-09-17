"""引证读取属于轻请求：它不会进入重搜索舱壁，但仍受全局 OpenSearch 容量保护。"""

from fastapi import APIRouter, Depends

from app.api.dependencies import acquire_search_request_slot, get_citation_service
from app.core.exceptions import (
    ErrorCode,
    InvalidPatentIdentifierError,
    PatentNotFoundError,
    service_error,
)
from app.core.security import require_api_key
from app.schemas.response import CitationResponse
from app.services.citation_service import CitationService


router = APIRouter(prefix="/api/patent", tags=["patent-citations"])


@router.get(
    "/citations/{patent_id}",
    dependencies=[Depends(require_api_key), Depends(acquire_search_request_slot)],
    response_model=CitationResponse,
)
def get_patent_citations(
    patent_id: str,
    service: CitationService = Depends(get_citation_service),
):
    # 只把领域异常转换成稳定错误码；下游异常继续交给全局错误处理器处理。
    try:
        return service.get_citations(patent_id)
    except InvalidPatentIdentifierError as exc:
        raise service_error(ErrorCode.INVALID_REQUEST) from exc
    except PatentNotFoundError as exc:
        raise service_error(ErrorCode.PATENT_NOT_FOUND) from exc
