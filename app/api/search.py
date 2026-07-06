from fastapi import APIRouter, Depends

from app.core.exceptions import OpenSearchQueryError, QuerySyntaxError, service_error
from app.core.security import require_api_key
from app.schemas.search import SearchRequest
from app.services.search_service import SearchService


router = APIRouter(prefix="/api/patent", tags=["patent-search"])


def get_search_service() -> SearchService:
    return SearchService()


@router.post("/search", dependencies=[Depends(require_api_key)])
def search_patents(
    request: SearchRequest,
    service: SearchService = Depends(get_search_service),
):
    try:
        return service.search(request)
    except QuerySyntaxError as exc:
        raise service_error(400, 40001, str(exc))
    except OpenSearchQueryError as exc:
        raise service_error(502, 50001, str(exc))
