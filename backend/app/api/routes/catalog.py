from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.routes.tickets import ApiProblem
from app.services.catalog import CatalogNotFoundError, CatalogService, SourceOutOfSyncError


router = APIRouter(prefix="/api", tags=["catalog"])


def get_catalog_service(request: Request) -> CatalogService:
    return request.app.state.catalog_service


@router.get("/knowledge")
async def list_knowledge(
    user_id: Annotated[str, Query(min_length=1, max_length=64)],
    service: Annotated[CatalogService, Depends(get_catalog_service)],
) -> dict[str, object]:
    try:
        return await service.list_documents(user_id)
    except CatalogNotFoundError as error:
        raise ApiProblem(404, "user_not_found", "演示用户不存在。") from error


@router.get("/knowledge/{document_id}")
async def get_knowledge_article(
    document_id: str,
    user_id: Annotated[str, Query(min_length=1, max_length=64)],
    service: Annotated[CatalogService, Depends(get_catalog_service)],
) -> dict[str, object]:
    try:
        return await service.get_document(user_id, document_id)
    except CatalogNotFoundError as error:
        raise ApiProblem(404, "knowledge_not_found", "资料不存在或当前用户无权查看。") from error
    except SourceOutOfSyncError as error:
        raise ApiProblem(409, "knowledge_out_of_sync", "资料文件与索引不一致，请重新导入知识库。") from error


@router.get("/tickets")
async def list_user_tickets(
    user_id: Annotated[str, Query(min_length=1, max_length=64)],
    service: Annotated[CatalogService, Depends(get_catalog_service)],
) -> dict[str, object]:
    try:
        return await service.list_tickets(user_id)
    except CatalogNotFoundError as error:
        raise ApiProblem(404, "user_not_found", "演示用户不存在。") from error
