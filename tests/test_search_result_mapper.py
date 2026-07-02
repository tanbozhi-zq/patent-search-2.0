from app.mappings.result_mapper import map_search_response


def test_maps_search_response_to_vendor_like_shape():
    raw = {
        "hits": {
            "total": {"value": 1},
            "hits": [
                {
                    "_score": 12.3,
                    "_source": {
                        "patent_id": "cn-1",
                        "ApplicationNumber": "CN1",
                        "PublicationNumber": "CN1A",
                        "Title": "标题",
                        "Abstract": "摘要",
                        "Applicant": "申请人",
                        "Assignee": "权利人",
                        "Inventor": "发明人",
                        "IPC": "H02M",
                        "IPCList": ["H02M"],
                        "ApplicationDate": "2020-01-01",
                        "PublicationDate": "2020-02-01",
                        "LatestLegalStatus": "授权",
                        "Type": "发明专利",
                    },
                }
            ],
        }
    }

    mapped = map_search_response(raw, page=1, page_size=50)

    assert mapped["total"] == 1
    assert mapped["records"][0]["patent_id"] == "cn-1"
    assert mapped["records"][0]["title"] == "标题"
    assert mapped["records"][0]["ipcMainList"] == ["H02M"]
