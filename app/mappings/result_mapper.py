"""搜索 mapper 将 OpenSearch hits 变成固定字段的分页响应。与详情不同，列表字段
即使为空也保留，便于客户端稳定反序列化和表格渲染。
"""

from math import ceil

from app.mappings.ipc_mapper import normalized_ipc_list, normalized_main_ipc
from app.mappings.patent_type_mapper import normalized_patent_type
from app.mappings.text_value import normalized_text
from app.query.budget import QueryBudget


SEARCH_RECORD_FIELDS = (
    "id",
    "application_number",
    "publication_number",
    "title",
    "abstract",
    "applicant",
    "current_assignee",
    "inventor",
    "main_ipc",
    "ipc_list",
    "main_claim",
    "application_date",
    "publication_date",
    "legal_status",
    "type",
    "score",
)


def map_search_response(
    raw: dict,
    page: int,
    page_size: int,
    *,
    budget: QueryBudget,
    result_window: int | None = None,
    search_context: dict | None = None,
) -> dict:
    """将已校验的 OpenSearch 响应映射为受 result window 限制的分页列表。

    Repository 负责验证上游响应的最低结构；本函数计算总页数与当前预算实际可访问
    的页数，并逐条进行业务字段归一化。即使总命中更多，``next_page`` 也不会指向
    超出 OpenSearch result window 的页，避免客户端得到必然失败的翻页链接。
    """
    # 列表记录维持固定键集；分页可访问性由预算而非 total 单独决定。
    hits = raw.get("hits", {})
    total = _extract_total(hits.get("total", 0))
    if result_window is not None:
        total = min(total, result_window)
    records = [_map_record(hit) for hit in hits.get("hits", [])]
    total_pages = ceil(total / page_size) if total else 0
    accessible_window = min(
        budget.max_result_window,
        result_window if result_window is not None else budget.max_result_window,
    )
    accessible_pages = min(
        total_pages,
        accessible_window // page_size,
    )

    response = {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "accessible_pages": accessible_pages,
        "next_page": page + 1 if page < accessible_pages else None,
        "took_ms": raw.get("took"),
        "records": records,
    }
    if search_context is not None:
        response["search_context"] = search_context
    return response


def _extract_total(total):
    # track_total_hits 在不同 OpenSearch 版本可能是 int 或 {value,...}。
    if isinstance(total, dict):
        return total.get("value", 0)
    return total or 0


def _map_record(hit: dict) -> dict:
    """把单条 hit 的 source 投影为搜索列表记录的固定 16 字段。

    所有索引字段别名、文本空值、IPC 和专利类型规则在此收敛；不会把 ``_source``
    原样透传。``_score`` 保留为排序诊断值，字段缺失则以各归一化器约定的空值表示。
    """
    # 映射器不把原始 source 直接返回，所有外部字段都通过明确的 snake_case 键生成。
    source = hit.get("_source", {})
    application_number = normalized_text(source.get("ApplicationNumber"))
    publication_number = normalized_text(source.get("PublicationNumber"))
    main_claim = _first_string(source, ("MainClaim", "MainClaimCN", "MainClaimEN"))

    return {
        "id": normalized_text(source.get("patent_id")),
        "application_number": application_number,
        "publication_number": publication_number,
        "title": normalized_text(source.get("Title")),
        "abstract": normalized_text(source.get("Abstract")),
        "applicant": normalized_text(source.get("Applicant")),
        "current_assignee": normalized_text(source.get("Assignee")),
        "inventor": normalized_text(source.get("Inventor")),
        "main_ipc": normalized_main_ipc(source),
        "ipc_list": normalized_ipc_list(source.get("IPCList")),
        "main_claim": main_claim,
        "application_date": normalized_text(source.get("ApplicationDate")),
        "publication_date": normalized_text(source.get("PublicationDate")),
        "legal_status": normalized_text(source.get("LatestLegalStatus") or source.get("LegalStatus")),
        "type": normalized_patent_type(source),
        "score": hit.get("_score"),
    }


def _first_string(source: dict, fields: tuple[str, ...]) -> str:
    # 主权利要求按通用 → CN → EN 回退，保持与详情 contract 一致。
    for field in fields:
        value = normalized_text(source.get(field))
        if value:
            return value
    return ""
