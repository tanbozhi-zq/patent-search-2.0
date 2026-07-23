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


def test_openapi_uses_the_service_version(client):
    response = client().get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["version"] == "0.2.0"
