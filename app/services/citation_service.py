"""引证服务只读取一个完整文档，再由 citation_mapper 同时生成归一化摘要和
历史兼容字段，保证 API 与 MCP 共享一份输出语义。
"""

from app.core.exceptions import InvalidPatentIdentifierError, PatentNotFoundError
from app.mappings.citation_mapper import map_citations_response
from app.repositories.opensearch_repo import OpenSearchRepository


class CitationService:
    """读取专利引证相关字段并交给兼容映射器整理。"""

    def __init__(self, repository: OpenSearchRepository):
        self.repository = repository

    def get_citations(self, patent_id: str) -> dict:
        identifier = _clean_identifier(patent_id)
        hit = self.repository.get_patent_by_identifier(identifier)

        if hit is None:
            raise PatentNotFoundError("patent not found")

        return map_citations_response(hit)


def _clean_identifier(patent_id: str) -> str:
    # 空标识符在进入 OpenSearch 前被拒绝，避免产生无意义的 term 查询。
    identifier = (patent_id or "").strip()
    if not identifier:
        raise InvalidPatentIdentifierError("patent_id 参数非法")
    return identifier
