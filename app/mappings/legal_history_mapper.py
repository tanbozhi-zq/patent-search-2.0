def map_legal_history_response(hit: dict) -> dict:
    source = hit.get("_source", {})
    patent_id = _string(
        source.get("patent_id")
        or source.get("PublicationNumber")
        or source.get("ApplicationNumber")
    )
    transactions = _transactions(source)
    return {
        "patent_id": patent_id,
        "transaction_count": len(transactions),
        "transactions": transactions,
    }


def _transactions(source: dict) -> list:
    value = source.get("LegalStatusHistory")
    if isinstance(value, list):
        return value
    return []


def _string(value) -> str:
    if value is None:
        return ""
    return str(value)
