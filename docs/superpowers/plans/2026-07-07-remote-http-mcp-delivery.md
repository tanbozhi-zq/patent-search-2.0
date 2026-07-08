# Remote HTTP MCP Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the validated stdio MCP patent-search server into a remote HTTP MCP delivery shape with token authentication, deployment support, smoke verification, and cleaned delivery documentation.

**Architecture:** Keep `app/` as the FastAPI/OpenSearch owner and keep `mcp_server/` as the MCP protocol wrapper. The MCP Server keeps stdio mode, adds a project-level `--transport http` mode, maps that to the MCP SDK Streamable HTTP app, wraps HTTP requests with bearer-token authentication, and continues calling FastAPI through HTTP.

**Tech Stack:** Python 3, FastAPI, Starlette ASGI middleware/wrapper, Uvicorn, MCP Python SDK `mcp==1.28.1`, httpx, pytest, systemd.

---

## File Structure

Implementation should touch these files only unless verification reveals a direct breakage:

```text
README.md
.env.example
deployment/patent-mcp.service
docs/README.md
docs/delivery/stage12_deerflow_tool_mcp_work_plan.md
docs/delivery/mcp_integration_guide.md
docs/internal/stage12_mcp_dev_assignment.md
mcp_server/README.md
mcp_server/server.py
mcp_server/settings.py
scripts/smoke_mcp_http.py
tests/test_mcp_server.py
tests/test_mcp_settings.py
```

Delete these active-mainline DeerFlow Tool files:

```text
deerflow_tool/
scripts/smoke_deerflow_tool.py
tests/test_deerflow_tool.py
docs/delivery/deerflow_tool_integration_guide.md
docs/internal/stage12_deerflow_tool_dev_assignment.md
docs/internal/stage12_3_deerflow_integration_plan.md
```

Do not modify:

```text
patent_harness_base_副本/
frontend/index.html
会议记录/
```

`frontend/index.html` and `会议记录/` may already be dirty user-owned worktree changes. Leave them alone.

---

## Task 1: Remove The DeerFlow Tool Active Mainline

**Files:**
- Delete: `deerflow_tool/`
- Delete: `scripts/smoke_deerflow_tool.py`
- Delete: `tests/test_deerflow_tool.py`
- Delete: `docs/delivery/deerflow_tool_integration_guide.md`
- Delete: `docs/internal/stage12_deerflow_tool_dev_assignment.md`
- Delete: `docs/internal/stage12_3_deerflow_integration_plan.md`
- Modify: `README.md`
- Modify: `docs/README.md`
- Modify: `docs/delivery/stage12_deerflow_tool_mcp_work_plan.md`
- Modify: `docs/internal/stage12_mcp_dev_assignment.md`

- [ ] **Step 1: Record current references before deletion**

Run:

```bash
rg -n "deerflow_tool|smoke_deerflow_tool|deerflow_tool_integration_guide|stage12_deerflow_tool_dev_assignment|stage12_3_deerflow_integration_plan|DeerFlow Tool" README.md docs mcp_server scripts tests
```

Expected: references appear in README and Stage 12 docs. Use this list to update active docs after deleting the unused path.

- [ ] **Step 2: Delete unused DeerFlow Tool files**

Run:

```bash
git rm -r deerflow_tool
git rm scripts/smoke_deerflow_tool.py
git rm tests/test_deerflow_tool.py
git rm docs/delivery/deerflow_tool_integration_guide.md
git rm docs/internal/stage12_deerflow_tool_dev_assignment.md
git rm docs/internal/stage12_3_deerflow_integration_plan.md
```

Expected: Git stages only those removals. If any path is already missing, confirm with `git status --short` and continue with the remaining paths.

- [ ] **Step 3: Update `README.md` stage summary**

Replace the current Stage section with this content:

