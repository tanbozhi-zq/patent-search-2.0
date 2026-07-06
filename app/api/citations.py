from fastapi import APIRouter, Depends

from app.core.exceptions import (
    InvalidPatentIdentifierError,
    OpenSearchQueryError,
    PatentNotFoundError,
    service_error,
)
from app.core.security import require_api_key
from app.services.citation_service import CitationService


router = APIRouter(prefix="/api/patent", tags=["patent-citations"])


def get_citation_service() -> CitationService:
    return CitationService()


@router.get("/citations/{patent_id}", dependencies=[Depends(require_api_key)])
def get_patent_citations(
    patent_id: str,
    service: CitationService = Depends(get_citation_service),
):
    try:
        return service.get_citations(patent_id)
    except InvalidPatentIdentifierError as exc:
        raise service_error(400, 40002, str(exc))
    except PatentNotFoundError as exc:
        raise service_error(404, 40401, str(exc))
    except OpenSearchQueryError as exc:
        raise service_error(502, 50001, str(exc))