"""/metrics 是进程本地观测出口。它不访问 OpenSearch、不走业务鉴权和舱壁，
由部署网络边界负责限制抓取来源。
"""

from fastapi import APIRouter, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST

from app.core.metrics import ServiceMetrics


router = APIRouter(tags=["observability"])


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    # ServiceMetrics 使用独立 CollectorRegistry，避免测试或多次导入时污染
    # prometheus_client 的全局注册表。
    service_metrics: ServiceMetrics = request.app.state.service_metrics
    return Response(
        content=service_metrics.render(),
        headers={
            "Content-Type": CONTENT_TYPE_LATEST,
            "Cache-Control": "no-store",
        },
    )
