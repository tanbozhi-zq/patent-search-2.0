import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    def _factory() -> TestClient:
        return TestClient(app)

    return _factory
