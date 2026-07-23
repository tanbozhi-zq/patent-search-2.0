from app.mappings.legal_history_mapper import map_legal_history_response


def test_maps_legal_history_base_response_without_history_data():
    mapped = map_legal_history_response({"_source": {"patent_id": "cn-1"}})

    assert mapped == {
        "patent_id": "cn-1",
        "transaction_count": 0,
        "transactions": [],
    }


def test_maps_legal_history_transactions_when_available():
    mapped = map_legal_history_response(
        {
            "_source": {
                "patent_id": "cn-1",
                "LegalStatusHistory": [{"date": "2020-01-01", "type": "授权"}],
            }
        }
    )

    assert mapped["transaction_count"] == 1
    assert mapped["transactions"] == [{"date": "2020-01-01", "type": "授权"}]
