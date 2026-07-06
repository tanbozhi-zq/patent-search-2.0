def map_search_response(raw: dict, page: int, page_size: int) -> dict:
    hits = raw.get("hits", {})
    total = _extract_total(hits.get("total", 0))
    records = [_map_record(hit) for hit in hits.get("hits", [])]

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "records": records,
    }


def _extract_total(total):
    if isinstance(total, dict):
        return total.get("value", 0)
    return total or 0


def _map_record(hit: dict) -> dict:
    source = hit.get("_source", {})
    patent_id = _string(source.get("patent_id"))
    application_number = _string(source.get("ApplicationNumber"))
    document_number = _string(source.get("PublicationNumber"))
    title = _string(source.get("Title"))
    abstract = _string(source.get("Abstract"))
    applicant = _string(source.get("Applicant"))
    main_ipc = _string(source.get("IPC"))
    application_date = _string(source.get("ApplicationDate"))
    document_date = _string(source.get("PublicationDate"))
    legal_status = _string(source.get("LatestLegalStatus") or source.get("LegalStatus"))

    return {
        "id": patent_id,
        "patent_id": patent_id,
        "applicationNumber": application_number,
        "documentNumber": document_number,
        "title": title,
        "ti": title,
        "abstract": abstract,
        "ab": abstract,
        "applicant": applicant,
        "pa": applicant,
        "currentAssignee": _string(source.get("Assignee") or source.get("Applicant")),
        "inventor": _string(source.get("Inventor")),
        "mainIpc": main_ipc,
        "ipcMainList": _array(source.get("IPCList")),
        "applicationDate": application_date,
        "ad": application_date,
        "documentDate": document_date,
        "legalStatus": legal_status,
        "application_number": application_number,
        "document_number": document_number,
        "application_date": application_date,
        "document_date": document_date,
        "legal_status": legal_status,
        "main_ipc": main_ipc,
        "currentStatus": _string(source.get("LatestLegalStatus")),
        "type": _string(source.get("Type")),
        "score": hit.get("_score"),
    }


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
