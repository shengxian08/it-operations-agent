"""Publication, immutable graph content and current/snapshot ACL intersections."""

from dataclasses import replace
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select, or_
from sqlalchemy.dialects.postgresql import insert

from app.agent.relations import Relation
from app.production.agent_models import GraphRelation, GraphRevision, GraphActivation
from app.production.knowledge_models import (
    KnowledgeRevision,
    KnowledgeSource,
    KnowledgeSnapshot,
    RevisionChunk,
)
from app.production.ticket_progress import current_role
from app.rag.retriever import ACCESS_HIERARCHY, _to_hit


async def stage_revision_graph(session, user_id, revision):
    """New knowledge snapshots get a candidate graph, never automatic publication."""
    from app.agent.relations import extract_candidates

    graph = GraphRevision(
        knowledge_revision=revision, status="candidate", created_by=user_id
    )
    session.add(graph)
    await session.flush()
    chunks = await session.scalars(
        select(RevisionChunk)
        .where(RevisionChunk.revision_id == revision)
        .order_by(RevisionChunk.id)
    )
    total = 0
    seen = set()
    for chunk in chunks:
        source = {
            "document_id": chunk.document_id,
            "chunk_index": chunk.chunk_index,
            "index_revision": revision,
        }
        for edge in extract_candidates(chunk.content, source):
            key = (
                edge.left,
                edge.right,
                edge.predicate,
                chunk.document_id,
                chunk.chunk_index,
            )
            if key in seen:
                continue
            seen.add(key)
            session.add(
                GraphRelation(
                    graph_id=graph.id,
                    left_entity=edge.left,
                    right_entity=edge.right,
                    predicate=edge.predicate,
                    document_id=chunk.document_id,
                    chunk_index=chunk.chunk_index,
                    source=edge.source,
                )
            )
            total += 1
            if total >= 100:
                return graph.id
    return graph.id


