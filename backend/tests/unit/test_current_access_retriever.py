from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_retrieval_rechecks_role_and_source_after_external_search(monkeypatch):
    from app.production import relations

    called = []
    role = "admin"

    @asynccontextmanager
    async def sessions():
        yield object()

    async def current_role(session, user):
        return role

    monkeypatch.setattr(relations, "current_role", current_role)

    class Store:
        async def source(self, session, user, reference, access):
            return None if role == "employee" else object()

    class Retriever:
        revision = SimpleNamespace(id="immutable-revision")

        async def retrieve(self, query, *, user_access_level, limit):
            nonlocal role
            called.append(user_access_level)
            role = "employee"
            return [
                SimpleNamespace(
                    citation=SimpleNamespace(
                        document_id="restricted",
                        index_revision="immutable-revision",
                        chunk_index=0,
                    )
                )
            ]

    wrapped = relations.CurrentAccessRetriever(
        Retriever(), Store(), sessions, "trusted-user", "support"
    )
    assert await wrapped.retrieve("question", user_access_level="admin", limit=5) == []
    assert called == ["support"]
    assert wrapped.revision.id == "immutable-revision"
