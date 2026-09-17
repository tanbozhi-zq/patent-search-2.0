"""验证 MCP 服务的工具注册、协议结果、命令行与 Bearer 鉴权层。"""

import json
import logging
from threading import Barrier, Event, Lock
from time import perf_counter, sleep

import anyio
import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.memory import create_connected_server_and_client_session
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.core.request_context import bind_request_id, current_request_id, reset_request_id
from mcp_server.patent_api_client import McpToolResult, PatentApiClient
from mcp_server.server import BearerTokenAuthApp, build_server, parse_args
from mcp_server.settings import McpServerSettings


class FakePatentApiClient:
    def __init__(self):
        self.search_calls = []

    def patent_search(self, **kwargs):
        self.search_calls.append(kwargs)
        return McpToolResult(
            payload={
                "total": 1,
                "page": kwargs["page"],
                "page_size": kwargs["page_size"],
                "total_pages": 1,
                "accessible_pages": 1,
                "next_page": None,
                "took_ms": 3,
                "patents": [{"id": "cn-1"}],
            }
        )

    def patent_get_detail(self, patent_id, include_description=False):
        return McpToolResult(payload={"id": patent_id, "claims": "权利要求"})

    def patent_get_citations(self, patent_id):
        return McpToolResult(
            payload={
                "patent_id": patent_id,
                "cited_by": [],
                "patent_references": [],
                "non_patent_references": [],
            }
        )

    def patent_get_legal_history(self, patent_id):
        return McpToolResult(
            payload={"patent_id": patent_id, "transaction_count": 0, "transactions": []}
        )


class FailingPatentApiClient:
    @staticmethod
    def _error():
        return McpToolResult(
            payload={
                "error": "查询语法错误",
                "code": 40001,
                "message": "查询语法错误",
                "request_id": "backend-request-id",
                "retryable": False,
            },
            is_error=True,
        )

    def patent_search(self, **kwargs):
        return self._error()

    def patent_get_detail(self, patent_id, include_description=False):
        return self._error()

    def patent_get_citations(self, patent_id):
        return self._error()

    def patent_get_legal_history(self, patent_id):
        return self._error()


class ConcurrentPatentApiClient(FakePatentApiClient):
    def __init__(self, concurrent_calls, delay_seconds):
        super().__init__()
        self._barrier = Barrier(concurrent_calls)
        self._delay_seconds = delay_seconds
        self._lock = Lock()
        self.active = 0
        self.peak_active = 0

    def patent_get_detail(self, patent_id, include_description=False):
        with self._lock:
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
        try:
            self._barrier.wait(timeout=2)
            sleep(self._delay_seconds)
            return McpToolResult(
                payload={
                    "id": patent_id,
                    "request_id": current_request_id(),
                }
            )
        finally:
            with self._lock:
                self.active -= 1


class GatedPatentApiClient(FakePatentApiClient):
    def __init__(self, expected_active):
        super().__init__()
        self.expected_active = expected_active
        self.started = Event()
        self.release = Event()
        self._lock = Lock()
        self.active = 0
        self.peak_active = 0

    def patent_get_detail(self, patent_id, include_description=False):
        with self._lock:
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            if self.active == self.expected_active:
                self.started.set()
        try:
            if not self.release.wait(timeout=2):
                raise TimeoutError("test worker was not released")
            return McpToolResult(payload={"id": patent_id})
        finally:
            with self._lock:
                self.active -= 1


class RaisesOncePatentApiClient(FakePatentApiClient):
    def __init__(self):
        super().__init__()
        self.should_raise = True

    def patent_get_detail(self, patent_id, include_description=False):
        if self.should_raise:
            self.should_raise = False
            raise RuntimeError("controlled test failure")
        return super().patent_get_detail(patent_id, include_description)