class RelationStore:
    def __init__(self, sessions):
        self.sessions = sessions

    async def source(self, session, user_id, reference, access_level=None):
        role = await current_role(session, user_id)
        if access_level is not None:
            ranks = {"employee": 0, "support": 1, "admin": 2}
            if access_level not in ranks:
                raise HTTPException(403, "invalid_access_ceiling")
            role = min((role, access_level), key=lambda r: ranks[r])
        allowed = ACCESS_HIERARCHY[role]
        row = (
            await session.execute(
                select(RevisionChunk, KnowledgeSource, KnowledgeSnapshot)
                .join(KnowledgeSource, KnowledgeSource.id == RevisionChunk.document_id)
                .join(
                    KnowledgeSnapshot,
                    (KnowledgeSnapshot.document_id == RevisionChunk.document_id)
                    & (KnowledgeSnapshot.revision_id == RevisionChunk.revision_id),
                )
                .where(
                    RevisionChunk.revision_id == reference.get("index_revision"),
                    RevisionChunk.document_id == reference.get("document_id"),
                    RevisionChunk.chunk_index == reference.get("chunk_index"),
                    RevisionChunk.access_level.in_(allowed),
                    KnowledgeSnapshot.access_level.in_(allowed),
                    KnowledgeSource.access_level.in_(allowed),
                    KnowledgeSource.status == "active",
                )
            )
        ).first()
        return row[0] if row else None

    async def create_candidates(self, user_id, revision, edges):
        if not 1 <= len(edges) <= 100 or any(e.revision != revision for e in edges):
            raise HTTPException(422, "invalid_graph_candidates")
        async with self.sessions.begin() as session:
            if await current_role(session, user_id) != "admin":
                raise HTTPException(403, "admin_required")
            if await session.get(KnowledgeRevision, revision) is None:
                raise HTTPException(404, "knowledge_revision_not_found")
            graph = GraphRevision(
                knowledge_revision=revision, status="candidate", created_by=user_id
            )
            session.add(graph)
            await session.flush()
            seen = set()
            for edge in edges:
                key = (
                    edge.left,
                    edge.right,
                    edge.predicate,
                    edge.source["document_id"],
                    edge.source["chunk_index"],
                )
                if key in seen:
                    continue
                seen.add(key)
                chunk = await self.source(session, user_id, edge.source)
                quote = edge.source.get("quote")
                if (
                    chunk is None
                    or not isinstance(quote, str)
                    or not quote
                    or quote not in chunk.content
                ):
                    raise HTTPException(422, "relation_source_not_supported")
                explicit = f"{edge.left} {edge.predicate} {edge.right}"
                if explicit not in quote:
                    raise HTTPException(
                        422, "relation_requires_explicit_source_or_reviewed_config"
                    )
                session.add(
                    GraphRelation(
                        graph_id=graph.id,
                        left_entity=edge.left,
                        right_entity=edge.right,
                        predicate=edge.predicate,
                        document_id=edge.source["document_id"],
                        chunk_index=edge.source["chunk_index"],
                        source=edge.source,
                    )
                )
            return graph.id

    async def publish(self, user_id, graph_id):
        async with self.sessions.begin() as session:
            if await current_role(session, user_id) != "admin":
                raise HTTPException(403, "admin_required")
            initial = await session.get(GraphRevision, graph_id)
            if initial is None:
                raise HTTPException(404, "graph_not_found")
            await session.scalar(
                select(KnowledgeRevision)
                .where(KnowledgeRevision.id == initial.knowledge_revision)
                .with_for_update()
            )
            graph = await session.scalar(
                select(GraphRevision)
                .where(GraphRevision.id == graph_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if graph is None:
                raise HTTPException(404, "graph_not_found")
            if graph.status == "published":
                return graph.id
            edges = list(
                await session.scalars(
                    select(GraphRelation).where(GraphRelation.graph_id == graph_id)
                )
            )
            for edge in edges:
                if await self.source(session, user_id, edge.source) is None:
                    raise HTTPException(409, "graph_source_access_changed")
            graph.status, graph.reviewed_by = "published", user_id
            graph.activated_at = datetime.now(timezone.utc)
            await session.execute(
                insert(GraphActivation)
                .values(knowledge_revision=graph.knowledge_revision, graph_id=graph.id)
                .on_conflict_do_update(
                    index_elements=[GraphActivation.knowledge_revision],
                    set_={"graph_id": graph.id},
                )
            )
            return graph.id

    async def activate_graph(self, user_id, graph_id):
        async with self.sessions.begin() as session:
            if await current_role(session, user_id) != "admin":
                raise HTTPException(403, "admin_required")
            initial = await session.get(GraphRevision, graph_id)
            if initial is None:
                raise HTTPException(404, "graph_not_found")
            await session.scalar(
                select(KnowledgeRevision)
                .where(KnowledgeRevision.id == initial.knowledge_revision)
                .with_for_update()
            )
            graph = await session.scalar(
                select(GraphRevision)
                .where(GraphRevision.id == graph_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if graph is None or graph.status != "published":
                raise HTTPException(409, "graph_not_published")
            await session.scalar(
                select(KnowledgeRevision)
                .where(KnowledgeRevision.id == graph.knowledge_revision)
                .with_for_update()
            )
            for edge in await session.scalars(
                select(GraphRelation).where(GraphRelation.graph_id == graph.id)
            ):
                if await self.source(session, user_id, edge.source) is None:
                    raise HTTPException(409, "graph_source_access_changed")
            graph.activated_at = datetime.now(timezone.utc)
            await session.execute(
                insert(GraphActivation)
                .values(knowledge_revision=graph.knowledge_revision, graph_id=graph.id)
                .on_conflict_do_update(
                    index_elements=[GraphActivation.knowledge_revision],
                    set_={"graph_id": graph.id},
                )
            )
            return graph.id

    async def active_version(self, user_id, revision):
        async with self.sessions() as session:
            await current_role(session, user_id)
            return await session.scalar(
                select(GraphActivation.graph_id).where(
                    GraphActivation.knowledge_revision == revision
                )
            )

    async def expand(
        self,
        user_id,
        revision,
        entities,
        *,
        graph_version=None,
        hops=2,
        access_level=None,
    ):
        if type(hops) is not int or not 1 <= hops <= 2 or len(entities) > 20:
            raise HTTPException(422, "invalid_graph_expansion")
        async with self.sessions() as session:
            await current_role(session, user_id)
            query = select(GraphRevision).where(
                GraphRevision.knowledge_revision == revision,
                GraphRevision.status == "published",
            )
            if graph_version is None:
                graph_version = await session.scalar(
                    select(GraphActivation.graph_id).where(
                        GraphActivation.knowledge_revision == revision
                    )
                )
            query = query.where(GraphRevision.id == graph_version)
            graph = await session.scalar(
                query.order_by(
                    GraphRevision.activated_at.desc().nullslast(),
                    GraphRevision.created_at.desc(),
                    GraphRevision.id.desc(),
                ).limit(1)
            )
            result = {
                "graph_version": graph.id if graph else None,
                "knowledge_revision": revision,
                "relations": [],
            }
            if not graph:
                return result
            frontier = set(entities)
            visited = set()
            for _ in range(hops):
                rows = list(
                    await session.scalars(
                        select(GraphRelation)
                        .where(
                            GraphRelation.graph_id == graph.id,
                            or_(
                                GraphRelation.left_entity.in_(frontier),
                                GraphRelation.right_entity.in_(frontier),
                            ),
                        )
                        .order_by(GraphRelation.id)
                        .limit(100)
                    )
                )
                following = set()
                for edge in rows:
                    if (
                        edge.id in visited
                        or await self.source(
                            session, user_id, edge.source, access_level
                        )
                        is None
                    ):
                        continue
                    visited.add(edge.id)
                    result["relations"].append(
                        Relation(
                            left=edge.left_entity,
                            right=edge.right_entity,
                            predicate=edge.predicate,
                            revision=revision,
                            source=edge.source,
                            status="published",
                        ).model_dump()
                    )
                    following.update((edge.left_entity, edge.right_entity))
                    if len(result["relations"]) >= 30:
                        return result
                frontier = following - frontier
                if not frontier:
                    break
            return result

    async def read_citation(self, user_id, citation, access_level=None):
        async with self.sessions() as session:
            chunk = await self.source(
                session,
                user_id,
                {
                    "document_id": citation.document_id,
                    "index_revision": citation.index_revision,
                    "chunk_index": citation.chunk_index,
                },
                access_level,
            )
            if chunk is None:
                raise HTTPException(403, "source_access_changed")
            if len(chunk.content) > 16000:
                raise HTTPException(422, "source_fragment_too_large")
            return replace(citation, excerpt=chunk.content)


class CurrentAccessRetriever:
    """Check current identity and every source again after external retrieval."""

    def __init__(self, retriever, store, sessions, user_id, access_ceiling):
        self.retriever, self.store, self.sessions, self.user_id, self.access_ceiling = (
            retriever,
            store,
            sessions,
            user_id,
            access_ceiling,
        )

    @property
    def revision(self):
        return getattr(self.retriever, "revision", None)

    async def retrieve(self, query, *, user_access_level, limit=5):
        ranks = {"employee": 0, "support": 1, "admin": 2}
        if user_access_level not in ranks or self.access_ceiling not in ranks:
            raise HTTPException(403, "invalid_access_ceiling")
        async with self.sessions() as session:
            role = await current_role(session, self.user_id)
            access = min(
                (role, user_access_level, self.access_ceiling),
                key=lambda item: ranks[item],
            )
        hits = await self.retriever.retrieve(
            query, user_access_level=access, limit=limit
        )
        permitted = []
        async with self.sessions() as session:
            await current_role(session, self.user_id)
            for hit in hits:
                ref = {
                    "document_id": hit.citation.document_id,
                    "index_revision": hit.citation.index_revision,
                    "chunk_index": hit.citation.chunk_index,
                }
                if (
                    await self.store.source(session, self.user_id, ref, access)
                    is not None
                ):
                    permitted.append(hit)
        return permitted


class RelationRetriever:
    def __init__(self, retriever, store, user_id, revision, graph_version):
        self.retriever, self.store, self.user_id, self.revision = (
            retriever,
            store,
            user_id,
            revision,
        )
        self.graph_version = graph_version
        self.latest_relations = {}

    async def retrieve(self, query, *, user_access_level, limit=5):
        import re

        hits = await self.retriever.retrieve(
            query, user_access_level=user_access_level, limit=limit
        )
        entities = re.findall(r"[\w.-]+", query)[:20]
        self.latest_relations = await self.store.expand(
            self.user_id,
            self.revision,
            entities,
            graph_version=self.graph_version,
            access_level=user_access_level,
        )
        known = {(h.citation.document_id, h.citation.chunk_index) for h in hits}
        async with self.store.sessions() as session:
            for edge in self.latest_relations["relations"]:
                ref = edge["source"]
                key = (ref["document_id"], ref["chunk_index"])
                if key in known:
                    continue
                chunk = await self.store.source(
                    session, self.user_id, ref, user_access_level
                )
                if chunk is None:
                    continue
                hits.append(_to_hit(chunk, query, 0.5, 0, 0, 0.5))
                known.add(key)
                if len(hits) >= min(10, limit + 5):
                    break
        self.latest_relations["relations"] = [
            e
            for e in self.latest_relations["relations"]
            if (e["source"]["document_id"], e["source"]["chunk_index"]) in known
        ]
        return hits
