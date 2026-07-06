from fastapi import FastAPI
from fastapi.testclient import TestClient

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


def test_request_validation_error_converts_to_400_with_40002():
    client = TestClient(_app_with_extra_route())

    response = client.get("/needs-q")

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["code"] == 40002
    assert "q" in body["message"]
    assert body["data"] is None