```markdown
## Stage

Current status: Stage 12 remote HTTP MCP delivery is in progress.

Implemented so far:

- FastAPI app
- `GET /health`
- `.env` configuration loading
- `X-API-Key` auth dependency
- OpenSearch client construction
- `POST /api/patent/search`
- Boolean `q` parser and OpenSearch DSL builder
- analyzer compatibility mode through `index_analyzer_mode`
- `GET /api/patent/detail/{patent_id}`
- `GET /api/patent/detail/{patent_id}?include_description=true`
- `GET /api/patent/citations/{patent_id}`
- `GET /api/patent/legal-history/{patent_id}`
- flat error responses for validation, business, auth, and OpenSearch failures
- search record snake_case compatibility aliases
- SaaS PatentHub tool adapter for self-hosted search/detail/citations/legal-history
- Stage 12.4 MCP stdio server under `mcp_server/`
- legacy static inspection page under `/test/`
- pytest test suite
- systemd deployment template for the FastAPI service

Current delivery mainline:

- FastAPI patent search service under `app/`
- Remote HTTP MCP Server under `mcp_server/`
- Company DeerFlow / workspace connects to the public MCP URL through `type: "http"`

The local DeerFlow Tool plugin path is not part of the current delivery scope.

Next stage:

- Add remote HTTP MCP transport, MCP bearer-token authentication, `patent-mcp.service`, HTTP MCP smoke verification, and updated company workspace integration documentation.
- Stage 12 no longer uses a separate test environment, tester assignment, test acceptance sheet, or test report; quality gates are developer self-check, project-control review, real integration records, and delivery-doc review.
```

In the README "Documentation" and "Local self-check" sections, remove references to deleted DeerFlow Tool files and `scripts/smoke_deerflow_tool.py`.

- [ ] **Step 4: Update `docs/README.md` current-stage summary**

Replace the "当前阶段" section with:

```markdown
## 当前阶段

当前项目处于 `Stage 12：远程 HTTP MCP 服务化交付`。

Stage 12 当前主线是：保持核心 FastAPI HTTP API 稳定，把已验证的 stdio MCP Server 收敛为公司真实 DeerFlow / 工作台可通过公网调用的远程 HTTP MCP Server。

本阶段不再维护 DeerFlow Tool 本地插件路径；相关历史可通过 Git 历史查询。
```

In "优先阅读", remove:

```text
docs/internal/stage12_deerflow_tool_dev_assignment.md
docs/internal/stage12_3_deerflow_integration_plan.md
docs/delivery/deerflow_tool_integration_guide.md
```

In "正式交付文档", remove the `deerflow_tool_integration_guide.md` row. Keep `mcp_integration_guide.md` and describe it as remote HTTP MCP integration, deployment, and smoke reference.

In "内部过程文档", remove the Stage 12.2 and Stage 12.3 DeerFlow Tool rows. Keep Stage 12.4 MCP Server and update its description to "远程 HTTP MCP 服务化".

- [ ] **Step 5: Update `docs/delivery/stage12_deerflow_tool_mcp_work_plan.md`**

Rewrite the opening target so it says:

```markdown
# Stage 12：远程 HTTP MCP 服务化交付工作计划

## 1. 当前目标

当前项目已经完成自研专利检索 API 主体建设和 stdio MCP 验证。Stage 12 当前交付主线收敛为：

1. `app/` 提供 FastAPI 专利检索 HTTP 服务。
2. `mcp_server/` 提供远程 HTTP MCP Server。
3. 公司真实 DeerFlow / 工作台通过公网 MCP URL 接入。

DeerFlow Tool 本地插件路径不作为当前交付范围，不再继续维护。
```

Remove sections that describe "阶段二：DeerFlow Tool 封装" and "阶段三：DeerFlow 端到端联调" as active work. If historical context is useful, replace them with a short note:

```markdown
## 历史路径说明

早期曾规划 DeerFlow Tool 本地插件路径。公司真实 DeerFlow / 工作台已确认支持 `type: "http"` 的远程 MCP Server，且服务器可访问我方公网 MCP 地址，因此当前交付不再走本地 Tool 插件方式。
```

Keep or add active sections for FastAPI service, remote HTTP MCP Server, authentication, deployment, smoke verification, and rollback.

- [ ] **Step 6: Update `docs/internal/stage12_mcp_dev_assignment.md`**

Change the title to:

```markdown
# Stage 12 MCP Server 远程 HTTP 服务化开发派工单
```

Replace entry conditions with:

```markdown
## 1. 阶段入口

项目总控已确认公司真实 DeerFlow / 工作台支持 `type: "http"` 的远程 MCP Server，且真实接入路径为公网访问。本阶段直接进入远程 HTTP MCP 服务化，不再依赖 DeerFlow Tool 本地插件联调作为前置条件。
```

Update the development target to require stdio plus HTTP mode, `/mcp`, and `MCP_ACCESS_TOKEN`.

- [ ] **Step 7: Verify removed references**

Run:

```bash
rg -n "deerflow_tool|smoke_deerflow_tool|deerflow_tool_integration_guide|stage12_deerflow_tool_dev_assignment|stage12_3_deerflow_integration_plan" README.md docs mcp_server scripts tests
```

Expected: no output. If output remains in archived files only, either leave archived historical references alone or add `docs/archive` to the search exception. Active docs must not point readers to deleted files.

- [ ] **Step 8: Run focused tests after cleanup**

Run:

```bash
.venv/bin/python -m pytest tests/test_mcp_server.py tests/test_mcp_patent_api_client.py tests/test_mcp_settings.py -q
```

Expected: all selected tests pass.

- [ ] **Step 9: Commit cleanup**

Run:

```bash
git status --short
git add README.md docs/README.md docs/delivery/stage12_deerflow_tool_mcp_work_plan.md docs/internal/stage12_mcp_dev_assignment.md
git status --short
git commit -m "chore: remove unused DeerFlow tool integration path"
```

Expected: commit contains only the DeerFlow Tool deletion and active-doc cleanup. Do not include `frontend/index.html` or `会议记录/`.

---

## Task 2: Add MCP Access Token Settings

**Files:**
- Modify: `mcp_server/settings.py`
- Modify: `tests/test_mcp_settings.py`

- [ ] **Step 1: Write failing settings tests**

Append to `tests/test_mcp_settings.py`:

```python
def test_mcp_settings_read_access_token(monkeypatch):
    monkeypatch.setenv("MCP_ACCESS_TOKEN", "mcp-secret")

    settings = McpServerSettings.from_env()

    assert settings.access_token == "mcp-secret"


def test_mcp_settings_require_access_token_for_http():
    settings = McpServerSettings(access_token="  mcp-secret  ")

    assert settings.require_access_token() == "mcp-secret"


def test_mcp_settings_reject_empty_access_token_for_http():
    settings = McpServerSettings(access_token=" ")

    try:
        settings.require_access_token()
    except RuntimeError as exc:
        assert str(exc) == "MCP_ACCESS_TOKEN is required when --transport http is used"
    else:
        raise AssertionError("empty MCP_ACCESS_TOKEN should fail HTTP startup")
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_mcp_settings.py -q
```

Expected: tests fail because `McpServerSettings` does not yet expose `access_token` or `require_access_token()`.

- [ ] **Step 3: Implement settings support**

Update `mcp_server/settings.py` to this shape:

```python
import os
from dataclasses import dataclass


@dataclass
class McpServerSettings:
    base_url: str = "http://127.0.0.1:8000"
    api_token: str = ""
    timeout_seconds: int = 30
    page_size_limit: int = 50
    access_token: str = ""

    @classmethod
    def from_env(cls) -> "McpServerSettings":
        return cls(
            base_url=os.getenv("PATENT_SEARCH_BASE_URL", "http://127.0.0.1:8000"),
            api_token=os.getenv("PATENT_SEARCH_API_TOKEN", ""),
            timeout_seconds=_env_int("PATENT_SEARCH_TIMEOUT_SECONDS", 30),
            page_size_limit=_env_int("PATENT_SEARCH_PAGE_SIZE_LIMIT", 50),
            access_token=os.getenv("MCP_ACCESS_TOKEN", ""),
        )

    def require_access_token(self) -> str:
        token = self.access_token.strip()
        if not token:
            raise RuntimeError("MCP_ACCESS_TOKEN is required when --transport http is used")
        return token


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default
```

