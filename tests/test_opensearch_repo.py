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
