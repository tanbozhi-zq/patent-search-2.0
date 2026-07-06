def map_citations_response(hit: dict) -> dict:
    source = hit.get("_source", {})
    references_cited = _array(source.get("ReferencesCited"))
    related_documents = _array(source.get("RelatedDocuments"))
    raw = _string(source.get("ReferencesCitedRaw"))
    text = _string(source.get("ReferencesCitedText"))

    return {
        "patent_id": _string(source.get("patent_id")),
        "cited_by": [_summarize_patent(item) for item in related_documents if isinstance(item, dict)],
        "patent_references": [
            _summarize_patent(item) for item in references_cited if isinstance(item, dict)
        ],
        "non_patent_references": _non_patent_references(raw, text),
        "referencesCited": references_cited,
        "referencesCitedRaw": raw,
        "referencesCitedText": text,
        "relatedDocuments": related_documents,
    }


def _summarize_patent(item: dict) -> dict:
    return {
        "id": _string(item.get("id") or item.get("patent_id")),
        "title": _string(item.get("title") or item.get("Title")),
        "applicant": _string(item.get("applicant") or item.get("Applicant")),
        "application_date": _string(item.get("applicationDate") or item.get("ApplicationDate")),
        "application_number": _string(item.get("applicationNumber") or item.get("ApplicationNumber")),
        "type": _string(item.get("type") or item.get("Type")),
        "legal_status": _string(item.get("legalStatus") or item.get("LatestLegalStatus") or item.get("LegalStatus")),
        "main_ipc": _string(item.get("mainIpc") or item.get("IPC")),
    }


def _non_patent_references(raw: str, text: str) -> list:
    values = []
    if raw:
        values.append(raw)
    if text and text != raw:
        values.append(text)
    return values


def _string(value) -> str:
    if value is None:
        return ""
    return str(value)


def _array(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]
