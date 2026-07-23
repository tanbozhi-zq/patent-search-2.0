from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.citations import router as citations_router
from app.api.console import router as console_router
from app.api.detail import router as detail_router
from app.api.legal_history import router as legal_history_router
from app.api.search import router as search_router
from app.core.error_handlers import register_error_handlers
from app.core.logging import configure_logging
from app.version import __version__


configure_logging()

app = FastAPI(
    title="patent-search-service",
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
)

register_error_handlers(app)

app.include_router(search_router)
app.include_router(detail_router)
app.include_router(citations_router)
app.include_router(legal_history_router)
app.include_router(console_router)

console_static_dir = Path(__file__).resolve().parent / "static" / "console"
if console_static_dir.is_dir():
    app.mount("/console", StaticFiles(directory=str(console_static_dir), html=True), name="console")
else:
    # Allow the service to start even if the console frontend has not been built/deployed yet.
    pass


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
