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


def test_mcp_settings_read_access_token(monkeypatch):
    monkeypatch.setenv("MCP_ACCESS_TOKEN", "mcp-secret")

    settings = McpServerSettings.from_env()

    assert settings.access_token == "mcp-secret"


def test_mcp_settings_require_access_token_for_http():
    settings = McpServerSettings(access_token="  mcp-secret  ")

    assert settings.require_access_token() == "mcp-secret"


def test_mcp_settings_reject_empty_access_token_for_http():
    settings = McpServerSettings(access_token=" ")

    try:
        settings.require_access_token()
    except RuntimeError as exc:
        assert str(exc) == "MCP_ACCESS_TOKEN is required when --transport http is used"
    else:
        raise AssertionError("empty MCP_ACCESS_TOKEN should fail HTTP startup")
