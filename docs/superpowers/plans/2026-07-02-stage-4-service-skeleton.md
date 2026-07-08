# Stage 4 Service Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a runnable FastAPI backend skeleton for `patent-search-service` with health check, configuration loading, API token dependency, OpenSearch client construction, tests, and deployment documentation.

**Architecture:** The service is a Python FastAPI application under `app/`. HTTP routes live in `app/api/`, configuration and cross-cutting concerns live in `app/core/`, OpenSearch access is isolated in `app/repositories/`, and tests live in `tests/`. Stage 4 must not implement patent search business logic; it only creates stable foundations for Stage 5.

**Tech Stack:** Python 3.9.19, FastAPI, Uvicorn, Pydantic Settings, OpenSearch Python Client, pytest, httpx.

## Global Constraints

- Deployment environment is production, but first deployment mode is `work` user + Python venv + FastAPI/Uvicorn + systemd.
- Docker and Nginx are not installed and are not required for Stage 4.
- Service port is `8000`.
- Service must support `X-API-Key` auth controlled by `ENABLE_AUTH=true/false` and `API_TOKEN`.
- OpenSearch host is `opensearch-o-00gcv9almneh.escloud.volces.com`, port `9200`, HTTPS enabled, index `patent_index`.
- OpenSearch uses a self-signed certificate chain; Stage 4 must expose `OPENSEARCH_VERIFY_CERTS=false` for initial deployment.
- Business API success responses in later stages should remain close to the vendor service; Stage 4 may use unified response only for `/health` and errors.
- Stage 4 must not implement complex query parsing, search DSL construction, patent detail retrieval, or citation retrieval.

---

## File Structure

Create this structure at repository root:

```text
app/
├── __init__.py
├── main.py
├── api/
│   ├── __init__.py
│   ├── citations.py
│   ├── detail.py
│   └── search.py
├── core/
│   ├── __init__.py
│   ├── config.py
│   ├── exceptions.py
│   ├── logging.py
│   └── security.py
├── repositories/
│   ├── __init__.py
│   └── opensearch_repo.py
├── schemas/
│   ├── __init__.py
│   └── response.py
scripts/
└── smoke_health.py
tests/
├── conftest.py
├── test_config.py
├── test_health.py
├── test_opensearch_repo.py
└── test_security.py
deployment/
└── patent-search-service.service
requirements.txt
pytest.ini
README.md
```

## Task 1: Dependency And Test Scaffold

**Files:**
- Create: `requirements.txt`
- Create: `pytest.ini`
- Create: `app/__init__.py`
- Create: `app/api/__init__.py`
- Create: `app/core/__init__.py`
- Create: `app/repositories/__init__.py`
- Create: `app/schemas/__init__.py`
- Create: `tests/conftest.py`

**Interfaces:**
- Produces: Python package imports under `app`.
- Produces: `client()` pytest fixture returning `fastapi.testclient.TestClient`.

- [ ] **Step 1: Create dependency files**

Create `requirements.txt`:

```txt
fastapi==0.115.6
uvicorn[standard]==0.34.0
pydantic-settings==2.7.1
opensearch-py==2.8.0
python-dotenv==1.0.1
pytest==8.3.4
httpx==0.27.2
```

Create `pytest.ini`:

```ini
[pytest]
testpaths = tests
pythonpath = .
```

- [ ] **Step 2: Create package marker files**

Create these files with empty content:

```text
app/__init__.py
app/api/__init__.py
app/core/__init__.py
app/repositories/__init__.py
app/schemas/__init__.py
```

- [ ] **Step 3: Create the test fixture**

Create `tests/conftest.py`:

```python
from fastapi.testclient import TestClient

from app.main import app


def client() -> TestClient:
    return TestClient(app)
```

- [ ] **Step 4: Run tests to verify scaffold state**

Run:

```bash
python3 -m pytest -q
```

Expected: test collection succeeds; if Task 2 has not been implemented yet, this may fail with `ModuleNotFoundError: No module named 'app.main'`.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt pytest.ini app tests/conftest.py
git commit -m "chore: scaffold python service test setup"
```

## Task 2: FastAPI Application And Health Check

**Files:**
- Create: `app/schemas/response.py`
- Create: `app/main.py`
- Create: `tests/test_health.py`

**Interfaces:**
- Produces: `app.main.app: FastAPI`
- Produces: `GET /health`

- [ ] **Step 1: Write the failing health test**

Create `tests/test_health.py`:

```python
def test_health_returns_service_status(client):
    response = client().get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "code": 0,
        "message": "ok",
        "data": {
            "status": "healthy",
            "service": "patent-search-service",
        },
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_health.py -q
```

Expected: FAIL because `app.main` or `/health` is not implemented.

- [ ] **Step 3: Implement shared response schema**

Create `app/schemas/response.py`:

```python
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel


