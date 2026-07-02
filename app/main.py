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
