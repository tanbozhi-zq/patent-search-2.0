def map_detail_response(hit: dict, include_description: bool = False) -> dict:
    source = hit.get("_source", {})
    patent_id = _string(
        source.get("patent_id")
        or source.get("PublicationNumber")
        or source.get("ApplicationNumber")
    )
    title = _string(source.get("Title"))
    abstract = _string(source.get("Abstract"))
    application_number = _string(source.get("ApplicationNumber"))
    document_number = _string(source.get("PublicationNumber"))
    application_date = _string(source.get("ApplicationDate"))
    document_date = _string(source.get("PublicationDate"))
    legal_status = _string(source.get("LatestLegalStatus") or source.get("LegalStatus"))
    current_status = _string(source.get("LatestLegalStatus"))
    current_assignee = _string(source.get("Assignee") or source.get("Applicant"))
    main_ipc = _string(source.get("IPC"))
    main_claim = _string(source.get("MainClaim"))

    response = {
        "id": patent_id,
        "patent_id": patent_id,
        "title": title,
        "ti": title,
        "abstract": abstract,
        "ab": abstract,
        "summary": abstract,
        "applicationNumber": application_number,
        "application_number": application_number,
        "documentNumber": document_number,
        "document_number": document_number,
        "applicationDate": application_date,
        "application_date": application_date,
        "documentDate": document_date,
        "document_date": document_date,
        "type": _string(source.get("Type")),
        "legalStatus": legal_status,
        "legal_status": legal_status,
        "currentStatus": current_status,
        "current_status": current_status,
        "applicant": _string(source.get("Applicant")),
        "firstApplicant": _string(source.get("FirstApplicant")),
        "first_applicant": _string(source.get("FirstApplicant")),
        "currentAssignee": current_assignee,
        "current_assignee": current_assignee,
        "assignee": _string(source.get("Assignee")),
        "inventor": _string(source.get("Inventor")),
        "firstInventor": _string(source.get("FirstInventor")),
        "first_inventor": _string(source.get("FirstInventor")),
        "applicantAddress": _string(source.get("ApplicantAddress")),
        "applicant_address": _string(source.get("ApplicantAddress")),
        "agency": _string(source.get("Agency")),
        "agent": _string(source.get("Agent")),
        "ipc": main_ipc,
        "mainIpc": main_ipc,
        "main_ipc": main_ipc,
        "ipcMainList": _array(source.get("IPCList")),
        "ipc_main_list": _array(source.get("IPCList")),
        "loc": _string(source.get("LOC")),
        "priorityNumber": _string(source.get("PriorityNumber")),
        "priority_number": _string(source.get("PriorityNumber")),
        "fullPriorityNumber": _string(source.get("FullPriorityNumber")),
        "full_priority_number": _string(source.get("FullPriorityNumber")),
        "pctDate": _string(source.get("PCTDate")),
        "pct_date": _string(source.get("PCTDate")),
        "pctApplicationData": _string(source.get("PCTApplicationData")),
        "pct_application_data": _string(source.get("PCTApplicationData")),
        "pctPublicationData": _string(source.get("PCTPublicationData")),
        "pct_publication_data": _string(source.get("PCTPublicationData")),
        "imagePath": _string(source.get("AbstractFigureUrl") or source.get("ImagePath")),
        "image_path": _string(source.get("AbstractFigureUrl") or source.get("ImagePath")),
        "pdfList": _array(source.get("PDFList") or source.get("pdfList")),
        "pdf_list": _array(source.get("PDFList") or source.get("pdfList")),
        "family": _first_array(source, ("Family", "SimpleFamily", "ExtendedFamily", "DocDBFamily")),
        "drawings": _first_array(source, ("Drawings", "DescriptionImages")),
        "legalStatusHistory": _array(source.get("LegalStatusHistory") or source.get("LegalStatus")),
        "legal_status_history": _array(source.get("LegalStatusHistory") or source.get("LegalStatus")),
        "mainClaim": main_claim,
        "main_claim": main_claim,
        "claims": _string(source.get("Requirement")),
    }

    if include_description:
        response["description"] = _string(source.get("Instructions"))

    return response


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


def _first_array(source: dict, fields: tuple) -> list:
    for field in fields:
        value = source.get(field)
        if value:
            return _array(value)
    return []