def test_mcp_server_lists_patent_tools():
    async def run():
        server = build_server(client=FakePatentApiClient())
        tools = await server.list_tools()
        names = {tool.name for tool in tools}

        assert names == {
            "patent_search",
            "patent_vector_search",
            "patent_hybrid_search",
            "patent_get_detail",
            "patent_get_citations",
            "patent_get_legal_history",
        }
        search_tool = next(tool for tool in tools if tool.name == "patent_search")
        assert "index_analyzer_mode" not in search_tool.inputSchema.get("properties", {})
        vector_tool = next(tool for tool in tools if tool.name == "patent_vector_search")
        hybrid_tool = next(tool for tool in tools if tool.name == "patent_hybrid_search")
        assert "q" not in vector_tool.inputSchema["properties"]
        assert "q" in hybrid_tool.inputSchema["properties"]
        for tool in (search_tool, vector_tool, hybrid_tool):
            assert "mode" not in tool.inputSchema["properties"]
            assert "pipeline" not in tool.inputSchema["properties"]
            assert "vector" not in tool.inputSchema["properties"]

    anyio.run(run)


def test_mcp_server_injected_client_does_not_depend_on_backend_environment(monkeypatch):
    monkeypatch.setenv("PATENT_SEARCH_TIMEOUT_SECONDS", "240")

    async def run():
        server = build_server(client=FakePatentApiClient())

        assert len(await server.list_tools()) == 6

    anyio.run(run)


def test_mcp_server_calls_patent_search_tool():
    async def run():
        client = FakePatentApiClient()
        server = build_server(client=client)
        result = await server.call_tool("patent_search", {"q": "阀门", "page": 1, "page_size": 2})
        payload = json.loads(result.content[0].text)

        assert result.isError is False
        assert result.structuredContent == payload
        assert payload["patents"][0]["id"] == "cn-1"
        assert payload["page_size"] == 2
        assert "records" not in payload
        assert client.search_calls == [
            {
                "q": "阀门",
                "mode": "boolean",
                "ds": "cn",
                "page": 1,
                "page_size": 2,
                "sort": "relation",
                "highlight": False,
            }
        ]

    anyio.run(run)


def test_mcp_server_calls_vector_and_hybrid_tools_with_fixed_modes():
    async def run():
        client = FakePatentApiClient()
        server = build_server(client=client)

        await server.call_tool(
            "patent_vector_search",
            {
                "semantic_text": "流体控制阀",
                "vector_fields": ["abstract", "main_claim"],
                "top_k": 40,
                "page": 2,
                "page_size": 10,
            },
        )
        await server.call_tool(
            "patent_hybrid_search",
            {
                "q": "ipc:F16K",
                "semantic_text": "流体控制阀",
                "vector_fields": ["abstract"],
                "sort": "!documentDate",
            },
        )

        assert client.search_calls == [
            {
                "mode": "vector",
                "semantic_text": "流体控制阀",
                "vector_fields": ["abstract", "main_claim"],
                "top_k": 40,
                "ds": "cn",
                "page": 2,
                "page_size": 10,
                "sort": "relation",
                "highlight": False,
            },
            {
                "q": "ipc:F16K",
                "mode": "hybrid",
                "semantic_text": "流体控制阀",
                "vector_fields": ["abstract"],
                "top_k": 100,
                "ds": "cn",
                "page": 1,
                "page_size": 10,
                "sort": "!documentDate",
                "highlight": False,
            },
        ]

    anyio.run(run)


def test_mcp_server_calls_detail_citations_and_legal_history_tools():
    async def run():
        server = build_server(client=FakePatentApiClient())
        detail_result = await server.call_tool("patent_get_detail", {"patent_id": "cn-1"})
        citations_result = await server.call_tool("patent_get_citations", {"patent_id": "cn-1"})
        legal_result = await server.call_tool("patent_get_legal_history", {"patent_id": "cn-1"})
        detail = json.loads(detail_result.content[0].text)
        citations = json.loads(citations_result.content[0].text)
        legal_history = json.loads(legal_result.content[0].text)

        assert detail_result.isError is False
        assert citations_result.isError is False
        assert legal_result.isError is False
        assert detail["claims"] == "权利要求"
        assert citations["patent_references"] == []
        assert legal_history["transactions"] == []

    anyio.run(run)


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("patent_search", {"q": "invalid"}),
        (
            "patent_vector_search",
            {"semantic_text": "invalid", "vector_fields": ["abstract"]},
        ),
        (
            "patent_hybrid_search",
            {
                "q": "invalid",
                "semantic_text": "invalid",
                "vector_fields": ["abstract"],
            },
        ),
        ("patent_get_detail", {"patent_id": "cn-1"}),
        ("patent_get_citations", {"patent_id": "cn-1"}),
        ("patent_get_legal_history", {"patent_id": "cn-1"}),
    ],
)
def test_mcp_server_marks_every_tool_error_as_is_error(tool_name, arguments):
    async def run():
        server = build_server(client=FailingPatentApiClient())
        result = await server.call_tool(tool_name, arguments)
        payload = json.loads(result.content[0].text)

        assert result.isError is True
        assert result.structuredContent == payload
        assert payload["code"] == 40001
        assert payload["request_id"] == "backend-request-id"
        assert payload["retryable"] is False

    anyio.run(run)


