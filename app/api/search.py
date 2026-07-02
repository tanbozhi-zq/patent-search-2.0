from fastapi import APIRouter, Depends, HTTPException

from app.core.security import require_api_key
from app.query.dsl_builder import UnsupportedQuerySyntax
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
    except UnsupportedQuerySyntax as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "success": False,
                "code": 40001,
                "message": str(exc),
                "data": None,
            },
        )
