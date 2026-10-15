"""Administrative relation review; content is published only by the existing trusted admin."""

from fastapi import APIRouter, Depends, Request, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.agent.relations import Relation
from app.production.agent_models import GraphRevision, GraphRelation, GraphActivation
from app.production.identity import Principal, require_admin, require_principal
from app.production.relations import RelationStore

router = APIRouter(prefix="/api/v1/agent", tags=["agent evidence"])


class CandidateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    knowledge_revision: str = Field(min_length=1, max_length=64)
    relations: list[Relation] = Field(min_length=1, max_length=100)


@router.post("/graphs/candidates", status_code=201)
async def candidates(
    body: CandidateInput,
    request: Request,
    principal: Principal = Depends(require_admin),
):
    store = RelationStore(request.app.state.session_factory)
    graph = await store.create_candidates(
        principal.user_id, body.knowledge_revision, body.relations
    )
    return {"graph_version": graph, "status": "candidate"}


@router.get("/graphs/{graph_id}")
async def preview(
    graph_id: str, request: Request, principal: Principal = Depends(require_admin)
):
    store = RelationStore(request.app.state.session_factory)
    async with store.sessions() as session:
        graph = await session.get(GraphRevision, graph_id)
        if graph is None:
            raise HTTPException(404, "graph_not_found")
        rows = list(
            await session.scalars(
                select(GraphRelation)
                .where(GraphRelation.graph_id == graph_id)
                .limit(100)
            )
        )
        edges = []
        for row in rows:
            chunk = await store.source(session, principal.user_id, row.source)
            if chunk is None:
                edges.append({"source_available": False})
            else:
                edges.append(
                    {
                        "left": row.left_entity,
                        "right": row.right_entity,
                        "predicate": row.predicate,
                        "source": row.source,
                        "source_available": True,
                    }
                )
        return {
            "graph_version": graph.id,
            "knowledge_revision": graph.knowledge_revision,
            "status": graph.status,
            "created_at": graph.created_at,
            "activated_at": graph.activated_at,
            "relations": edges,
        }


@router.get("/graphs")
async def list_graphs(
    request: Request,
    principal: Principal = Depends(require_admin),
    knowledge_revision: str | None = None,
    limit: int = Query(20, ge=1, le=100),
):
    async with request.app.state.session_factory() as session:
        query = select(GraphRevision, GraphActivation.graph_id).outerjoin(
            GraphActivation,
            GraphActivation.knowledge_revision == GraphRevision.knowledge_revision,
        )
        if knowledge_revision:
            query = query.where(GraphRevision.knowledge_revision == knowledge_revision)
        rows = (
            await session.execute(
                query.order_by(GraphRevision.created_at.desc(), GraphRevision.id).limit(
                    limit
                )
            )
        ).all()
        return {
            "items": [
                {
                    "graph_version": g.id,
                    "knowledge_revision": g.knowledge_revision,
                    "status": g.status,
                    "active": active == g.id,
                    "created_at": g.created_at,
                }
                for g, active in rows
            ]
        }


@router.post("/graphs/{graph_id}/publish")
async def publish(
    graph_id: str, request: Request, principal: Principal = Depends(require_admin)
):
    return {
        "graph_version": await RelationStore(request.app.state.session_factory).publish(
            principal.user_id, graph_id
        ),
        "status": "published",
    }


@router.post("/graphs/{graph_id}/activate")
async def activate(
    graph_id: str, request: Request, principal: Principal = Depends(require_admin)
):
    return {
        "graph_version": await RelationStore(
            request.app.state.session_factory
        ).activate_graph(principal.user_id, graph_id),
        "status": "active",
    }
