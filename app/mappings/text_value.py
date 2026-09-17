"""上游文本字段有时是标量、有时是数组。对外响应统一成字符串，数组按来源顺序
用分号拼接，避免 Python repr（带引号/括号）成为 API 的偶然格式。
"""

def normalized_text(value: object) -> str:
    """返回稳定文本，不用 Python repr 序列化数组。"""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        values = (normalized_text(item).strip() for item in value)
        return ";".join(item for item in values if item)
    return str(value)