T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    success: bool
    code: int
    message: str
    data: Optional[T] = None
```

- [ ] **Step 4: Implement FastAPI app and health route**

Create `app/main.py`:

```python
from fastapi import FastAPI


app = FastAPI(
    title="patent-search-service",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)


@app.get("/health")
def health():
    return {
        "success": True,
        "code": 0,
        "message": "ok",
        "data": {
            "status": "healthy",
            "service": "patent-search-service",
        },
    }
```

- [ ] **Step 5: Run the health test**

Run:

```bash
python3 -m pytest tests/test_health.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/schemas/response.py tests/test_health.py
git commit -m "feat: add health check endpoint"
```

## Task 3: Configuration Loading

**Files:**
- Create: `app/core/config.py`
- Modify: `.env.example`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings`
- Produces: `get_settings() -> Settings`

- [ ] **Step 1: Write configuration tests**

Create `tests/test_config.py`:

```python
from app.core.config import Settings


def test_settings_defaults_match_stage_four_contract():
    settings = Settings()

    assert settings.service_name == "patent-search-service"
    assert settings.service_host == "0.0.0.0"
    assert settings.service_port == 8000
    assert settings.enable_auth is True
    assert settings.opensearch_port == 9200
    assert settings.opensearch_use_https is True
    assert settings.opensearch_index == "patent_index"
    assert settings.opensearch_verify_certs is False


def test_opensearch_url_uses_https_when_enabled():
    settings = Settings(
        opensearch_host="example.com",
        opensearch_port=9200,
        opensearch_use_https=True,
    )

    assert settings.opensearch_url == "https://example.com:9200"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_config.py -q
```

Expected: FAIL because `app.core.config` is not implemented.

- [ ] **Step 3: Implement settings**

Create `app/core/config.py`:

```python
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "patent-search-service"
    service_host: str = "0.0.0.0"
    service_port: int = 8000

    enable_auth: bool = True
    api_token: str = Field(default="", repr=False)

    opensearch_host: str = "opensearch-o-00gcv9almneh.escloud.volces.com"
    opensearch_port: int = 9200
    opensearch_use_https: bool = True
    opensearch_user: str = ""
    opensearch_pass: str = Field(default="", repr=False)
    opensearch_index: str = "patent_index"
    opensearch_verify_certs: bool = False
    opensearch_timeout_seconds: int = 30

    @property
    def opensearch_url(self) -> str:
        scheme = "https" if self.opensearch_use_https else "http"
        return f"{scheme}://{self.opensearch_host}:{self.opensearch_port}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Update environment example**

Replace `.env.example` with:

```env
SERVICE_NAME=patent-search-service
SERVICE_HOST=0.0.0.0
SERVICE_PORT=8000

ENABLE_AUTH=true
API_TOKEN=

OPENSEARCH_HOST=opensearch-o-00gcv9almneh.escloud.volces.com
OPENSEARCH_PORT=9200
OPENSEARCH_USE_HTTPS=true
OPENSEARCH_USER=
OPENSEARCH_PASS=
OPENSEARCH_INDEX=patent_index
OPENSEARCH_VERIFY_CERTS=false
OPENSEARCH_TIMEOUT_SECONDS=30
```

- [ ] **Step 5: Run configuration tests**

Run:

```bash
python3 -m pytest tests/test_config.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/core/config.py .env.example tests/test_config.py
git commit -m "feat: add service configuration loading"
```

## Task 4: API Token Security Dependency

**Files:**
- Create: `app/core/security.py`
- Create: `tests/test_security.py`

**Interfaces:**
- Consumes: `Settings`
- Produces: `require_api_key(x_api_key: str | None, settings: Settings) -> None`

- [ ] **Step 1: Write security tests**

Create `tests/test_security.py`:

```python
import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.core.security import require_api_key


def test_require_api_key_allows_requests_when_auth_disabled():
    settings = Settings(enable_auth=False, api_token="")

    assert require_api_key(x_api_key=None, settings=settings) is None


def test_require_api_key_allows_matching_token():
    settings = Settings(enable_auth=True, api_token="secret-token")

    assert require_api_key(x_api_key="secret-token", settings=settings) is None


def test_require_api_key_rejects_missing_token():
    settings = Settings(enable_auth=True, api_token="secret-token")

    with pytest.raises(HTTPException) as exc:
        require_api_key(x_api_key=None, settings=settings)

    assert exc.value.status_code == 401
    assert exc.value.detail == {
        "success": False,
        "code": 40101,
        "message": "missing or invalid X-API-Key",
        "data": None,
    }


