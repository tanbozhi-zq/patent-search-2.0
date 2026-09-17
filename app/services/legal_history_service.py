"""法律状态历史保留上游交易条目结构；本层只负责找到文档和确定空/不存在语义。"""

from app.core.exceptions import InvalidPatentIdentifierError, PatentNotFoundError
from app.mappings.legal_history_mapper import map_legal_history_response
from app.repositories.opensearch_repo import OpenSearchRepository


class LegalHistoryService:
    """读取并映射一件专利的法律状态交易历史。"""

    def __init__(self, repository: OpenSearchRepository):
        self.repository = repository

    def get_legal_history(self, patent_id: str) -> dict:
        identifier = _clean_identifier(patent_id)
        hit = self.repository.get_patent_by_identifier(identifier)

        if hit is None:
            raise PatentNotFoundError("patent not found")

        return map_legal_history_response(hit)


def _clean_identifier(patent_id: str) -> str:
    # 与详情、引证保持完全一致的标识符入口规则。
    identifier = (patent_id or "").strip()
    if not identifier:
        raise InvalidPatentIdentifierError("patent_id 参数非法")
    return identifier
