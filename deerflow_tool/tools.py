from typing import Optional

import httpx

from app.integrations.patenthub_adapter import PatentHubAdapterConfig, PatentHubToolAdapter


try:
    from langchain.tools import tool as langchain_tool
except ImportError:
    langchain_tool = None


def create_adapter(
    config: Optional[PatentHubAdapterConfig] = None,
    client: Optional[httpx.Client] = None,
) -> PatentHubToolAdapter:
    return PatentHubToolAdapter(config=config, client=client)


def patent_search(
    q: str,
    ds: str = "cn",
    page: int = 1,
    page_size: int = 10,
    sort: str = "relation",
    highlight: bool = False,
) -> str:
    """Search patents through the self-hosted patent API.

    Args:
        q: Keyword or structured query expression.
        ds: Data scope, either "cn" or "all".
        page: One-based page number.
        page_size: Requested page size, capped by PATENT_SEARCH_PAGE_SIZE_LIMIT.
        sort: Sort value accepted by the self-hosted API.
        highlight: Whether to request highlighted snippets.
    """
    return create_adapter().patent_search(q, ds, page, page_size, sort, highlight)


def patent_get_detail(patent_id: str, include_description: bool = False) -> str:
    """Retrieve patent detail through the self-hosted patent API.

    Args:
        patent_id: Stable patent id returned by patent_search.
        include_description: Whether to include full description text.
    """
    return create_adapter().patent_get_detail(patent_id, include_description)


def patent_get_citations(patent_id: str) -> str:
    """Retrieve patent citation data through the self-hosted patent API.

    Args:
        patent_id: Stable patent id returned by patent_search.
    """
    return create_adapter().patent_get_citations(patent_id)


def patent_get_legal_history(patent_id: str) -> str:
    """Retrieve patent legal history through the self-hosted patent API.

    Args:
        patent_id: Stable patent id returned by patent_search.
    """
    return create_adapter().patent_get_legal_history(patent_id)


def _tool(name: str, func):
    if langchain_tool is None:
        return func
    return langchain_tool(name, parse_docstring=True)(func)


patent_search_tool = _tool("patent_search", patent_search)
patent_get_detail_tool = _tool("patent_get_detail", patent_get_detail)
patent_get_citations_tool = _tool("patent_get_citations", patent_get_citations)
patent_get_legal_history_tool = _tool("patent_get_legal_history", patent_get_legal_history)