def test_require_api_key_rejects_wrong_token():
    settings = Settings(enable_auth=True, api_token="secret-token")

    with pytest.raises(HTTPException) as exc:
        require_api_key(x_api_key="wrong-token", settings=settings)

    assert exc.value.status_code == 401
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_security.py -q
```

Expected: FAIL because `app.core.security` is not implemented.

- [ ] **Step 3: Implement security dependency**

Create `app/core/security.py`:

```python
from typing import Optional

from fastapi import Header, HTTPException

from app.core.config import Settings, get_settings


def require_api_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    settings: Settings = get_settings(),
) -> None:
    if not settings.enable_auth:
        return None

    if settings.api_token and x_api_key == settings.api_token:
        return None

    raise HTTPException(
        status_code=401,
        detail={
            "success": False,
            "code": 40101,
            "message": "missing or invalid X-API-Key",
            "data": None,
        },
    )
```

- [ ] **Step 4: Run security tests**

Run:

```bash
python3 -m pytest tests/test_security.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/core/security.py tests/test_security.py
git commit -m "feat: add api token security dependency"
```

## Task 5: OpenSearch Client Construction

**Files:**
- Create: `app/repositories/opensearch_repo.py`
- Create: `tests/test_opensearch_repo.py`

**Interfaces:**
- Consumes: `Settings`
- Produces: `OpenSearchRepository`
- Produces: `OpenSearchRepository.client`

- [ ] **Step 1: Write OpenSearch repository tests**

Create `tests/test_opensearch_repo.py`:

```python
from app.core.config import Settings
from app.repositories.opensearch_repo import OpenSearchRepository


def test_repository_stores_index_name():
    settings = Settings(opensearch_index="patent_index")
    repository = OpenSearchRepository(settings=settings)

    assert repository.index_name == "patent_index"


def test_repository_builds_client_with_configured_url():
    settings = Settings(
        opensearch_host="example.com",
        opensearch_port=9200,
        opensearch_use_https=True,
        opensearch_user="user",
        opensearch_pass="pass",
        opensearch_verify_certs=False,
        opensearch_timeout_seconds=15,
    )
    repository = OpenSearchRepository(settings=settings)

    assert repository.hosts == ["https://example.com:9200"]
    assert repository.http_auth == ("user", "pass")
    assert repository.verify_certs is False
    assert repository.timeout == 15
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_opensearch_repo.py -q
```

Expected: FAIL because `app.repositories.opensearch_repo` is not implemented.

- [ ] **Step 3: Implement OpenSearch repository**

Create `app/repositories/opensearch_repo.py`:

```python
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
```

- [ ] **Step 4: Run OpenSearch repository tests**

Run:

```bash
python3 -m pytest tests/test_opensearch_repo.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/repositories/opensearch_repo.py tests/test_opensearch_repo.py
git commit -m "feat: add opensearch client repository"
```

## Task 6: API Router Placeholders And Delivery Assets

**Files:**
- Create: `app/api/search.py`
- Create: `app/api/detail.py`
- Create: `app/api/citations.py`
- Create: `app/core/exceptions.py`
- Create: `app/core/logging.py`
- Modify: `app/main.py`
- Create: `scripts/smoke_health.py`
- Create: `deployment/patent-search-service.service`
- Create: `README.md`
- Create: `tests/test_router_imports.py`

**Interfaces:**
- Produces: importable router modules for Stage 5.
- Produces: runnable local command `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
- Produces: smoke script `python3 scripts/smoke_health.py http://127.0.0.1:8000`.

- [ ] **Step 1: Write router import test**

Create `tests/test_router_imports.py`:

```python
from app.api.citations import router as citations_router
from app.api.detail import router as detail_router
from app.api.search import router as search_router


def test_stage_four_router_modules_are_importable():
    assert search_router.prefix == "/api/patent"
    assert detail_router.prefix == "/api/patent"
    assert citations_router.prefix == "/api/patent"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_router_imports.py -q
```

Expected: FAIL because router modules are not implemented.

- [ ] **Step 3: Create empty router modules for later stages**

Create `app/api/search.py`:

```python
from fastapi import APIRouter


router = APIRouter(prefix="/api/patent", tags=["patent-search"])
```

Create `app/api/detail.py`:

```python
from fastapi import APIRouter


router = APIRouter(prefix="/api/patent", tags=["patent-detail"])
```

Create `app/api/citations.py`:

```python
from fastapi import APIRouter


router = APIRouter(prefix="/api/patent", tags=["patent-citations"])
```

- [ ] **Step 4: Add exceptions and logging foundations**

Create `app/core/exceptions.py`:

