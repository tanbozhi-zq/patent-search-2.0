"""详情路由保持薄：鉴权和全局轻请求舱壁由依赖完成，标识符清理和响应映射
由 DetailService 负责，避免正式 API 和 Console API 出现两套业务规则。
"""

from fastapi import APIRouter, Depends

from app.api.dependencies import acquire_search_request_slot, get_detail_service
from app.core.exceptions import (
    ErrorCode,
    InvalidPatentIdentifierError,
    PatentNotFoundError,
    service_error,
)
from app.core.security import require_api_key
from app.schemas.response import PatentDetailResponse
from app.services.detail_service import DetailService


router = APIRouter(prefix="/api/patent", tags=["patent-detail"])


@router.get(
    "/detail/{patent_id}",
    dependencies=[Depends(require_api_key), Depends(acquire_search_request_slot)],
    response_model=PatentDetailResponse,
    response_model_exclude_none=True,
)
def get_patent_detail(
    patent_id: str,
    include_description: bool = False,
    service: DetailService = Depends(get_detail_service),
):
    # response_model_exclude_none 保持“无可靠值就不返回”的详情契约。
    try:
        return service.get_detail(patent_id, include_description=include_description)
    except InvalidPatentIdentifierError as exc:
        raise service_error(ErrorCode.INVALID_REQUEST) from exc
    except PatentNotFoundError as exc:
        raise service_error(ErrorCode.PATENT_NOT_FOUND) from exc
