from typing import Optional

from app.core.exceptions import InvalidPatentIdentifierError, OpenSearchQueryError, PatentNotFoundError
from app.mappings.detail_mapper import map_detail_response
from app.repositories.opensearch_repo import OpenSearchRepository


class DetailService:
    def __init__(self, repository: Optional[OpenSearchRepository] = None):
        self.repository = repository or OpenSearchRepository()

    def get_detail(self, patent_id: str, include_description: bool = False) -> dict:
        identifier = _clean_identifier(patent_id)
        try:
            hit = self.repository.get_patent_by_identifier(identifier)
        except Exception as exc:
            raise OpenSearchQueryError("OpenSearch 查询异常") from exc

        if hit is None:
            raise PatentNotFoundError("patent not found")

        return map_detail_response(hit, include_description=include_description)


def _clean_identifier(patent_id: str) -> str:
    identifier = (patent_id or "").strip()
    if not identifier:
        raise InvalidPatentIdentifierError("patent_id 参数非法")
    return identifier