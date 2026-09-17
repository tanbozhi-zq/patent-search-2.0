"""验证详情 mapper 的 snake_case 输出、空值省略、图片与权利要求回退。"""

from app.mappings.detail_mapper import map_detail_response


def _hit(**overrides):
    source = {
        "patent_id": "cn-1",
        "ApplicationNumber": "CN202411108082.1",
        "PublicationNumber": "CN119188170B",
        "Title": "一种轴承座壳体的加工工艺",
        "Abstract": "本发明公开了一种加工工艺。",
        "Applicant": "某某公司",
        "FirstApplicant": "首位申请人",
        "Assignee": "当前权利人",
        "Inventor": "张三;李四",
        "FirstInventor": "张三",
        "ApplicantAddress": "北京市",
        "Agency": "某某代理机构",
        "Agent": "王五",
        "IPC": "H05K 5/03 (2006.01)I",
        "IPCSmallGroup": "H05K5/02",
        "IPCList": ["h05k 5/02 (2006.01)i", "B23P 15/00 (2006.01)", "invalid"],
        "MainClaim": "主权利要求",
        "IndependentClaimsCN": "中文独立权利要求",
        "Requirement": "完整权利要求书",
        "ApplicationDate": "2024-08-13",
        "PublicationDate": "2026-06-12",
        "LatestLegalStatus": "授权",
        "Type": "发明专利",
        "Priority": [
            {"ApplicationNumber": "CN202311111111.1"},
            {"ApplicationNumber": "CN202311111111.1"},
            {"ApplicationNumber": "CN202211111111.2"},
        ],
        "PCT": {
            "PCTApplicationDate": "2024-08-14",
            "PCTApplicationNumber": "PCT/CN2024/000001",
            "PCTPublicationNumber": "WO2025000001",
        },
        "AbstractFigureUrl": "https://example.invalid/abstract.gif",
        "PatentImage": "patent_packages/CN/CN119188170B/main.png",
        "PatentImages": [
            "patent_packages/CN/CN119188170B/main.png",
            "patent_packages/CN/CN119188170B/detail.jpg",
            "https://example.invalid/not-a-tos-image.png",
        ],
        "Family": ["family-1"],
        "Drawings": [{"file": "drawing.png"}],
        "PDFFiles": [{"object_key": "patent.pdf"}],
        "Instructions": "说明书正文",
    }
    source.update(overrides)
    return {"_source": source}


def test_detail_uses_exact_snake_case_business_contract():
    mapped = map_detail_response(_hit(), include_description=True)

    assert mapped == {
        "id": "cn-1",
        "application_number": "CN202411108082.1",
        "publication_number": "CN119188170B",
        "title": "一种轴承座壳体的加工工艺",
        "abstract": "本发明公开了一种加工工艺。",
        "applicant": "某某公司",
        "first_applicant": "首位申请人",
        "current_assignee": "当前权利人",
        "inventor": "张三;李四",
        "first_inventor": "张三",
        "applicant_address": "北京市",
        "agency": "某某代理机构",
        "agent": "王五",
        "main_ipc": "H05K5/02",
        "ipc_list": ["H05K5/02", "B23P15/00"],
        "main_claim": "主权利要求",
        "independent_claims": "中文独立权利要求",
        "claims": "完整权利要求书",
        "application_date": "2024-08-13",
        "publication_date": "2026-06-12",
        "legal_status": "授权",
        "type": "发明专利",
        "priority_numbers": ["CN202311111111.1", "CN202211111111.2"],
        "pct_application_date": "2024-08-14",
        "pct_application_number": "PCT/CN2024/000001",
        "pct_publication_number": "WO2025000001",
        "image_path": "patent_packages/CN/CN119188170B/main.png",
        "images": [
            "patent_packages/CN/CN119188170B/main.png",
            "patent_packages/CN/CN119188170B/detail.jpg",
        ],
        "family": ["family-1"],
        "description": "说明书正文",
    }
    assert "drawings" not in mapped
    assert "pdf_list" not in mapped
    assert "patent_id" not in mapped
    assert "mainIpc" not in mapped


def test_detail_omits_unavailable_optional_values_and_description_by_default():
    mapped = map_detail_response({"_source": {"patent_id": "cn-empty"}})
    requested = map_detail_response({"_source": {"patent_id": "cn-empty"}}, include_description=True)

    assert mapped == {"id": "cn-empty"}
    assert requested == {"id": "cn-empty"}


def test_detail_normalizes_array_text_fields_and_omits_missing_assignee():
    mapped = map_detail_response(
        _hit(
            Applicant=["鲁贝里股份公司"],
            FirstApplicant=["鲁贝里股份公司", "第二申请人"],
            Assignee=None,
            Inventor=["发明人甲", "发明人乙"],
            FirstInventor=["发明人甲"],
        )
    )

    assert mapped["applicant"] == "鲁贝里股份公司"
    assert mapped["first_applicant"] == "鲁贝里股份公司;第二申请人"
    assert mapped["inventor"] == "发明人甲;发明人乙"
    assert mapped["first_inventor"] == "发明人甲"
    assert "current_assignee" not in mapped


def test_detail_uses_external_abstract_figure_only_as_image_path_fallback():
    mapped = map_detail_response(
        _hit(
            PatentImage=None,
            PatentImages=[],
            AbstractFigureUrl="https://example.invalid/abstract.gif",
        )
    )

    assert mapped["image_path"] == "https://example.invalid/abstract.gif"
    assert "images" not in mapped


def test_detail_claim_and_type_fallbacks_use_formal_source_fields():
    mapped = map_detail_response(
        _hit(
            MainClaim=None,
            MainClaimCN="中文首权",
            IndependentClaimsCN=None,
            IndependentClaimsOriginal="原文独权",
            Requirement=None,
            RequirementCN="中文完整权利要求",
            Type=None,
            PatentTypeCode="2",
        )
    )

    assert mapped["main_claim"] == "中文首权"
    assert mapped["independent_claims"] == "原文独权"
    assert mapped["claims"] == "中文完整权利要求"
    assert mapped["type"] == "实用新型"
