"""详情 Service 负责标识符清理、最小 source 投影和详情映射；找不到文档的
领域语义在这里统一成 PatentNotFoundError，由 API 层再翻译成 40401。
"""

from app.core.exceptions import InvalidPatentIdentifierError, PatentNotFoundError
from app.mappings.detail_mapper import map_detail_response
from app.mappings.source_fields import detail_source_fields
from app.repositories.opensearch_repo import OpenSearchRepository


class DetailService:
    """读取单件专利详情，并按请求决定是否加载说明书。"""

    def __init__(self, repository: OpenSearchRepository):
        self.repository = repository

    def get_detail(self, patent_id: str, include_description: bool = False) -> dict:
        # 只有明确要求时才把 Instructions 加入 _source，避免普通详情读取大量正文。
        identifier = _clean_identifier(patent_id)
        hit = self.repository.get_patent_by_identifier(
            identifier,
            source_fields=detail_source_fields(include_description),
        )

        if hit is None:
            raise PatentNotFoundError("patent not found")

        return map_detail_response(hit, include_description=include_description)


def _clean_identifier(patent_id: str) -> str:
    # 这里只做空值/首尾空白处理；真正的字段匹配由 Repository 按稳定优先级完成。
    identifier = (patent_id or "").strip()
    if not identifier:
        raise InvalidPatentIdentifierError("patent_id 参数非法")
    return identifier
