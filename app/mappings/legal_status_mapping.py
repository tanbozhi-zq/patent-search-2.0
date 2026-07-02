LEGAL_STATUS_KEYWORDS = {
    "有效专利": ["授权", "有效"],
    "在审": ["公开", "实质审查"],
    "失效": ["终止", "届满", "撤回", "驳回"],
}


def build_legal_status_clause(value: str) -> dict:
    keywords = LEGAL_STATUS_KEYWORDS.get(value)
    if keywords:
        return {
            "bool": {
                "should": [{"match": {"LatestLegalStatus": keyword}} for keyword in keywords],
                "minimum_should_match": 1,
            }
        }

    return {
        "multi_match": {
            "query": value,
            "fields": ["LatestLegalStatus", "LegalStatus"],
        }
    }
