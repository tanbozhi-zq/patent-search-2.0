from mcp_server.settings import McpServerSettings


def test_mcp_settings_read_patent_search_environment(monkeypatch):
    monkeypatch.setenv("PATENT_SEARCH_BASE_URL", "http://api")
    monkeypatch.setenv("PATENT_SEARCH_API_TOKEN", "token")
    monkeypatch.setenv("PATENT_SEARCH_TIMEOUT_SECONDS", "7")

    settings = McpServerSettings.from_env()

    assert settings.base_url == "http://api"
    assert settings.api_token == "token"
    assert settings.timeout_seconds == 7


def test_mcp_settings_use_safe_defaults(monkeypatch):
    monkeypatch.delenv("PATENT_SEARCH_BASE_URL", raising=False)
    monkeypatch.delenv("PATENT_SEARCH_API_TOKEN", raising=False)
    monkeypatch.delenv("PATENT_SEARCH_TIMEOUT_SECONDS", raising=False)

    settings = McpServerSettings.from_env()

    assert settings.base_url == "http://127.0.0.1:8000"
    assert settings.api_token == ""
    assert settings.timeout_seconds == 30
