from app.core.config import Settings
from app.repositories.opensearch_repo import OpenSearchRepository


def test_repository_stores_index_name():
    settings = Settings(opensearch_index="patent_index")
    repository = OpenSearchRepository(settings=settings)

    assert repository.index_name == "patent_index"


def test_repository_builds_client_with_configured_url():
    settings = Settings(
        opensearch_host="example.com",
        opensearch_port=9200,
        opensearch_use_https=True,
        opensearch_user="user",
        opensearch_pass="pass",
        opensearch_verify_certs=False,
        opensearch_timeout_seconds=15,
    )
    repository = OpenSearchRepository(settings=settings)

    assert repository.hosts == ["https://example.com:9200"]
    assert repository.http_auth == ("user", "pass")
    assert repository.verify_certs is False
    assert repository.timeout == 15


class FakeSearchClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def search(self, index, body):
        self.calls.append({"index": index, "body": body})
        return self.responses.pop(0)


def test_repository_get_patent_by_identifier_uses_patent_id_first():
    settings = Settings(opensearch_index="patent_index")
    repository = OpenSearchRepository(settings=settings)
    repository.client = FakeSearchClient(
        [
            {
                "hits": {
                    "hits": [
                        {
                            "_id": "1",
                            "_source": {
                                "patent_id": "cn-1",
                                "PublicationNumber": "CN1A",
                            },
                        }
                    ]
                }
            }
        ]
    )

    hit = repository.get_patent_by_identifier("cn-1")

    assert hit["_source"]["patent_id"] == "cn-1"
    assert repository.client.calls[0]["index"] == "patent_index"
    assert repository.client.calls[0]["body"]["size"] == 1
    assert repository.client.calls[0]["body"]["query"] == {"term": {"patent_id": "cn-1"}}


def test_repository_get_patent_by_identifier_falls_back_to_publication_number():
    settings = Settings(opensearch_index="patent_index")
    repository = OpenSearchRepository(settings=settings)
    repository.client = FakeSearchClient(
        [
            {"hits": {"hits": []}},
            {
                "hits": {
                    "hits": [
                        {
                            "_id": "2",
                            "_source": {
                                "patent_id": "cn-2",
                                "PublicationNumber": "CN2A",
                            },
                        }
                    ]
                }
            },
        ]
    )

    hit = repository.get_patent_by_identifier("CN2A")

    assert hit["_source"]["patent_id"] == "cn-2"
    assert repository.client.calls[0]["body"]["query"] == {"term": {"patent_id": "CN2A"}}
    assert repository.client.calls[1]["body"]["query"] == {"term": {"PublicationNumber": "CN2A"}}


def test_repository_get_patent_by_identifier_falls_back_to_application_number():
    settings = Settings(opensearch_index="patent_index")
    repository = OpenSearchRepository(settings=settings)
    repository.client = FakeSearchClient(
        [
            {"hits": {"hits": []}},
            {"hits": {"hits": []}},
            {
                "hits": {
                    "hits": [
                        {
                            "_id": "3",
                            "_source": {
                                "patent_id": "cn-3",
                                "ApplicationNumber": "CN2024000001",
                            },
                        }
                    ]
                }
            },
        ]
    )

    hit = repository.get_patent_by_identifier("CN2024000001")

    assert hit["_source"]["patent_id"] == "cn-3"
    assert repository.client.calls[2]["body"]["query"] == {"term": {"ApplicationNumber": "CN2024000001"}}


def test_repository_get_patent_by_identifier_returns_none_when_not_found():
    settings = Settings(opensearch_index="patent_index")
    repository = OpenSearchRepository(settings=settings)
    repository.client = FakeSearchClient(
        [
            {"hits": {"hits": []}},
            {"hits": {"hits": []}},
            {"hits": {"hits": []}},
        ]
    )

    hit = repository.get_patent_by_identifier("missing")

    assert hit is None
    assert len(repository.client.calls) == 3
