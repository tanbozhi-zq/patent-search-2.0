import os
from dataclasses import dataclass


@dataclass
class McpServerSettings:
    base_url: str = "http://127.0.0.1:8000"
    api_token: str = ""
    timeout_seconds: int = 30
    page_size_limit: int = 50

    @classmethod
    def from_env(cls) -> "McpServerSettings":
        return cls(
            base_url=os.getenv("PATENT_SEARCH_BASE_URL", "http://127.0.0.1:8000"),
            api_token=os.getenv("PATENT_SEARCH_API_TOKEN", ""),
            timeout_seconds=_env_int("PATENT_SEARCH_TIMEOUT_SECONDS", 30),
            page_size_limit=_env_int("PATENT_SEARCH_PAGE_SIZE_LIMIT", 50),
        )


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default