def test_mcp_protocol_transmits_is_error_and_structured_error_payload():
    async def run():
        server = build_server(client=FailingPatentApiClient())
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool("patent_search", {"q": "invalid"})
            payload = json.loads(result.content[0].text)

            assert result.isError is True
            assert result.structuredContent == payload
            assert payload["code"] == 40001
            assert payload["request_id"] == "backend-request-id"

    anyio.run(run)


def test_mcp_protocol_rejects_conflicting_200_error_marker():
    def handler(_request):
        return httpx.Response(
            200,
            json={
                "id": "cn-1",
                "code": 40001,
                "message": "查询语法错误",
                "request_id": "backend-request-id",
                "retryable": False,
            },
        )

    client = PatentApiClient(
        settings=McpServerSettings(base_url="http://api"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    async def run():
        server = build_server(client=client)
        async with create_connected_server_and_client_session(server) as session:
            result = await session.call_tool("patent_get_detail", {"patent_id": "cn-1"})
            payload = json.loads(result.content[0].text)

            assert result.isError is True
            assert result.structuredContent == payload
            assert payload["code"] == 50001

    anyio.run(run)


def test_mcp_slow_tools_overlap_and_keep_request_context_isolated():
    async def run():
        client = ConcurrentPatentApiClient(concurrent_calls=3, delay_seconds=0.2)
        server = build_server(
            client=client,
            settings=McpServerSettings(max_concurrent_tools=3),
        )
        results = {}

        async def call_tool(index):
            request_id = f"mcp-concurrent-{index}"
            token = bind_request_id(request_id)
            try:
                result = await server.call_tool(
                    "patent_get_detail",
                    {"patent_id": f"cn-{index}"},
                )
                results[index] = json.loads(result.content[0].text)
            finally:
                reset_request_id(token)

        started = perf_counter()
        async with anyio.create_task_group() as task_group:
            for index in range(3):
                task_group.start_soon(call_tool, index)
        elapsed_seconds = perf_counter() - started

        assert elapsed_seconds < 0.5
        assert client.peak_active == 3
        assert results == {
            index: {
                "id": f"cn-{index}",
                "request_id": f"mcp-concurrent-{index}",
            }
            for index in range(3)
        }

    anyio.run(run)


def test_mcp_tool_limit_rejects_immediately_and_recovers(caplog):
    async def run():
        client = GatedPatentApiClient(expected_active=2)
        server = build_server(
            client=client,
            settings=McpServerSettings(max_concurrent_tools=2),
        )
        admitted_results = []

        async def admitted_call(index):
            admitted_results.append(
                await server.call_tool(
                    "patent_get_detail",
                    {"patent_id": f"cn-{index}"},
                )
            )

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(admitted_call, 1)
            task_group.start_soon(admitted_call, 2)
            assert await anyio.to_thread.run_sync(client.started.wait, 1)

            started = perf_counter()
            rejected = await server.call_tool(
                "patent_get_detail",
                {"patent_id": "cn-rejected"},
            )
            rejection_elapsed = perf_counter() - started
            client.release.set()

        rejected_payload = json.loads(rejected.content[0].text)
        recovered = await server.call_tool(
            "patent_get_detail",
            {"patent_id": "cn-recovered"},
        )

        assert rejection_elapsed < 0.1
        assert rejected.isError is True
        assert rejected_payload["code"] == 50301
        assert rejected_payload["retryable"] is True
        assert rejected_payload["request_id"]
        assert all(result.isError is False for result in admitted_results)
        assert recovered.isError is False
        assert client.peak_active == 2

    caplog.set_level(logging.WARNING, logger="mcp_server.server")
    anyio.run(run)

    rejection = next(
        record
        for record in caplog.records
        if getattr(record, "event", None) == "mcp_tool_completed"
        and getattr(record, "outcome", None) == "rejected"
    )
    assert rejection.operation == "patent_get_detail"
    assert rejection.code == 50301
    assert rejection.capacity == 2
    assert "cn-rejected" not in caplog.text


def test_mcp_tool_limit_stays_held_until_cancelled_worker_finishes():
    async def run():
        client = GatedPatentApiClient(expected_active=1)
        server = build_server(
            client=client,
            settings=McpServerSettings(max_concurrent_tools=1),
        )
        cancelled_done = anyio.Event()

        async def cancelled_call(*, task_status=anyio.TASK_STATUS_IGNORED):
            with anyio.CancelScope() as cancel_scope:
                task_status.started(cancel_scope)
                await server.call_tool(
                    "patent_get_detail",
                    {"patent_id": "cn-cancelled"},
                )
            cancelled_done.set()

        async with anyio.create_task_group() as task_group:
            cancel_scope = await task_group.start(cancelled_call)
            assert await anyio.to_thread.run_sync(client.started.wait, 1)
            cancel_scope.cancel()

            rejected = await server.call_tool(
                "patent_get_detail",
                {"patent_id": "cn-while-worker-runs"},
            )
            assert rejected.isError is True
            assert json.loads(rejected.content[0].text)["code"] == 50301

            client.release.set()
            await cancelled_done.wait()

        recovered = await server.call_tool(
            "patent_get_detail",
            {"patent_id": "cn-after-cancel"},
        )
        assert recovered.isError is False
        assert client.active == 0

    anyio.run(run)


def test_mcp_tool_limit_releases_after_unexpected_exception():
    async def run():
        server = build_server(
            client=RaisesOncePatentApiClient(),
            settings=McpServerSettings(max_concurrent_tools=1),
        )

        with pytest.raises(ToolError, match="controlled test failure"):
            await server.call_tool(
                "patent_get_detail",
                {"patent_id": "cn-failed"},
            )

        recovered = await server.call_tool(
            "patent_get_detail",
            {"patent_id": "cn-recovered"},
        )
        assert recovered.isError is False

    anyio.run(run)


def test_parse_args_defaults_to_stdio():
    args = parse_args([])

    assert args.transport == "stdio"
    assert args.host == "0.0.0.0"
    assert args.port == 9000


def test_parse_args_accepts_http_host_and_port():
    args = parse_args(["--transport", "http", "--host", "127.0.0.1", "--port", "9100"])

    assert args.transport == "http"
    assert args.host == "127.0.0.1"
    assert args.port == 9100


def test_bearer_token_auth_rejects_missing_and_invalid_tokens():
    async def ok(_request):
        return JSONResponse({"ok": True})

    app = BearerTokenAuthApp(
        Starlette(routes=[Route("/mcp", ok, methods=["GET", "POST"])]),
        access_token="secret",
    )
    client = TestClient(app)

    missing = client.post("/mcp")
    invalid = client.post("/mcp", headers={"Authorization": "Bearer wrong"})

    assert missing.status_code == 401
    assert missing.json() == {"error": "unauthorized", "code": 40101}
    assert invalid.status_code == 401
    assert invalid.json() == {"error": "unauthorized", "code": 40101}


def test_bearer_token_auth_allows_valid_token():
    async def ok(_request):
        return JSONResponse({"ok": True})

    app = BearerTokenAuthApp(
        Starlette(routes=[Route("/mcp", ok, methods=["GET", "POST"])]),
        access_token="secret",
    )
    client = TestClient(app)

    response = client.post("/mcp", headers={"Authorization": "Bearer secret"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}
