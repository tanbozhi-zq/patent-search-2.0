"""验证分阶段拆分后的各业务路由模块仍可独立导入并保留公开路径。"""

from app.api.citations import router as citations_router
from app.api.detail import router as detail_router
from app.api.legal_history import router as legal_history_router
from app.api.search import router as search_router


def test_stage_four_router_modules_are_importable():
    assert search_router.prefix == "/api/patent"
    assert detail_router.prefix == "/api/patent"
    assert citations_router.prefix == "/api/patent"
    assert legal_history_router.prefix == "/api/patent"
