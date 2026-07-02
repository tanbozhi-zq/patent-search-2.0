from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "patent-search-service"
    service_host: str = "0.0.0.0"
    service_port: int = 8000

    enable_auth: bool = True
    api_token: str = Field(default="", repr=False)

    opensearch_host: str = "opensearch-o-00gcv9almneh.escloud.volces.com"
    opensearch_port: int = 9200
    opensearch_use_https: bool = True
    opensearch_user: str = ""
    opensearch_pass: str = Field(default="", repr=False)
    opensearch_index: str = "patent_index"
    opensearch_verify_certs: bool = False
    opensearch_timeout_seconds: int = 30

    @property
    def opensearch_url(self) -> str:
        scheme = "https" if self.opensearch_use_https else "http"
        return f"{scheme}://{self.opensearch_host}:{self.opensearch_port}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
