from app.mappings.citation_mapper import map_citations_response


def test_map_citations_response_returns_tool_and_raw_compat_fields():
    hit = {
        "_source": {
            "patent_id": "cn-1",
            "ReferencesCited": [
                {
                    "id": "cn-ref",
                    "title": "引用专利",
                    "applicant": "引用申请人",
                    "applicationDate": "2020-01-01",
                    "applicationNumber": "CN2020000001",
                    "type": "发明专利",
                    "legalStatus": "有效",
                    "mainIpc": "G06F",
                }
            ],
            "ReferencesCitedRaw": "非专利文献 A",
            "ReferencesCitedText": "非专利文献 B",
            "RelatedDocuments": [
                {
                    "id": "cn-cited-by",
                    "title": "被引专利",
                    "applicant": "被引申请人",
                    "applicationDate": "2021-01-01",
                    "applicationNumber": "CN2021000001",
                    "type": "实用新型",
                    "legalStatus": "授权",
                    "mainIpc": "H02M",
                }
            ],
        }
    }

    mapped = map_citations_response(hit)

    assert mapped["patent_id"] == "cn-1"
    assert mapped["referencesCited"] == hit["_source"]["ReferencesCited"]
    assert mapped["referencesCitedRaw"] == "非专利文献 A"
    assert mapped["referencesCitedText"] == "非专利文献 B"
    assert mapped["relatedDocuments"] == hit["_source"]["RelatedDocuments"]
    assert mapped["patent_references"][0] == {
        "id": "cn-ref",
        "title": "引用专利",
        "applicant": "引用申请人",
        "application_date": "2020-01-01",
        "application_number": "CN2020000001",
        "type": "发明专利",
        "legal_status": "有效",
        "main_ipc": "G06F",
    }
    assert mapped["cited_by"][0]["id"] == "cn-cited-by"
    assert mapped["non_patent_references"] == ["非专利文献 A", "非专利文献 B"]


def test_map_citations_response_handles_missing_fields():
    mapped = map_citations_response({"_source": {"patent_id": "cn-empty"}})

    assert mapped == {
        "patent_id": "cn-empty",
        "cited_by": [],
        "patent_references": [],
        "non_patent_references": [],
        "referencesCited": [],
        "referencesCitedRaw": "",
        "referencesCitedText": "",
        "relatedDocuments": [],
    }


def test_map_citations_response_summarizes_raw_references_cited_doc_number():
    hit = {
        "_source": {
            "patent_id": "cn-490997baf50c646d",
            "ReferencesCited": [
                {
                    "DocNumber": "112501955",
                    "Kind": "A",
                    "Country": "CN",
                    "Date": "2021-03-16",
                },
                {
                    "DocNumber": "112501955",
                    "Kind": "A",
                    "Country": "CN",
                    "Date": "2021-03-16",
                },
                {
                    "Unknown": "value",
                },
            ],
        }
    }

    mapped = map_citations_response(hit)

    assert mapped["referencesCited"] == hit["_source"]["ReferencesCited"]
    assert mapped["patent_references"] == [
        {
            "id": "CN112501955A",
            "title": "",
            "applicant": "",
            "application_date": "2021-03-16",
            "application_number": "",
            "type": "",
            "legal_status": "",
            "main_ipc": "",
        }
    ]


def test_map_citations_response_summarizes_related_documents_doc_number():
    hit = {
        "_source": {
            "patent_id": "cn-1",
            "RelatedDocuments": [
                {
                    "DocNumber": "115629104",
                    "Kind": "A",
                    "Country": "CN",
                    "Date": "2023-01-20",
                }
            ],
        }
    }

    mapped = map_citations_response(hit)

    assert mapped["relatedDocuments"] == hit["_source"]["RelatedDocuments"]
    assert mapped["cited_by"][0]["id"] == "CN115629104A"
    assert mapped["cited_by"][0]["application_date"] == "2023-01-20"