- [ ] **Step 4: Run settings tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mcp_settings.py -q
```

Expected: all `tests/test_mcp_settings.py` tests pass.

---

## Task 3: Add HTTP MCP CLI And Bearer Authentication

**Files:**
- Modify: `mcp_server/server.py`
- Modify: `tests/test_mcp_server.py`

- [ ] **Step 1: Write failing server tests**

Append to `tests/test_mcp_server.py`:

```python
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from mcp_server.server import BearerTokenAuthApp, parse_args


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
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_mcp_server.py -q
```

Expected: tests fail because `parse_args` and `BearerTokenAuthApp` do not exist.

- [ ] **Step 3: Implement CLI and auth wrapper**

Update `mcp_server/server.py` to include these imports:

```python
import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from mcp_server.patent_api_client import PatentApiClient
from mcp_server.settings import McpServerSettings
```

Keep the existing local import bootstrapping:

```python
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
```

Change `build_server` to accept HTTP settings:

```python
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
```

Add the ASGI auth wrapper after `build_server`:

```python
class BearerTokenAuthApp:
    def __init__(self, app, access_token: str):
        self.app = app
        self.expected_authorization = f"Bearer {access_token}"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if headers.get("authorization") != self.expected_authorization:
            response = JSONResponse({"error": "unauthorized", "code": 40101}, status_code=401)
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
```

Add HTTP app construction:

```python
def build_http_app(
    access_token: str,
    client: Optional[PatentApiClient] = None,
    host: str = "0.0.0.0",
    port: int = 9000,
):
    server = build_server(client=client, host=host, port=port, streamable_http_path="/mcp")
    return BearerTokenAuthApp(server.streamable_http_app(), access_token=access_token)
```

Add argument parsing:

```python
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Patent search MCP server")
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9000)
    return parser.parse_args(argv)
```

Replace `main()` with:

```python
def main(argv: Optional[Sequence[str]] = None) -> None:
    args = parse_args(argv)
    if args.transport == "stdio":
        build_server().run("stdio")
        return

    settings = McpServerSettings.from_env()
    access_token = settings.require_access_token()
    app = build_http_app(access_token=access_token, host=args.host, port=args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="error")
```

Keep:

```python
if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run server tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_mcp_server.py -q
```

Expected: all `tests/test_mcp_server.py` tests pass.

- [ ] **Step 5: Verify stdio CLI compatibility still works**

Run:

```bash
.venv/bin/python mcp_server/server.py --transport stdio --help >/tmp/patent-mcp-help.txt
sed -n '1,80p' /tmp/patent-mcp-help.txt
```

Expected: help output includes `--transport`, `--host`, and `--port`. This command must not start a long-running server.

---

## Task 4: Add HTTP MCP Smoke Script

**Files:**
- Create: `scripts/smoke_mcp_http.py`

- [ ] **Step 1: Create the HTTP smoke script**

Create `scripts/smoke_mcp_http.py` with:

```python
import json
import sys

import anyio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


SEARCH_CASE = {"q": "阀门", "page": 1, "page_size": 1}


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: python scripts/smoke_mcp_http.py <mcp_url> <mcp_access_token>")
        return 2

    mcp_url = sys.argv[1]
    access_token = sys.argv[2]
    return anyio.run(_run_smoke, mcp_url, access_token)


async def _run_smoke(mcp_url: str, access_token: str) -> int:
    headers = {"Authorization": f"Bearer {access_token}"}
    async with streamablehttp_client(mcp_url, headers=headers) as (read_stream, write_stream, _get_session_id):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            return await _check_tools(session)


