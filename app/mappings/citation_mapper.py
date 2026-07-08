def map_citations_response(hit: dict) -> dict:
    source = hit.get("_source", {})
    references_cited = _array(source.get("ReferencesCited"))
    related_documents = _array(source.get("RelatedDocuments"))
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
        "main_ipc": _string(_first_value(item, ("mainIpc", "IPC", "ipc"))),
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


def _first_value(item: dict, keys: tuple):
    for key in keys:
        value = item.get(key)
        if value:
            return value
    return None


def _document_number(item: dict) -> str:
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
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]
