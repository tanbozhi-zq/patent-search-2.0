import os
from dataclasses import dataclass


@dataclass
class McpServerSettings:
    base_url: str = "http://127.0.0.1:8000"
    api_token: str = ""
    timeout_seconds: int = 30
    page_size_limit: int = 50
    access_token: str = ""

    @classmethod
    def from_env(cls) -> "McpServerSettings":
        return cls(
            base_url=os.getenv("PATENT_SEARCH_BASE_URL", "http://127.0.0.1:8000"),
            api_token=os.getenv("PATENT_SEARCH_API_TOKEN", ""),
            timeout_seconds=_env_int("PATENT_SEARCH_TIMEOUT_SECONDS", 30),
            page_size_limit=_env_int("PATENT_SEARCH_PAGE_SIZE_LIMIT", 50),
            access_token=os.getenv("MCP_ACCESS_TOKEN", ""),
        )

    def require_access_token(self) -> str:
        token = self.access_token.strip()
        if not token:
            raise RuntimeError("MCP_ACCESS_TOKEN is required when --transport http is used")
        return token


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default
