from app.core.config import Settings


def test_settings_defaults_match_stage_four_contract():
    settings = Settings()

    assert settings.service_name == "patent-search-service"
    assert settings.service_host == "0.0.0.0"
    assert settings.service_port == 8000
    assert settings.enable_auth is True
    assert settings.opensearch_port == 9200
    assert settings.opensearch_use_https is True
    assert settings.opensearch_index == "patent_index"
    assert settings.opensearch_verify_certs is False


def test_opensearch_url_uses_https_when_enabled():
    settings = Settings(
        opensearch_host="example.com",
        opensearch_port=9200,
        opensearch_use_https=True,
    )

    assert settings.opensearch_url == "https://example.com:9200"