```python
from fastapi import HTTPException


def service_error(status_code: int, code: int, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={
            "success": False,
            "code": code,
            "message": message,
            "data": None,
        },
    )
```

Create `app/core/logging.py`:

```python
import logging


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
```

- [ ] **Step 5: Include router modules in app startup**

Update `app/main.py`:

```python
from fastapi import FastAPI

from app.api.citations import router as citations_router
from app.api.detail import router as detail_router
from app.api.search import router as search_router
from app.core.logging import configure_logging


configure_logging()

app = FastAPI(
    title="patent-search-service",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.include_router(search_router)
app.include_router(detail_router)
app.include_router(citations_router)


@app.get("/health")
def health():
    return {
        "success": True,
        "code": 0,
        "message": "ok",
        "data": {
            "status": "healthy",
            "service": "patent-search-service",
        },
    }
```

- [ ] **Step 6: Add smoke script**

Create `scripts/smoke_health.py`:

```python
import sys

import httpx


def main() -> int:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    response = httpx.get(f"{base_url.rstrip('/')}/health", timeout=10)
    response.raise_for_status()
    payload = response.json()
    if payload.get("data", {}).get("status") != "healthy":
        raise RuntimeError(f"unexpected health payload: {payload}")
    print("health ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: Add systemd unit template**

Create `deployment/patent-search-service.service`:

```ini
[Unit]
Description=Patent Search Service
After=network.target

[Service]
User=work
Group=work
WorkingDirectory=/opt/patent-search-service
EnvironmentFile=/opt/patent-search-service/.env
ExecStart=/opt/patent-search-service/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
StandardOutput=append:/var/log/patent-search-service/service.log
StandardError=append:/var/log/patent-search-service/error.log

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 8: Add README**

Create `README.md`:

```markdown
# patent-search-service

Self-hosted patent search backend service based on FastAPI and OpenSearch.

## Stage

Current stage: Stage 4 service skeleton.

Implemented in this stage:

- FastAPI app
- `GET /health`
- `.env` configuration loading
- `X-API-Key` auth dependency
- OpenSearch client construction
- pytest test scaffold
- systemd deployment template

Not implemented in this stage:

- Patent search query parsing
- OpenSearch search DSL
- Patent detail lookup
- Citation lookup

## Local Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python3 -m pytest -q
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Health Check

```bash
curl http://127.0.0.1:8000/health
python3 scripts/smoke_health.py http://127.0.0.1:8000
```

## Production Deployment Shape

The first deployment uses:

- `work` Linux user
- Python venv
- FastAPI/Uvicorn
- systemd
- port `8000`
- `.env` for secrets

OpenSearch credentials must not be committed to Git.
```

- [ ] **Step 9: Run full tests**

Run:

```bash
python3 -m pytest -q
```

Expected: PASS for all tests created in Stage 4.

- [ ] **Step 10: Start service and run smoke test**

Run:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In another shell:

```bash
python3 scripts/smoke_health.py http://127.0.0.1:8000
```

Expected: `health ok`.

- [ ] **Step 11: Commit**

```bash
git add app scripts deployment README.md tests
git commit -m "chore: add service delivery skeleton"
```

## Stage 4 Acceptance Checklist

- [ ] `python3 -m pytest -q` passes.
- [ ] `uvicorn app.main:app --host 127.0.0.1 --port 8000` starts locally.
- [ ] `GET /health` returns `success=true`, `code=0`, and `data.status=healthy`.
- [ ] `.env.example` contains service, auth, and OpenSearch settings.
- [ ] OpenSearch client construction is covered by unit tests without requiring a live OpenSearch connection.
- [ ] `X-API-Key` auth dependency is covered by unit tests.
- [ ] `deployment/patent-search-service.service` matches venv + systemd deployment mode.
- [ ] Stage 4 does not implement search, detail, citation, query parser, or DSL business logic.

## Self-Review

Spec coverage:

- `GET /health`: Task 2.
- `.env` configuration: Task 3.
- OpenSearch configuration and client wrapper: Task 5.
- `X-API-Key` auth foundation: Task 4.
- FastAPI runnable skeleton: Tasks 2 and 6.
- systemd deployment shape: Task 6.
- Tests: all tasks.

Placeholder scan:

- No placeholder implementation is allowed in Stage 4 code.
- Empty routers are allowed because they intentionally expose no business routes in this stage.

Type consistency:

- `Settings` is defined in Task 3 and consumed by Tasks 4 and 5.
- `require_api_key` signature is defined in Task 4.
- `OpenSearchRepository` is defined in Task 5 and stores `index_name`, `hosts`, `http_auth`, `verify_certs`, and `timeout`.
