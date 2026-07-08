from fastapi import APIRouter, Depends

from app.core.exceptions import (
    InvalidPatentIdentifierError,
    OpenSearchQueryError,
    PatentNotFoundError,
    service_error,
)
from app.core.security import require_api_key
from app.services.legal_history_service import LegalHistoryService


router = APIRouter(prefix="/api/patent", tags=["patent-legal-history"])


def get_legal_history_service() -> LegalHistoryService:
    return LegalHistoryService()


@router.get("/legal-history/{patent_id}", dependencies=[Depends(require_api_key)])
def get_patent_legal_history(
    patent_id: str,
    service: LegalHistoryService = Depends(get_legal_history_service),
):
    try:
        return service.get_legal_history(patent_id)
    except InvalidPatentIdentifierError as exc:
        raise service_error(400, 40002, str(exc))
    except PatentNotFoundError as exc:
        raise service_error(404, 40401, str(exc))
    except OpenSearchQueryError as exc:
        raise service_error(502, 50001, str(exc))
