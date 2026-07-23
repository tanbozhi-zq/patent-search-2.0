from app.mappings.detail_mapper import map_detail_response


def _hit():
    return {
        "_source": {
            "patent_id": "cn-1",
            "ApplicationNumber": "CN202411108082.1",
            "PublicationNumber": "CN119188170B",
            "Title": "一种轴承座壳体的加工工艺",
            "Abstract": "本发明公开了一种加工工艺。",
            "Applicant": "某某公司",
            "FirstApplicant": "某某公司",
            "Assignee": "某某公司",
            "Inventor": "张三;李四",
            "FirstInventor": "张三",
            "ApplicantAddress": "北京市",
            "Agency": "某某代理机构",
            "Agent": "王五",
            "IPC": "B23P15/00",
            "IPCList": ["B23P15/00", "B23Q3/00"],
            "ApplicationDate": "2024-08-13",
            "PublicationDate": "2026-06-12",
            "LatestLegalStatus": "授权",
            "LegalStatus": "授权公告",
            "Type": "发明专利",
            "MainClaim": "一种轴承座壳体的加工工艺，其特征在于...",
            "Requirement": "1. 一种轴承座壳体的加工工艺...",
            "Instructions": "说明书正文",
            "Family": [{"id": "family-1"}],
            "Drawings": [{"url": "drawing.png"}],
            "AbstractFigureUrl": "figure.png",
            "PDFList": ["a.pdf"],
        }
    }


def test_map_detail_response_returns_camel_and_snake_case_fields():
    mapped = map_detail_response(_hit())

    assert mapped["id"] == "cn-1"
    assert mapped["patent_id"] == "cn-1"
    assert mapped["applicationNumber"] == "CN202411108082.1"
    assert mapped["application_number"] == "CN202411108082.1"
    assert mapped["documentNumber"] == "CN119188170B"
    assert mapped["document_number"] == "CN119188170B"
    assert mapped["title"] == "一种轴承座壳体的加工工艺"
    assert mapped["ti"] == "一种轴承座壳体的加工工艺"
    assert mapped["abstract"] == "本发明公开了一种加工工艺。"
    assert mapped["summary"] == "本发明公开了一种加工工艺。"
    assert mapped["currentAssignee"] == "某某公司"
    assert mapped["current_assignee"] == "某某公司"
    assert mapped["mainIpc"] == "B23P15/00"
    assert mapped["main_ipc"] == "B23P15/00"
    assert mapped["legalStatus"] == "授权"
    assert mapped["legal_status"] == "授权"
    assert mapped["claims"] == "1. 一种轴承座壳体的加工工艺..."
    assert mapped["mainClaim"] == "一种轴承座壳体的加工工艺，其特征在于..."
    assert mapped["main_claim"] == "一种轴承座壳体的加工工艺，其特征在于..."


def test_map_detail_response_omits_description_by_default():
    mapped = map_detail_response(_hit())

    assert "description" not in mapped


def test_map_detail_response_includes_description_when_requested():
    mapped = map_detail_response(_hit(), include_description=True)

    assert mapped["description"] == "说明书正文"


def test_map_detail_response_uses_empty_values_for_missing_fields():
    mapped = map_detail_response({"_source": {"patent_id": "cn-empty"}})

    assert mapped["id"] == "cn-empty"
    assert mapped["applicationNumber"] == ""
    assert mapped["application_number"] == ""
    assert mapped["ipcMainList"] == []
    assert mapped["family"] == []
    assert mapped["drawings"] == []
    assert mapped["claims"] == ""
