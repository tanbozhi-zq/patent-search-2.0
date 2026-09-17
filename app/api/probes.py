"""探针返回最小状态，不泄露 OpenSearch 地址、索引或原始异常；详细诊断留在
结构化日志和内部管理面板，避免编排系统把敏感信息当作健康响应传播。
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.probes import ReadinessProbe, ServiceLifecycle
from app.schemas.response import ProbeResponse


router = APIRouter(tags=["probes"])


def _unavailable(status: str) -> JSONResponse:
    # 探针失败固定为 503，响应体只保留编排所需的状态词。
    return JSONResponse(status_code=503, content={"status": status})


@router.get("/live", response_model=ProbeResponse)
async def live() -> ProbeResponse:
    # live 只表示进程还能够执行到路由，不触发任何外部依赖检查。
    return ProbeResponse(status="live")


@router.get(
    "/startup",
    response_model=ProbeResponse,
    responses={503: {"model": ProbeResponse}},
)
async def startup(request: Request) -> ProbeResponse | JSONResponse:
    # startup 表示 lifespan 的一次性初始化已经成功；它不是 OpenSearch 可用性检查。
    lifecycle: ServiceLifecycle | None = getattr(
        request.app.state,
        "probe_lifecycle",
        None,
    )
    if lifecycle is None or not lifecycle.is_started:
        return _unavailable("not_started")
    return ProbeResponse(status="started")


@router.get(
    "/ready",
    response_model=ProbeResponse,
    responses={503: {"model": ProbeResponse}},
)
async def ready(request: Request) -> ProbeResponse | JSONResponse:
    # ready 在 startup 之后才会访问带缓存/合并的廉价 OpenSearch 检查。
    lifecycle: ServiceLifecycle | None = getattr(
        request.app.state,
        "probe_lifecycle",
        None,
    )
    readiness_probe: ReadinessProbe | None = getattr(
        request.app.state,
        "readiness_probe",
        None,
    )
    if lifecycle is None or not lifecycle.is_started or readiness_probe is None:
        return _unavailable("not_ready")
    if not await readiness_probe.is_ready():
        return _unavailable("not_ready")
    return ProbeResponse(status="ready")
