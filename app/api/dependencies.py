"""路由层只通过这些依赖取得共享资源和服务对象。这样业务函数不需要知道
app.state 的具体布局，也方便测试时替换某一层而不触碰真正的 OpenSearch。
"""

from typing import AsyncIterator

from fastapi import Depends, Request

from app.core.bulkhead import ApplicationBulkhead
from app.integrations.query_vector import QueryVectorAdapter
from app.repositories.opensearch_repo import OpenSearchRepository
from app.query.budget import QueryBudgetProvider, StaticQueryBudgetProvider
from app.services.citation_service import CitationService
from app.services.detail_service import DetailService
from app.services.legal_history_service import LegalHistoryService
from app.services.search_service import SearchService


def get_opensearch_repository(request: Request) -> OpenSearchRepository:
    # Repository 在 lifespan 中创建一次；请求依赖只返回当前 worker 的实例。
    return request.app.state.opensearch_repository


def get_query_budget_provider(request: Request) -> QueryBudgetProvider:
    # 请求体中间件会在进入 Pydantic/路由前冻结一份预算快照。后续即使配置
    # 提供器被替换，本次请求仍使用同一组限制，避免中途改变安全边界。
    snapshot = getattr(request.state, "query_budget_snapshot", None)
    if snapshot is not None:
        return StaticQueryBudgetProvider(snapshot)
    return request.app.state.query_budget_provider


def get_query_vector_adapter(request: Request) -> QueryVectorAdapter:
    return request.app.state.query_vector_adapter


async def acquire_search_request_slot(request: Request) -> AsyncIterator[None]:
    # 详情、引证、法律状态等轻请求只占用全局槽位。
    bulkhead: ApplicationBulkhead = request.app.state.search_request_bulkhead
    async with bulkhead.slot():
        yield


async def acquire_heavy_search_request_slot(request: Request) -> AsyncIterator[None]:
    heavy_bulkhead: ApplicationBulkhead = (
        request.app.state.heavy_search_request_bulkhead
    )
    global_bulkhead: ApplicationBulkhead = request.app.state.search_request_bulkhead
    # 先拿更窄的重请求许可，再拿全局许可。这样重请求被拒绝时不会短暂占用
    # 留给轻请求的全局容量，也避免进入 Repository 后才发现没有可用配额。
    async with heavy_bulkhead.slot():
        async with global_bulkhead.slot():
            yield


def get_search_service(
    request: Request,
    repository: OpenSearchRepository = Depends(get_opensearch_repository),
    query_budget_provider: QueryBudgetProvider = Depends(
        get_query_budget_provider
    ),
    query_vector_adapter: QueryVectorAdapter = Depends(get_query_vector_adapter),
) -> SearchService:
    # Service 本身是轻量的无状态编排器；Repository 和预算提供器仍由 worker 共享。
    return SearchService(
        repository,
        query_budget_provider=query_budget_provider,
        query_vector_adapter=query_vector_adapter,
        metrics=getattr(request.app.state, "service_metrics", None),
    )


def get_detail_service(
    repository: OpenSearchRepository = Depends(get_opensearch_repository),
) -> DetailService:
    return DetailService(repository)


def get_citation_service(
    repository: OpenSearchRepository = Depends(get_opensearch_repository),
) -> CitationService:
    return CitationService(repository)


def get_legal_history_service(
    repository: OpenSearchRepository = Depends(get_opensearch_repository),
) -> LegalHistoryService:
    return LegalHistoryService(repository)
