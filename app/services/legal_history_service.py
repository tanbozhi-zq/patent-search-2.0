from typing import Optional

from app.core.exceptions import InvalidPatentIdentifierError, OpenSearchQueryError, PatentNotFoundError
from app.mappings.legal_history_mapper import map_legal_history_response
from app.repositories.opensearch_repo import OpenSearchRepository


class LegalHistoryService:
    def __init__(self, repository: Optional[OpenSearchRepository] = None):
        self.repository = repository or OpenSearchRepository()

    def get_legal_history(self, patent_id: str) -> dict:
        identifier = _clean_identifier(patent_id)
        try:
            hit = self.repository.get_patent_by_identifier(identifier)
        except Exception as exc:
            raise OpenSearchQueryError("OpenSearch 查询异常") from exc

        if hit is None:
            raise PatentNotFoundError("patent not found")

        return map_legal_history_response(hit)


def _clean_identifier(patent_id: str) -> str:
    identifier = (patent_id or "").strip()
    if not identifier:
        raise InvalidPatentIdentifierError("patent_id 参数非法")
    return identifier
