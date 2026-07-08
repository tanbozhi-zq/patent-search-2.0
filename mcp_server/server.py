import argparse
import secrets
import sys
from pathlib import Path
from typing import Optional, Sequence


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from mcp_server.patent_api_client import PatentApiClient
from mcp_server.settings import McpServerSettings


def build_server(
    client: Optional[PatentApiClient] = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    streamable_http_path: str = "/mcp",
) -> FastMCP:
    patent_client = client or PatentApiClient()
    server = FastMCP(
        "patent-search-mcp",
        instructions="Self-hosted patent search tools backed by the patent search HTTP API.",
        log_level="ERROR",
        host=host,
        port=port,
        streamable_http_path=streamable_http_path,
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


class BearerTokenAuthApp:
    def __init__(self, app, access_token: str):
        self.app = app
        self.expected_authorization = f"Bearer {access_token}"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        authorization = headers.get("authorization", "")
        if not secrets.compare_digest(authorization, self.expected_authorization):
            response = JSONResponse({"error": "unauthorized", "code": 40101}, status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def build_http_app(
    access_token: str,
    client: Optional[PatentApiClient] = None,
    host: str = "0.0.0.0",
    port: int = 9000,
):
    server = build_server(client=client, host=host, port=port, streamable_http_path="/mcp")
    return BearerTokenAuthApp(server.streamable_http_app(), access_token=access_token)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Patent search MCP server")
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9000)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    if args.transport == "stdio":
        build_server().run("stdio")
        return

    settings = McpServerSettings.from_env()
    access_token = settings.require_access_token()
    app = build_http_app(access_token=access_token, host=args.host, port=args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="error")


if __name__ == "__main__":
    main()
