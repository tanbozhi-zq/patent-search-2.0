import json
from typing import Optional

import httpx

from app.integrations.patenthub_adapter import PatentHubAdapterConfig, PatentHubToolAdapter
from mcp_server.settings import McpServerSettings


class PatentApiClient:
    def __init__(
        self,
        settings: Optional[McpServerSettings] = None,
        http_client: Optional[httpx.Client] = None,
    ):
        self.settings = settings or McpServerSettings.from_env()
        self._adapter = PatentHubToolAdapter(
            config=PatentHubAdapterConfig(
                self_hosted_base_url=self.settings.base_url,
                self_hosted_api_token=self.settings.api_token,
                use_self_hosted=True,
                page_size_limit=self.settings.page_size_limit,
                timeout_seconds=self.settings.timeout_seconds,
            ),
            client=http_client,
        )

    def patent_search(
        self,
        q: str,
        ds: str = "cn",
        page: int = 1,
        page_size: int = 10,
        sort: str = "relation",
        highlight: bool = False,
    ) -> dict:
        return _loads(self._adapter.patent_search(q, ds, page, page_size, sort, highlight))

    def patent_get_detail(self, patent_id: str, include_description: bool = False) -> dict:
        return _loads(self._adapter.patent_get_detail(patent_id, include_description))

    def patent_get_citations(self, patent_id: str) -> dict:
        return _loads(self._adapter.patent_get_citations(patent_id))

    def patent_get_legal_history(self, patent_id: str) -> dict:
        return _loads(self._adapter.patent_get_legal_history(patent_id))


def _loads(value: str) -> dict:
    data = json.loads(value)
    return data if isinstance(data, dict) else {"error": "invalid tool response", "code": 50002}
