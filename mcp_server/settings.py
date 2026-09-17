"""MCP 进程只保存两类配置：它访问 FastAPI 的内部地址/API Token，以及 HTTP
Streamable MCP 对外使用的 Bearer Token。stdio 不需要后者；HTTP 启动时必须显式提供。
"""

import os
from dataclasses import dataclass

from app.core.timing_contract import MIN_MCP_BACKEND_TIMEOUT_SECONDS


DEFAULT_MCP_MAX_CONCURRENT_TOOLS = 4


@dataclass
class McpServerSettings:
    """MCP 进程的最小运行配置及其使用边界。

    前四项只用于调用同机或内网的专利 HTTP API；``access_token`` 仅保护
    Streamable HTTP transport，stdio 模式不会读取或要求它。配置读取保持宽容，
    部署层负责对环境值做更严格的发布前校验。
    """

    # timeout 245 秒略大于 FastAPI 的 240 秒总预算，优先让调用方收到后端生成的
    # 50401/request_id，而不是 MCP 客户端先自行超时。
    base_url: str = "http://127.0.0.1:8000"
    api_token: str = ""
    timeout_seconds: int = MIN_MCP_BACKEND_TIMEOUT_SECONDS
    page_size_limit: int = 50
    access_token: str = ""
    max_concurrent_tools: int = DEFAULT_MCP_MAX_CONCURRENT_TOOLS

    def __post_init__(self) -> None:
        if (
            type(self.timeout_seconds) is not int
            or self.timeout_seconds < MIN_MCP_BACKEND_TIMEOUT_SECONDS
        ):
            raise ValueError(
                "PATENT_SEARCH_TIMEOUT_SECONDS must be at least "
                f"{MIN_MCP_BACKEND_TIMEOUT_SECONDS} seconds"
            )
        if type(self.max_concurrent_tools) is not int or self.max_concurrent_tools < 1:
            raise ValueError("MCP_MAX_CONCURRENT_TOOLS must be at least 1")

    @classmethod
    def from_env(cls) -> "McpServerSettings":
        """从 MCP 专用环境变量构造独立进程配置。

        缺失或非法整数使用保守默认值，以避免本地 stdio 开发因无关配置直接无法
        启动；请求侧仍会在 adapter 和后端分别执行页大小与业务参数校验。
        """
        # MCP 侧保持简单的环境读取；页面/客户端传入的 page_size 最终还会在
        # PatentHubAdapter 中被配置上限再次裁剪。
        return cls(
            base_url=os.getenv("PATENT_SEARCH_BASE_URL", "http://127.0.0.1:8000"),
            api_token=os.getenv("PATENT_SEARCH_API_TOKEN", ""),
            timeout_seconds=_env_int(
                "PATENT_SEARCH_TIMEOUT_SECONDS",
                MIN_MCP_BACKEND_TIMEOUT_SECONDS,
            ),
            page_size_limit=_env_int("PATENT_SEARCH_PAGE_SIZE_LIMIT", 50),
            access_token=os.getenv("MCP_ACCESS_TOKEN", ""),
            max_concurrent_tools=_env_int(
                "MCP_MAX_CONCURRENT_TOOLS",
                DEFAULT_MCP_MAX_CONCURRENT_TOOLS,
            ),
        )

    def require_access_token(self) -> str:
        """取得 HTTP transport 必需的 Bearer Token，缺失时拒绝启动。

        此检查刻意不在 ``from_env`` 中执行，确保无需对外监听的 stdio transport
        不会因为未配置远程访问令牌而失败。
        """
        # 只在 HTTP transport 启动时调用；stdio 不应因为没有远程 Bearer Token 而失败。
        token = self.access_token.strip()
        if not token:
            raise RuntimeError("MCP_ACCESS_TOKEN is required when --transport http is used")
        return token


def _env_int(name: str, default: int) -> int:
    # MCP 是独立进程，非法整数沿用安全默认值以保持本地 stdio 可启动；部署侧若需
    # fail-fast，应在 systemd/发布检查中验证环境，而不是把异常带入每次工具调用。
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default