async def _check_tools(session: ClientSession) -> int:
    tools = await session.list_tools()
    names = {tool.name for tool in tools.tools}
    required = {
        "patent_search",
        "patent_get_detail",
        "patent_get_citations",
        "patent_get_legal_history",
    }
    tools_ok = required.issubset(names)
    print(json.dumps({"name": "mcp_http_tools_list", "ok": tools_ok, "tools": sorted(names)}, ensure_ascii=False))
    if not tools_ok:
        return 1

    search = _tool_payload(await session.call_tool("patent_search", SEARCH_CASE))
    patents = search.get("patents", [])
    first_patent = patents[0] if patents else {}
    patent_id = first_patent.get("id") or first_patent.get("patent_id") or ""
    search_ok = "error" not in search and bool(patent_id) and "records" not in search
    print(
        json.dumps(
            {
                "name": "mcp_http_patent_search",
                "ok": search_ok,
                "total": search.get("total"),
                "patents": len(patents),
                "metadata": all(field in search for field in ("total_pages", "next_page", "took_ms")),
            },
            ensure_ascii=False,
        )
    )
    if not search_ok:
        return 1

    detail = _tool_payload(
        await session.call_tool(
            "patent_get_detail",
            {"patent_id": patent_id, "include_description": True},
        )
    )
    detail_ok = "error" not in detail and all(field in detail for field in ("id", "patent_id", "claims"))
    print(json.dumps({"name": "mcp_http_patent_get_detail", "ok": detail_ok}, ensure_ascii=False))
    if not detail_ok:
        return 1

    citations = _tool_payload(await session.call_tool("patent_get_citations", {"patent_id": patent_id}))
    citations_ok = "error" not in citations and all(
        field in citations for field in ("patent_id", "cited_by", "patent_references", "non_patent_references")
    )
    print(json.dumps({"name": "mcp_http_patent_get_citations", "ok": citations_ok}, ensure_ascii=False))
    if not citations_ok:
        return 1

    legal_history = _tool_payload(await session.call_tool("patent_get_legal_history", {"patent_id": patent_id}))
    legal_ok = "error" not in legal_history and all(
        field in legal_history for field in ("patent_id", "transaction_count", "transactions")
    )
    print(json.dumps({"name": "mcp_http_patent_get_legal_history", "ok": legal_ok}, ensure_ascii=False))
    if not legal_ok:
        return 1

    error = _tool_payload(
        await session.call_tool(
            "patent_search",
            {"q": "ipc:H02M AND AND tscd:(均衡)", "page": 1, "page_size": 1},
        )
    )
    error_ok = error.get("code") == 40001 and "error" in error
    print(json.dumps({"name": "mcp_http_error_conversion", "ok": error_ok, "code": error.get("code")}, ensure_ascii=False))
    return 0 if error_ok else 1


def _tool_payload(result) -> dict:
    if not result.content:
        return {"error": "empty MCP tool result", "code": 50002}
    return json.loads(result.content[0].text)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Verify script imports**

Run:

```bash
.venv/bin/python -m py_compile scripts/smoke_mcp_http.py
```

Expected: command exits with code 0.

- [ ] **Step 3: Verify usage path**

Run:

```bash
.venv/bin/python scripts/smoke_mcp_http.py
```

Expected: command exits with code 2 and prints:

```text
usage: python scripts/smoke_mcp_http.py <mcp_url> <mcp_access_token>
```

---

## Task 5: Add HTTP MCP Deployment Template And Environment Docs

**Files:**
- Create: `deployment/patent-mcp.service`
- Modify: `.env.example`
- Modify: `mcp_server/README.md`
- Modify: `docs/delivery/mcp_integration_guide.md`

- [ ] **Step 1: Add `deployment/patent-mcp.service`**

Create `deployment/patent-mcp.service`:

```ini
[Unit]
Description=Patent Search MCP Server
After=network.target patent-search-service.service

[Service]
User=work
Group=work
WorkingDirectory=/opt/patent-search-service
EnvironmentFile=/opt/patent-search-service/.env
ExecStart=/opt/patent-search-service/.venv/bin/python mcp_server/server.py --transport http --host 0.0.0.0 --port 9000
Restart=always
RestartSec=5
StandardOutput=append:/var/log/patent-search-service/patent-mcp.log
StandardError=append:/var/log/patent-search-service/patent-mcp.err.log

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Add MCP variables to `.env.example`**

Append under the existing `PATENT_SEARCH_*` variables:

```bash
# Remote HTTP MCP server authentication.
MCP_ACCESS_TOKEN=
```

Keep all secret values empty in `.env.example`.

- [ ] **Step 3: Update `mcp_server/README.md`**

Replace the "Start With stdio" and "Local Smoke" area with:

````markdown
## Start With stdio

```bash
python3 mcp_server/server.py --transport stdio
```

## Start With HTTP

```bash
export MCP_ACCESS_TOKEN="change-me"
python3 mcp_server/server.py --transport http --host 0.0.0.0 --port 9000
```

The HTTP MCP endpoint is:

```text
http://<server>:9000/mcp
```

HTTP callers must send:

```text
Authorization: Bearer <MCP_ACCESS_TOKEN>
```

## Local Smoke

Start the patent search API first, then run:

```bash
python3 scripts/smoke_mcp_server.py http://127.0.0.1:8000 "$API_TOKEN"
python3 scripts/smoke_mcp_http.py http://127.0.0.1:9000/mcp "$MCP_ACCESS_TOKEN"
```
````

- [ ] **Step 4: Rewrite `docs/delivery/mcp_integration_guide.md` for remote HTTP**

Ensure it includes these sections:

````markdown
# MCP Integration Guide

## 1. Scope

The current delivery mainline is a remote HTTP MCP Server in `mcp_server/`, backed by the self-hosted FastAPI patent search service.

The MCP Server exposes four patent tools and calls the FastAPI service through HTTP. It does not query OpenSearch directly, does not read OpenSearch settings, and does not create an OpenSearch client.

The local DeerFlow Tool plugin path is not part of the current delivery scope.

## 2. Runtime

The MCP Python SDK is pinned in `requirements.txt`:

```text
mcp==1.28.1
```

Project-level HTTP startup uses `--transport http`; internally this maps to the SDK Streamable HTTP transport.

## 3. Environment Variables

```bash
PATENT_SEARCH_BASE_URL=http://127.0.0.1:8000
PATENT_SEARCH_API_TOKEN=<provided securely>
PATENT_SEARCH_TIMEOUT_SECONDS=30
PATENT_SEARCH_PAGE_SIZE_LIMIT=50
MCP_ACCESS_TOKEN=<provided securely>
```

`PATENT_SEARCH_API_TOKEN` is for MCP-to-FastAPI calls. `MCP_ACCESS_TOKEN` is for DeerFlow/workspace-to-MCP calls.

## 4. Start

```bash
python3 mcp_server/server.py --transport stdio
python3 mcp_server/server.py --transport http --host 0.0.0.0 --port 9000
```

## 5. Company Workspace Config Example

```json
{
  "mcpServers": {
    "patent-search": {
      "enabled": true,
      "type": "http",
      "url": "http://<server-public-ip>:9000/mcp",
      "headers": {
        "Authorization": "Bearer ${PATENT_MCP_TOKEN}"
      },
      "description": "自研专利检索 MCP 服务"
    }
  },
  "skills": {}
}
```
````

Keep the existing tool table and error-shape documentation, but update smoke and rollback sections for HTTP MCP.

- [ ] **Step 5: Verify docs do not contain real secrets**

Run:

```bash
rg -n "Bearer [A-Za-z0-9._-]{12,}|OPENSEARCH_PASS=.+|API_TOKEN=.+|MCP_ACCESS_TOKEN=.+" .env.example README.md docs mcp_server deployment
```

Expected: no real token or password values. Placeholder strings like `<provided securely>`, `<MCP_ACCESS_TOKEN>`, and `${PATENT_MCP_TOKEN}` are acceptable.

---

## Task 6: Run Full Verification For HTTP MCP Delivery

**Files:**
- Read-only verification across the worktree.

- [ ] **Step 1: Run unit tests**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: all tests pass.

- [ ] **Step 2: Start a local HTTP MCP process for auth smoke**

Use a terminal session:

```bash
MCP_ACCESS_TOKEN=local-mcp-token PATENT_SEARCH_BASE_URL=http://127.0.0.1:8000 .venv/bin/python mcp_server/server.py --transport http --host 127.0.0.1 --port 9000
```

Expected: process stays running and listens on `127.0.0.1:9000`.

- [ ] **Step 3: Verify missing token is rejected**

Run in another terminal:

```bash
curl -i -X POST http://127.0.0.1:9000/mcp
```

Expected: HTTP status `401` and JSON body:

```json
{"error":"unauthorized","code":40101}
```

- [ ] **Step 4: Verify invalid token is rejected**

Run:

```bash
curl -i -X POST http://127.0.0.1:9000/mcp -H "Authorization: Bearer wrong"
```

Expected: HTTP status `401` and JSON body:

```json
{"error":"unauthorized","code":40101}
```

- [ ] **Step 5: Verify correct token reaches MCP**

Run:

```bash
.venv/bin/python scripts/smoke_mcp_http.py http://127.0.0.1:9000/mcp local-mcp-token
```

Expected if FastAPI and OpenSearch-backed data service are running: all printed JSON lines have `"ok": true`.

Expected if FastAPI is not running: the script connects to MCP but tool calls return API connection errors. Record this as live FastAPI smoke not available, then still complete unit-test verification.

- [ ] **Step 6: Run existing stdio MCP smoke if FastAPI is available**

Run:

```bash
.venv/bin/python scripts/smoke_mcp_server.py http://127.0.0.1:8000 "$API_TOKEN"
```

Expected if FastAPI is running with data: all printed JSON lines have `"ok": true`.

- [ ] **Step 7: Stop the local HTTP MCP process**

Stop the server started in Step 2 with Ctrl-C. Confirm no session remains running before final response.

- [ ] **Step 8: Check protected paths**

Run:

```bash
git status --short
git diff --name-only
```

Expected:

1. No changes under `patent_harness_base_副本/`.
2. `frontend/index.html` remains user-owned and unstaged if it was already dirty.
3. `会议记录/` remains user-owned and unstaged if it was already untracked.

---

## Task 7: Commit HTTP MCP Delivery

**Files:**
- Commit files modified in Tasks 2 through 6.

- [ ] **Step 1: Review changed files**

Run:

```bash
git status --short
git diff --stat
```

Expected: changes correspond to HTTP MCP settings, server, smoke, deployment, docs, and tests. Do not include unrelated user changes.

- [ ] **Step 2: Stage implementation files**

Run:

```bash
git add .env.example deployment/patent-mcp.service docs/delivery/mcp_integration_guide.md mcp_server/README.md mcp_server/server.py mcp_server/settings.py scripts/smoke_mcp_http.py tests/test_mcp_server.py tests/test_mcp_settings.py
```

If Task 5 also updated `README.md`, `docs/README.md`, `docs/delivery/stage12_deerflow_tool_mcp_work_plan.md`, or `docs/internal/stage12_mcp_dev_assignment.md` after the cleanup commit, add only those intended files.

- [ ] **Step 3: Confirm staged set**

Run:

```bash
git diff --cached --name-only
```

Expected staged files are only intended HTTP MCP delivery files.

- [ ] **Step 4: Commit**

Run:

```bash
git commit -m "feat: add remote HTTP MCP server deployment support"
```

Expected: second implementation commit is created.

---

## Task 8: Final Delivery Summary

**Files:**
- No file changes.

- [ ] **Step 1: Capture commit history**

Run:

```bash
git log --oneline -4
```

Expected: latest commits include:

```text
feat: add remote HTTP MCP server deployment support
chore: remove unused DeerFlow tool integration path
docs: add remote HTTP MCP implementation plan
docs: add remote HTTP MCP delivery design
```

- [ ] **Step 2: Capture final status**

Run:

```bash
git status --short
```

Expected: only pre-existing user-owned changes remain, such as `frontend/index.html` and `会议记录/`, or a clean worktree if the user has handled them.

- [ ] **Step 3: Report verification evidence**

Final response must include:

1. The two implementation commit messages.
2. Unit test command and result.
3. stdio MCP smoke result, or why live smoke could not run.
4. HTTP MCP auth check result.
5. HTTP MCP smoke result, or why live smoke could not run.
6. Confirmation that no real token or production `.env` was committed.
7. Confirmation that `patent_harness_base_副本/` was not modified.
