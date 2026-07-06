from fastapi import APIRouter, Depends

from app.core.exceptions import (
    InvalidPatentIdentifierError,
    OpenSearchQueryError,
    PatentNotFoundError,
    service_error,
)
from app.core.security import require_api_key
from app.services.detail_service import DetailService


router = APIRouter(prefix="/api/patent", tags=["patent-detail"])


def get_detail_service() -> DetailService:
    return DetailService()


@router.get("/detail/{patent_id}", dependencies=[Depends(require_api_key)])
def get_patent_detail(
    patent_id: str,
    include_description: bool = False,
    service: DetailService = Depends(get_detail_service),
):
    try:
        return service.get_detail(patent_id, include_description=include_description)
    except InvalidPatentIdentifierError as exc:
        raise service_error(400, 40002, str(exc))
    except PatentNotFoundError as exc:
        raise service_error(404, 40401, str(exc))
    except OpenSearchQueryError as exc:
        raise service_error(502, 50001, str(exc))