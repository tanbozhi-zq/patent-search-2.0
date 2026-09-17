"""详情 mapper 将 v2 source 结构投影为 snake_case 业务响应。它只添加可靠非空值，
并把图片、PCT、Priority 等历史嵌套结构转换成稳定的简单字段。
"""

from app.mappings.ipc_mapper import normalized_ipc_list, normalized_main_ipc
from app.mappings.patent_type_mapper import normalized_patent_type
from app.mappings.text_value import normalized_text


def map_detail_response(hit: dict, include_description: bool = False) -> dict:
    """把一条 OpenSearch hit 转换为详情接口的稀疏业务对象。

    详情响应与搜索列表不同：只有经归一化后仍可靠的值才会输出，以免用空字符串
    伪装未知事实。函数还定义了专利 ID、多语言权利要求、图片、Priority 与 PCT
    字段的回退顺序；说明书是唯一按调用方显式开关加入的较大字段。
    """
    # patent_id 缺失时按公开号/申请号回退，确保详情对象至少有可关联的 id。
    source = hit.get("_source", {})
    patent_id = normalized_text(
        source.get("patent_id")
        or source.get("PublicationNumber")
        or source.get("ApplicationNumber")
    )
    response = {"id": patent_id}

    _add_if_present(response, "application_number", normalized_text(source.get("ApplicationNumber")))
    _add_if_present(response, "publication_number", normalized_text(source.get("PublicationNumber")))
    _add_if_present(response, "title", normalized_text(source.get("Title")))
    _add_if_present(response, "abstract", normalized_text(source.get("Abstract")))
    _add_if_present(response, "applicant", normalized_text(source.get("Applicant")))
    _add_if_present(response, "first_applicant", normalized_text(source.get("FirstApplicant")))
    _add_if_present(response, "current_assignee", normalized_text(source.get("Assignee")))
    _add_if_present(response, "inventor", normalized_text(source.get("Inventor")))
    _add_if_present(response, "first_inventor", normalized_text(source.get("FirstInventor")))
    _add_if_present(response, "applicant_address", normalized_text(source.get("ApplicantAddress")))
    _add_if_present(response, "agency", normalized_text(source.get("Agency")))
    _add_if_present(response, "agent", normalized_text(source.get("Agent")))

    _add_if_present(response, "main_ipc", normalized_main_ipc(source))
    _add_if_present(response, "ipc_list", normalized_ipc_list(source.get("IPCList")))
    _add_if_present(response, "main_claim", _first_string(source, ("MainClaim", "MainClaimCN", "MainClaimEN")))
    _add_if_present(
        response,
        "independent_claims",
        _first_string(source, ("IndependentClaimsCN", "IndependentClaimsOriginal", "IndependentClaimsEN")),
    )
    _add_if_present(response, "claims", _first_string(source, ("Requirement", "RequirementCN", "RequirementEN")))
    _add_if_present(response, "application_date", normalized_text(source.get("ApplicationDate")))
    _add_if_present(response, "publication_date", normalized_text(source.get("PublicationDate")))
    _add_if_present(response, "legal_status", normalized_text(source.get("LatestLegalStatus") or source.get("LegalStatus")))
    _add_if_present(response, "type", normalized_patent_type(source))

    _add_if_present(response, "priority_numbers", _priority_numbers(source.get("Priority")))
    _add_if_present(response, "pct_application_date", _pct_value(source, "PCTApplicationDate"))
    _add_if_present(response, "pct_application_number", _pct_value(source, "PCTApplicationNumber"))
    _add_if_present(response, "pct_publication_number", _pct_value(source, "PCTPublicationNumber"))

    # TOS 图片优先作为 image_path；只有没有内部图片时才允许使用外部摘要图 URL。
    images = _tos_images(source)
    _add_if_present(response, "image_path", images[0] if images else _external_image_url(source))
    _add_if_present(response, "images", images)
    _add_if_present(response, "family", _first_array(source, ("Family", "SimpleFamily", "ExtendedFamily", "DocDBFamily")))

    if include_description:
        _add_if_present(response, "description", normalized_text(source.get("Instructions")))
    return response


def _add_if_present(response: dict, key: str, value: object) -> None:
    # 详情契约要求“不可靠/空值就省略”，与搜索列表的固定字段集不同。
    if _present(value):
        response[key] = value


def _present(value: object) -> bool:
    # 空字符串、空数组和空对象都视为缺失；数字/布尔值等非字符串值保留。
    if value is None or value == [] or value == {}:
        return False
    return not isinstance(value, str) or bool(value.strip())


def _array(value: object) -> list:
    # 兼容供应商把单值和列表交替返回的情况，统一成便于后续遍历的列表。
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _first_array(source: dict, fields: tuple[str, ...]) -> list:
    # 家族字段按 v2/历史来源顺序取第一个非空数组，不合并不同家族模型，避免重复。
    for field in fields:
        values = _array(source.get(field))
        if _present(values):
            return values
    return []


def _first_string(source: dict, fields: tuple[str, ...]) -> str:
    # 多语言字段按参数顺序选择第一份可靠文本；该顺序就是对外的回退契约。
    for field in fields:
        value = normalized_text(source.get(field))
        if _present(value):
            return value
    return ""


def _priority_numbers(value: object) -> list[str]:
    """从历史 Priority 混合结构中提取去重后的申请号列表。

    索引里可能混入空值、非对象或不完整对象；这些条目被跳过而不影响整个详情响应。
    输出按源数据首次出现的顺序排列，避免排序规则随实现细节改变。
    """
    # Priority 只输出申请号并去重；异常条目不阻断整件专利详情。
    numbers = []
    for item in _array(value):
        if not isinstance(item, dict):
            continue
        number = normalized_text(item.get("ApplicationNumber")).strip()
        if number and number not in numbers:
            numbers.append(number)
    return numbers


def _pct_value(source: dict, field: str) -> str:
    # v2 可能把 PCT 字段平铺，也可能放在 PCT 对象中，先平铺后嵌套回退。
    value = source.get(field)
    if _present(value):
        return normalized_text(value)
    pct = source.get("PCT")
    if isinstance(pct, dict):
        return normalized_text(pct.get(field))
    return ""


def _tos_images(source: dict) -> list[str]:
    # PatentImage/PatentImages 只接受内部路径，不把外部 URL 混进 images 数组；
    # 同一图片出现在两个来源时按首次出现顺序去重。
    images = []
    for value in (*_array(source.get("PatentImage")), *_array(source.get("PatentImages"))):
        image = normalized_text(value).strip()
        if image and not _is_external_url(image) and image not in images:
            images.append(image)
    return images


def _external_image_url(source: dict) -> str:
    # 外部摘要图只作为没有内部 TOS 图片时的主图回退，不进入 images 列表。
    value = normalized_text(source.get("AbstractFigureUrl")).strip()
    return value if _is_external_url(value) else ""


def _is_external_url(value: str) -> bool:
    # 识别绝对/协议相对 URL，避免把外链误当成内部存储路径。
    return value.lower().startswith(("http://", "https://", "//"))
