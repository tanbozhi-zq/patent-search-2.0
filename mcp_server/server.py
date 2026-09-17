"""MCP server 只注册工具和 transport。真正的数据访问由 PatentApiClient 通过 HTTP
完成，因此该进程不建立 OpenSearch 客户端，也不复制 FastAPI 的查询语义。
"""

import argparse
from functools import partial
import json
import logging
import secrets
import sys
from pathlib import Path
from time import monotonic
from typing import Callable, Optional, Sequence


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anyio
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from app.core.exceptions import ErrorCode
from app.core.logging import configure_logging, log_event
from mcp_server.patent_api_client import (
    McpToolResult,
    PatentApiClient,
    mcp_error_result,
)
from mcp_server.settings import McpServerSettings


configure_logging()
logger = logging.getLogger(__name__)


def build_server(
    client: Optional[PatentApiClient] = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    streamable_http_path: str = "/mcp",
    settings: Optional[McpServerSettings] = None,
) -> FastMCP:
    """构造并注册专利检索 MCP 工具，但不启动任何传输监听。

    每个工具仅委托给 ``PatentApiClient``，因此 MCP 进程不复制 FastAPI 的查询逻辑
    或直连 OpenSearch。可注入 client 使嵌入式宿主和测试能够复用同一工具 schema；
    host、port 与路径只作为 FastMCP transport 元数据保留。
    """
    # build_server 可注入 client 供测试/宿主复用；默认构造的 client 只访问配置的
    # self-hosted API，FastMCP 负责 tools/list 和工具 schema。
    if settings is not None:
        resolved_settings = settings
    elif isinstance(client, PatentApiClient):
        resolved_settings = client.settings
    elif client is None:
        resolved_settings = McpServerSettings.from_env()
    else:
        resolved_settings = McpServerSettings()
    patent_client = client or PatentApiClient(settings=resolved_settings)
    tool_limiter = anyio.CapacityLimiter(resolved_settings.max_concurrent_tools)
    server = FastMCP(
        "patent-search-mcp",
        instructions="Self-hosted patent search tools backed by the patent search HTTP API.",
        log_level="ERROR",
        host=host,
        port=port,
        streamable_http_path=streamable_http_path,
    )

    async def run_tool(
        operation: str,
        call: Callable[..., McpToolResult],
        /,
        *args: object,
        **kwargs: object,
    ) -> CallToolResult:
        """在线程中执行同步适配器调用；满载立即返回稳定的 MCP 错误。"""
        started = monotonic()
        try:
            tool_limiter.acquire_nowait()
        except anyio.WouldBlock:
            result = mcp_error_result(ErrorCode.SERVICE_BUSY)
            log_event(
                logger,
                logging.WARNING,
                "mcp_tool_completed",
                request_id=result.payload["request_id"],
                dependency="patent_search_api",
                operation=operation,
                outcome="rejected",
                code=int(ErrorCode.SERVICE_BUSY),
                elapsed_ms=round((monotonic() - started) * 1000, 3),
                retry_count=0,
                capacity=int(tool_limiter.total_tokens),
            )
            return _call_tool_result(result)

        try:
            result = await anyio.to_thread.run_sync(
                partial(call, *args, **kwargs),
                abandon_on_cancel=False,
            )
            return _call_tool_result(result)
        finally:
            tool_limiter.release()

    @server.tool()
    async def patent_search(
        q: str,
        ds: str = "cn",
        page: int = 1,
        page_size: int = 10,
        sort: str = "relation",
        highlight: bool = False,
    ) -> CallToolResult:
        # MCP schema 从函数签名生成，返回结果同时放入 text 和 structuredContent，
        # 兼容只支持文本或支持结构化结果的客户端。
        """检索专利并返回兼容 PatentHub 的 ``patents`` 分页数据。

        ``q`` 使用后端支持的查询语法；其余参数控制数据集、分页、排序与高亮。
        参数校验和查询执行交由后端，错误会以 MCP 的 ``isError`` 结果返回。
        """
        return await run_tool(
            "patent_search",
            patent_client.patent_search,
            q=q,
            mode="boolean",
            ds=ds,
            page=page,
            page_size=page_size,
            sort=sort,
            highlight=highlight,
        )

    @server.tool()
    async def patent_vector_search(
        semantic_text: str,
        vector_fields: list[str],
        top_k: int = 100,
        ds: str = "cn",
        page: int = 1,
        page_size: int = 10,
        sort: str = "relation",
        highlight: bool = False,
    ) -> CallToolResult:
        """按语义文本检索专利；字段、窗口、分页和排序由 HTTP API 统一校验。"""
        return await run_tool(
            "patent_vector_search",
            patent_client.patent_search,
            mode="vector",
            semantic_text=semantic_text,
            vector_fields=vector_fields,
            top_k=top_k,
            ds=ds,
            page=page,
            page_size=page_size,
            sort=sort,
            highlight=highlight,
        )

    @server.tool()
    async def patent_hybrid_search(
        q: str,
        semantic_text: str,
        vector_fields: list[str],
        top_k: int = 100,
        ds: str = "cn",
        page: int = 1,
        page_size: int = 10,
        sort: str = "relation",
        highlight: bool = False,
    ) -> CallToolResult:
        """融合布尔查询和语义召回；实际解析、融合与排序只由 HTTP API 执行。"""
        return await run_tool(
            "patent_hybrid_search",
            patent_client.patent_search,
            q=q,
            mode="hybrid",
            semantic_text=semantic_text,
            vector_fields=vector_fields,
            top_k=top_k,
            ds=ds,
            page=page,
            page_size=page_size,
            sort=sort,
            highlight=highlight,
        )

    @server.tool()
    async def patent_get_detail(
        patent_id: str,
        include_description: bool = False,
    ) -> CallToolResult:
        """按稳定专利 ID 返回详情；大字段说明书默认不加载。"""
        return await run_tool(
            "patent_get_detail",
            patent_client.patent_get_detail,
            patent_id,
            include_description=include_description,
        )

    @server.tool()
    async def patent_get_citations(patent_id: str) -> CallToolResult:
        """按稳定专利 ID 返回被引、专利引证与非专利引证信息。"""
        return await run_tool(
            "patent_get_citations",
            patent_client.patent_get_citations,
            patent_id,
        )

    @server.tool()
    async def patent_get_legal_history(patent_id: str) -> CallToolResult:
        """按稳定专利 ID 返回标准化法律历史交易结构。"""
        return await run_tool(
            "patent_get_legal_history",
            patent_client.patent_get_legal_history,
            patent_id,
        )

    return server


