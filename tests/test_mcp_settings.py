"""验证 MCP 独立进程的环境配置、安全默认值与 HTTP Token 启动门槛。"""

import pytest

from app.core.admin_config import config_definition
from app.core.timing_contract import MIN_MCP_BACKEND_TIMEOUT_SECONDS
from mcp_server.patent_api_client import PatentApiClient
from mcp_server.settings import DEFAULT_MCP_MAX_CONCURRENT_TOOLS, McpServerSettings


def test_mcp_settings_read_patent_search_environment(monkeypatch):
    monkeypatch.setenv("PATENT_SEARCH_BASE_URL", "http://api")
    monkeypatch.setenv("PATENT_SEARCH_API_TOKEN", "token")
    monkeypatch.setenv("PATENT_SEARCH_TIMEOUT_SECONDS", "300")

    settings = McpServerSettings.from_env()

    assert settings.base_url == "http://api"
    assert settings.api_token == "token"
    assert settings.timeout_seconds == 300


def test_mcp_settings_use_safe_defaults(monkeypatch):
    monkeypatch.delenv("PATENT_SEARCH_BASE_URL", raising=False)
    monkeypatch.delenv("PATENT_SEARCH_API_TOKEN", raising=False)
    monkeypatch.delenv("PATENT_SEARCH_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("MCP_MAX_CONCURRENT_TOOLS", raising=False)

    settings = McpServerSettings.from_env()

    assert settings.base_url == "http://127.0.0.1:8000"
    assert settings.api_token == ""
    assert settings.timeout_seconds == MIN_MCP_BACKEND_TIMEOUT_SECONDS
    assert settings.max_concurrent_tools == DEFAULT_MCP_MAX_CONCURRENT_TOOLS
    assert (
        config_definition("request.deadline_seconds").maximum
        < settings.timeout_seconds
    )


@pytest.mark.parametrize("timeout_seconds", (120, 240, 244))
def test_mcp_settings_reject_actual_timeout_without_backend_margin(
    monkeypatch,
    timeout_seconds,
):
    monkeypatch.setenv("PATENT_SEARCH_TIMEOUT_SECONDS", str(timeout_seconds))

    with pytest.raises(ValueError, match="must be at least 245 seconds"):
        McpServerSettings.from_env()


@pytest.mark.parametrize("timeout_seconds", (245, 300))
def test_mcp_settings_accept_actual_timeout_with_backend_margin(
    monkeypatch,
    timeout_seconds,
):
    monkeypatch.setenv("PATENT_SEARCH_TIMEOUT_SECONDS", str(timeout_seconds))

    assert McpServerSettings.from_env().timeout_seconds == timeout_seconds


def test_patent_api_client_rejects_short_timeout_on_real_startup_path(monkeypatch):
    monkeypatch.setenv("PATENT_SEARCH_TIMEOUT_SECONDS", "240")

    with pytest.raises(ValueError, match="must be at least 245 seconds"):
        PatentApiClient()


def test_mcp_settings_read_access_token(monkeypatch):
    monkeypatch.setenv("MCP_ACCESS_TOKEN", "mcp-secret")

    settings = McpServerSettings.from_env()

    assert settings.access_token == "mcp-secret"


def test_mcp_settings_read_max_concurrent_tools(monkeypatch):
    monkeypatch.setenv("MCP_MAX_CONCURRENT_TOOLS", "7")

    settings = McpServerSettings.from_env()

    assert settings.max_concurrent_tools == 7


@pytest.mark.parametrize("max_concurrent_tools", (0, -1, True))
def test_mcp_settings_reject_invalid_max_concurrent_tools(max_concurrent_tools):
    with pytest.raises(ValueError, match="MCP_MAX_CONCURRENT_TOOLS must be at least 1"):
        McpServerSettings(max_concurrent_tools=max_concurrent_tools)


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
