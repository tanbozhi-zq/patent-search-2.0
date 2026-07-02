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
