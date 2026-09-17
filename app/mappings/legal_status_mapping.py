"""业务集合映射为多个精确 LatestLegalStatus term；其余输入同时兼容当前状态和
历史 LegalStatus 字段，避免把状态文本交给 analyzer 产生模糊命中。
"""

LEGAL_STATUS_KEYWORDS = {
    "有效专利": ["授权", "有效"],
    "在审": ["公开", "实质审查"],
    "失效": ["终止", "届满", "撤回", "驳回"],
}


def build_legal_status_clause(value: str) -> dict:
    # “有效/在审/失效”是产品语义集合，普通值则按用户给出的状态精确匹配。
    keywords = LEGAL_STATUS_KEYWORDS.get(value)
    if keywords:
        return {
            "bool": {
                "should": [{"term": {"LatestLegalStatus": keyword}} for keyword in keywords],
                "minimum_should_match": 1,
            }
        }

    return {
        "bool": {
            "should": [
                {"term": {"LatestLegalStatus": value}},
                {"term": {"LegalStatus": value}},
            ],
            "minimum_should_match": 1,
        }
    }
