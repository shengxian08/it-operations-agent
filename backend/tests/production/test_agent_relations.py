import importlib
import os
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


def module():
    try:
        return importlib.import_module("app.production.relations")
    except ModuleNotFoundError:
        pytest.fail("PostgreSQL relationship publication and ACL checks are missing")


@pytest.mark.asyncio
async def test_pg_graph_requires_review_and_rechecks_current_source_permissions():
    m = module()
    from app.db.models import User
    from app.production.identity_models import IdentityAccount
    from app.production.knowledge_models import (
        KnowledgeSource,
        KnowledgeRevision,
        KnowledgeSnapshot,
        RevisionChunk,
    )
    from app.agent.relations import Relation

    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user, doc, revision = (uuid4().hex for _ in range(3))
    async with sessions.begin() as session:
        session.add(
            User(id=user, display_name="Synthetic graph reviewer", access_level="admin")
        )
        await session.flush()
        session.add(
            IdentityAccount(user_id=user, issuer="test", subject=user, enabled=True)
        )
        session.add(
            KnowledgeSource(
                id=doc,
                title="Synthetic dependencies",
                source_path="public/dependencies.md",
                content_hash="hash",
                content="api depends_on db",
                access_level="employee",
                status="active",
            )
        )
        session.add(
            KnowledgeRevision(
                id=revision,
                collection_name=revision,
                document_count=1,
                chunk_count=1,
                embedding_model="scripted",
                embedding_revision="fixture",
                dimensions=16,
                pipeline_config={},
                created_by=user,
            )
        )
        await session.flush()
        session.add(
            KnowledgeSnapshot(
                revision_id=revision,
                document_id=doc,
                title="Dependencies",
                source_path="public/dependencies.md",
                content="api depends_on db",
                content_hash="hash",
                access_level="employee",
            )
        )
        session.add(
            RevisionChunk(
                revision_id=revision,
                document_id=doc,
                chunk_index=0,
                content="api depends_on db\napi depends_on db",
                source_title="Dependencies",
                source_path="public/dependencies.md",
                char_start=0,
                char_end=17,
                access_level="employee",
            )
        )
    store = m.RelationStore(sessions)
    async with sessions.begin() as session:
        automatic = await m.stage_revision_graph(session, user, revision)
    from app.production.agent_models import GraphRevision, GraphRelation
    from sqlalchemy import select

    async with sessions() as session:
        candidate = await session.get(GraphRevision, automatic)
        assert candidate.status == "candidate"
        assert (
            len(
                list(
                    await session.scalars(
                        select(GraphRelation).where(GraphRelation.graph_id == automatic)
                    )
                )
            )
            == 1
        )
    edge = Relation(
        left="api",
        right="db",
        predicate="depends_on",
        revision=revision,
        source={
            "document_id": doc,
            "chunk_index": 0,
            "index_revision": revision,
            "quote": "api depends_on db",
        },
    )
    graph = await store.create_candidates(user, revision, [edge])
    assert (await store.expand(user, revision, ["api"]))["relations"] == []
    await store.publish(user, graph)
    result = await store.expand(user, revision, ["api"])
    assert result["relations"][0]["right"] == "db"
    assert result["graph_version"] == graph
    second = await store.create_candidates(user, revision, [edge])
    await store.publish(user, second)
    assert (await store.expand(user, revision, ["api"]))["graph_version"] == second
    await store.activate_graph(user, graph)
    assert (await store.expand(user, revision, ["api"]))["graph_version"] == graph
    from app.production.agent_models import GraphActivation

    async with sessions() as session:
        assert (await session.get(GraphActivation, revision)).graph_id == graph
    async with sessions.begin() as session:
        source = await session.get(KnowledgeSource, doc)
        source.access_level = "admin"
    assert (await store.expand(user, revision, ["api"], access_level="employee"))[
        "relations"
    ] == []
    async with sessions.begin() as session:
        source = await session.get(KnowledgeSource, doc)
        source.access_level = "employee"
    from app.production.answer_access import readable_result

    report = {
        "observations": [
            {
                "id": "ev1",
                "status": "ok",
                "tool": "search_knowledge",
                "origin": "knowledge",
                "data": {"hits": [{"citation": edge.source}]},
            }
        ]
    }
    payload = {
        "final_state": "investigated",
        "answer": "readonly report",
        "investigation_report": report,
    }
    async with sessions() as session:
        assert not (await readable_result(session, user, "admin", payload)).get(
            "access_redacted"
        )
    async with sessions.begin() as session:
        source = await session.get(KnowledgeSource, doc)
        source.status = "inactive"
    assert (await store.expand(user, revision, ["api"]))["relations"] == []
    async with sessions() as session:
        assert (await readable_result(session, user, "admin", payload))[
            "access_redacted"
        ] is True
    await engine.dispose()
