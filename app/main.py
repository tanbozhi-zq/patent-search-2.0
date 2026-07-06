from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.citations import router as citations_router
from app.api.detail import router as detail_router
from app.api.search import router as search_router
from app.core.error_handlers import register_error_handlers
from app.core.logging import configure_logging


configure_logging()

app = FastAPI(
    title="patent-search-service",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

register_error_handlers(app)

app.include_router(search_router)
app.include_router(detail_router)
app.include_router(citations_router)

app.mount("/test", StaticFiles(directory="frontend", html=True), name="frontend")


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
