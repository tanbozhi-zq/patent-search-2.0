"""pytest 共享夹具与应用构造约定，供各 API、服务和基础设施测试复用。"""

from contextlib import ExitStack
import os

import pytest
from fastapi.testclient import TestClient


os.environ["PATENT_SEARCH_BULKHEAD_CAPACITY"] = "4"
os.environ["PATENT_SEARCH_HEAVY_BULKHEAD_CAPACITY"] = "3"
os.environ["PATENT_SEARCH_BULKHEAD_ACQUIRE_TIMEOUT_SECONDS"] = "0.01"
os.environ["CONSOLE_USERNAME"] = "console-test-user"
os.environ["CONSOLE_PASSWORD"] = "console-test-password"


from app.main import app


@pytest.fixture
def client():
    stack = ExitStack()

    def _factory() -> TestClient:
        return stack.enter_context(TestClient(app))

    yield _factory
    stack.close()
