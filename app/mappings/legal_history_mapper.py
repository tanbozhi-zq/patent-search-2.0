"""法律历史是少数有意保留上游交易条目结构的响应；mapper 只确定 id、计数和列表，
不擅自重命名或裁剪交易内部字段。
"""

def map_legal_history_response(hit: dict) -> dict:
    # id 依次回退到内部 id、公开号、申请号，确保空 source 仍有稳定的字符串键。
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
    # 非列表值视为空历史，不把单个异常对象包装成一条“交易”。
    value = source.get("LegalStatusHistory")
    if isinstance(value, list):
        return value
    return []


def _string(value) -> str:
    # 与其他 mapper 保持 None → 空字符串的兼容处理。
    if value is None:
        return ""
    return str(value)
