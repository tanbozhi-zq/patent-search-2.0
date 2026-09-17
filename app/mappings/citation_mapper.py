"""引证来源同时包含专利引用、非专利文献和历史兼容对象。mapper 生成新的摘要列表，
也保留原字段，且不把任意供应商结构误判为专利。
"""

from app.mappings.ipc_mapper import normalize_compat_ipc_records, normalized_main_ipc


def map_citations_response(hit: dict) -> dict:
    """将一条专利 hit 拆分为被引、专利引证与非专利引证三个稳定集合。

    原始兼容字段仍会随响应返回，供需要完整历史结构的调用方使用；摘要列表则只
    暴露本服务约定的字段。输入即使缺少某个旧字段也返回完整顶层结构，便于客户
    端直接渲染而无需区分索引版本。
    """
    # 先把 scalar/list 统一，再分别生成摘要和非专利参考，最后保留清洗后的兼容字段。
    source = hit.get("_source", {})
    references_cited = normalize_compat_ipc_records(_array(source.get("ReferencesCited")))
    related_documents = normalize_compat_ipc_records(_array(source.get("RelatedDocuments")))
    raw = _string(source.get("ReferencesCitedRaw"))
    text = _string(source.get("ReferencesCitedText"))

    return {
        "patent_id": _string(source.get("patent_id")),
        "cited_by": _summarize_patents(related_documents),
        "patent_references": _summarize_patents(references_cited),
        "non_patent_references": _non_patent_references(raw, text),
        "referencesCited": references_cited,
        "referencesCitedRaw": raw,
        "referencesCitedText": text,
        "relatedDocuments": related_documents,
    }


def _summarize_patents(items: list) -> list:
    """从兼容引证列表构造可显示的专利摘要，并按完整摘要去重。

    仅字典项可能表示专利；完全空的映射结果不应出现在调用方看到的列表中。去重键
    覆盖摘要的所有字段而非仅 document number，因为历史数据可能缺失或复用编号。
    """
    # 只接受字典对象，并按摘要所有字段去重；空摘要不应制造没有业务信息的条目。
    summaries = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        summary = _summarize_patent(item)
        if not any(summary.values()):
            continue
        key = tuple(summary.values())
        if key in seen:
            continue
        seen.add(key)
        summaries.append(summary)
    return summaries


def _summarize_patent(item: dict) -> dict:
    # 兼容大小写/驼峰字段，并用 document number 补齐缺失 id。
    document_number = _document_number(item)
    return {
        "id": _string(
            _first_value(item, ("id", "patent_id", "patentId", "PatentID"))
            or document_number
        ),
        "title": _string(_first_value(item, ("title", "Title"))),
        "applicant": _string(_first_value(item, ("applicant", "Applicant"))),
        "application_date": _string(
            _first_value(item, ("applicationDate", "ApplicationDate", "Date", "date"))
        ),
        "application_number": _string(_first_value(item, ("applicationNumber", "ApplicationNumber"))),
        "type": _string(_first_value(item, ("type", "Type"))),
        "legal_status": _string(
            _first_value(item, ("legalStatus", "LatestLegalStatus", "LegalStatus"))
        ),
        "main_ipc": normalized_main_ipc(item),
    }


def _non_patent_references(raw: str, text: str) -> list:
    # raw 与 text 可能是同一内容的两个历史字段，重复时只保留一次。
    values = []
    if raw:
        values.append(raw)
    if text and text != raw:
        values.append(text)
    return values


def _string(value) -> str:
    # 引证兼容字段保留原始文本表示，None 统一为空而不输出 Python None 字符串。
    if value is None:
        return ""
    return str(value)


def _first_value(item: dict, keys: tuple):
    # 以第一个 truthy 值实现跨版本字段回退；空值不会遮挡后续别名。
    for key in keys:
        value = item.get(key)
        if value:
            return value
    return None


def _document_number(item: dict) -> str:
    """构造供摘要回退使用的文献编号，兼容拆分存放的国家和 kind 码。

    已包含国家前缀或 kind 后缀的编号不会重复拼接；没有任何主编号时返回空串，
    由上层决定是否仍保留该摘要。这一逻辑只服务显示/关联回退，不修改原始字段。
    """
    # 供应商可能把 country/kind 分开给出，这里只在缺失时补前后缀，避免重复拼接。
    value = _first_value(
        item,
        (
            "documentNumber",
            "DocumentNumber",
            "PublicationNumber",
            "publicationNumber",
            "DocNumber",
            "docNumber",
        ),
    )
    if not value:
        return ""

    document_number = str(value)
    country = _string(_first_value(item, ("Country", "country")))
    kind = _string(_first_value(item, ("Kind", "kind")))

    if country and not document_number.upper().startswith(country.upper()):
        document_number = f"{country}{document_number}"
    if kind and not document_number.upper().endswith(kind.upper()):
        document_number = f"{document_number}{kind}"
    return document_number


def _array(value) -> list:
    # 引证字段同样兼容单对象和列表。
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]
