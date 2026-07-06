from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.core.error_handlers import register_error_handlers
from app.core.exceptions import service_error


def _app_with_extra_route():
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/raise-service-error")
    def _raise():
        raise service_error(400, 40002, "参数非法：page must be greater than 1")

    @app.get("/raise-plain-http-exception")
    def _raise_plain():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="plain string detail")

    @app.get("/raise-unexpected")
    def _raise_unexpected():
        raise RuntimeError("database password leaked")

    @app.get("/needs-q")
    def _needs_q(q: str):
        return {"q": q}

    return app


def test_service_error_payload_is_returned_flat_without_detail_wrapper():
    client = TestClient(_app_with_extra_route())

    response = client.get("/raise-service-error")

    assert response.status_code == 400
    assert response.json() == {
        "success": False,
        "code": 40002,
        "message": "参数非法：page must be greater than 1",
        "data": None,
    }


def test_plain_http_exception_detail_becomes_flat_envelope():
    client = TestClient(_app_with_extra_route())

    response = client.get("/raise-plain-http-exception")

    assert response.status_code == 404
    assert response.json() == {
        "success": False,
        "code": 40400,
        "message": "plain string detail",
        "data": None,
    }


def test_route_not_found_becomes_flat_envelope():
    client = TestClient(_app_with_extra_route())

    response = client.get("/missing-route")

    assert response.status_code == 404
    assert response.json()["success"] is False
    assert response.json()["code"] == 40400
    assert "detail" not in response.json()


def test_request_validation_error_converts_to_400_with_40002():
    client = TestClient(_app_with_extra_route())

    response = client.get("/needs-q")

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["code"] == 40002
    assert "q" in body["message"]
    assert body["data"] is None


def test_pagination_validation_error_uses_40003():
    app = FastAPI()
    register_error_handlers(app)

    class FakeRequest(BaseModel):
        q: str
        page: int = Field(ge=1)

    @app.post("/search")
    def _search(request: FakeRequest):
        return {"ok": True}

    client = TestClient(app)
    response = client.post("/search", json={"q": "阀门", "page": 0})

    assert response.status_code == 400
    assert response.json()["success"] is False
    assert response.json()["code"] == 40003
    assert response.json()["data"] is None


def test_unexpected_exception_converts_to_50002_without_internal_detail():
    client = TestClient(_app_with_extra_route(), raise_server_exceptions=False)

    response = client.get("/raise-unexpected")

    assert response.status_code == 500
    assert response.json() == {
        "success": False,
        "code": 50002,
        "message": "服务内部异常",
        "data": None,
    }
    assert "database password leaked" not in response.text