def _call_tool_result(result: McpToolResult) -> CallToolResult:
    """将内部工具结果同时编码为 MCP 文本内容和结构化内容。

    两种内容承载同一对象，避免只支持文本的客户端与支持 ``structuredContent`` 的
    客户端得到不同语义；错误标记严格沿用下游已判定的 ``is_error``。
    """
    # 工具 payload 以 JSON 文本保留中文，再把同一对象作为 structuredContent；
    # is_error 只由底层安全错误结果决定。
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(result.payload, ensure_ascii=False))],
        structuredContent=result.payload,
        isError=result.is_error,
    )


class BearerTokenAuthApp:
    """为 Streamable HTTP MCP 增加无状态、常量时间比较的 Bearer 鉴权层。

    它只负责 HTTP transport 的入口保护，不解析 MCP payload，也不替代 FastMCP
    的协议处理。非 HTTP ASGI scope 原样传递，便于 lifespan 等框架生命周期正常
    运行；认证失败响应不会包含令牌或内部应用信息。
    """

    def __init__(self, app, access_token: str):
        self.app = app
        self.expected_authorization = f"Bearer {access_token}"

    async def __call__(self, scope, receive, send):
        """按 ASGI scope 类型转发请求，或在 HTTP 鉴权失败时短路为 401。"""
        # 非 HTTP scope（例如 ASGI lifespan）直接转发；HTTP 请求必须完整匹配
        # Authorization，失败只返回 401/code，不暴露 token 或内部应用细节。
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
    settings: Optional[McpServerSettings] = None,
):
    """创建受 Bearer Token 保护的 Streamable HTTP MCP ASGI 应用。

    FastMCP 生成协议应用，本函数只在最外层套认证 middleware；传入的 client 会
    继续注入到工具层，方便宿主按自己的连接/测试策略构造服务。
    """
    # MCP SDK 负责协议，外层 ASGI wrapper 只负责传输级鉴权。
    server = build_server(
        client=client,
        host=host,
        port=port,
        streamable_http_path="/mcp",
        settings=settings,
    )
    return BearerTokenAuthApp(server.streamable_http_app(), access_token=access_token)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """解析进程入口的 transport 与监听参数，不读取环境或启动服务。"""
    # transport 选择影响是否需要 MCP_ACCESS_TOKEN；host/port 只用于 HTTP listener。
    parser = argparse.ArgumentParser(description="Patent search MCP server")
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9000)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    """按命令行选择 stdio 或受保护的 HTTP transport 并交给相应运行器。"""
    # stdio 直接交给 FastMCP；HTTP 则先读环境并强制要求 Bearer Token，再交给 Uvicorn。
    args = parse_args(argv)
    settings = McpServerSettings.from_env()
    if args.transport == "stdio":
        build_server(settings=settings).run("stdio")
        return

    access_token = settings.require_access_token()
    app = build_http_app(
        access_token=access_token,
        host=args.host,
        port=args.port,
        settings=settings,
    )
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="error",
        access_log=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
