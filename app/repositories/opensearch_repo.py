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

    def search(self, body: dict) -> dict:
        return self.client.search(index=self.index_name, body=body)

    def count(self, body: dict) -> int:
        raw = self.client.count(index=self.index_name, body=body)
        return int(raw.get("count", 0))

    def count_with_min_score(self, query: dict, min_score: float) -> int:
        raw = self.search({
            "size": 0,
            "track_total_hits": True,
            "min_score": min_score,
            "query": query,
        })
        return self._total_hits(raw.get("hits", {}))

    def find_in_query(self, query: dict, identity: dict) -> Optional[dict]:
        raw = self.search({
            "size": 1,
            "track_total_hits": True,
            "query": {"bool": {"must": [query], "filter": [identity]}},
        })
        return self._first_hit(raw)

    def find_target(self, identifier: str) -> tuple[str, Optional[dict], int]:
        patent_id = identifier.strip()
        raw = self.search({
            "size": 10,
            "track_total_hits": True,
            "query": self._identifier_query("patent_id", patent_id),
        })
        hits_data = raw.get("hits", {})
        hits = hits_data.get("hits", [])
        if hits:
            return "patent_id", hits[0], self._total_hits(hits_data)

        publication_number = patent_id.upper()
        raw = self.search({
            "size": 10,
            "track_total_hits": True,
            "query": self._identifier_query("PublicationNumber", publication_number),
        })
        hits_data = raw.get("hits", {})
        hits = hits_data.get("hits", [])
        return "PublicationNumber", (hits[0] if hits else None), self._total_hits(hits_data)

    def get_patent_by_identifier(self, identifier: str) -> Optional[dict]:
        for field in ("patent_id", "PublicationNumber", "ApplicationNumber"):
            raw = self.search(
                {
                    "size": 1,
                    "query": self._identifier_query(field, identifier),
                }
            )
            hit = self._first_hit(raw)
            if hit is not None:
                return hit
        return None

    def _identifier_query(self, field: str, identifier: str) -> dict:
        return {"term": {field: identifier}}

    def _first_hit(self, raw: dict) -> Optional[dict]:
        hits = raw.get("hits", {}).get("hits", [])
        if not hits:
            return None
        return hits[0]

    @staticmethod
    def _total_hits(hits: dict) -> int:
        total = hits.get("total", 0)
        if isinstance(total, dict):
            return int(total.get("value", 0))
        return int(total or 0)
