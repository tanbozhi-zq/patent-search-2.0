from typing import Optional, Tuple

from opensearchpy import OpenSearch

from app.core.config import Settings, get_settings


class OpenSearchRepository:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.index_name = self.settings.opensearch_index
        self.hosts = [self.settings.opensearch_url]
        self.http_auth = self._build_http_auth()
        self.verify_certs = self.settings.opensearch_verify_certs
        self.timeout = self.settings.opensearch_timeout_seconds
        self.client = self._build_client()

    def _build_http_auth(self) -> Optional[Tuple[str, str]]:
        if self.settings.opensearch_user and self.settings.opensearch_pass:
            return (self.settings.opensearch_user, self.settings.opensearch_pass)
        return None

    def _build_client(self) -> OpenSearch:
        return OpenSearch(
            hosts=self.hosts,
            http_auth=self.http_auth,
            use_ssl=self.settings.opensearch_use_https,
            verify_certs=self.verify_certs,
            ssl_show_warn=self.verify_certs,
            timeout=self.timeout,
        )
