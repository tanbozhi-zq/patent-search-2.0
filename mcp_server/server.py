import sys
from pathlib import Path
from typing import Optional


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.fastmcp import FastMCP

from mcp_server.patent_api_client import PatentApiClient


def build_server(client: Optional[PatentApiClient] = None) -> FastMCP:
    patent_client = client or PatentApiClient()
    server = FastMCP(
        "patent-search-mcp",
        instructions="Self-hosted patent search tools backed by the patent search HTTP API.",
        log_level="ERROR",
    )

    @server.tool()
    def patent_search(
        q: str,
        ds: str = "cn",
        page: int = 1,
        page_size: int = 10,
        sort: str = "relation",
        highlight: bool = False,
    ) -> dict:
        """Search patents and return PatentHub-style tool data with patents."""
        return patent_client.patent_search(
            q=q,
            ds=ds,
            page=page,
            page_size=page_size,
            sort=sort,
            highlight=highlight,
        )

    @server.tool()
    def patent_get_detail(patent_id: str, include_description: bool = False) -> dict:
        """Return patent detail by stable patent id."""
        return patent_client.patent_get_detail(patent_id, include_description=include_description)

    @server.tool()
    def patent_get_citations(patent_id: str) -> dict:
        """Return patent citation summaries by stable patent id."""
        return patent_client.patent_get_citations(patent_id)

    @server.tool()
    def patent_get_legal_history(patent_id: str) -> dict:
        """Return patent legal history base structure by stable patent id."""
        return patent_client.patent_get_legal_history(patent_id)

    return server


def main() -> None:
    build_server().run("stdio")


if __name__ == "__main__":
    main()
