"""法律状态历史和详情/引证共用同一个轻请求资源边界，保证读取类接口的行为一致。"""

from fastapi import APIRouter, Depends

from app.api.dependencies import acquire_search_request_slot, get_legal_history_service
from app.core.exceptions import (
    ErrorCode,
    InvalidPatentIdentifierError,
    PatentNotFoundError,
    service_error,
)
from app.core.security import require_api_key
from app.schemas.response import LegalHistoryResponse
from app.services.legal_history_service import LegalHistoryService


router = APIRouter(prefix="/api/patent", tags=["patent-legal-history"])


@router.get(
    "/legal-history/{patent_id}",
    dependencies=[Depends(require_api_key), Depends(acquire_search_request_slot)],
    response_model=LegalHistoryResponse,
)
def get_patent_legal_history(
    patent_id: str,
    service: LegalHistoryService = Depends(get_legal_history_service),
):
    # Service 只返回映射后的结构；路由不直接暴露 OpenSearch hit。
    try:
        return service.get_legal_history(patent_id)
    except InvalidPatentIdentifierError as exc:
        raise service_error(ErrorCode.INVALID_REQUEST) from exc
    except PatentNotFoundError as exc:
        raise service_error(ErrorCode.PATENT_NOT_FOUND) from exc
