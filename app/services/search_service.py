from typing import Optional

from app.mappings.result_mapper import map_search_response
from app.query.dsl_builder import build_search_dsl
from app.repositories.opensearch_repo import OpenSearchRepository
from app.schemas.search import SearchRequest


class SearchService:
    def __init__(self, repository: Optional[OpenSearchRepository] = None):
        self.repository = repository or OpenSearchRepository()

    def search(self, request: SearchRequest) -> dict:
        body = build_search_dsl(request)
        raw = self.repository.search(body)
        return map_search_response(raw, page=request.page, page_size=request.page_size)
