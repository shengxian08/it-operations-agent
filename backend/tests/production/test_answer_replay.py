"""Historical answers use current source permissions and exact server provenance."""
import hashlib
import json
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete, select, update

from app.agent.graph import GraphDependencies
from app.db.models import AgentRun, Message, User
from app.llm.providers import ChatResult
from app.production.identity import Principal, require_principal
from app.production.knowledge_models import KnowledgeRevision, KnowledgeSnapshot, KnowledgeSource, RevisionChunk
from app.production.runs import router
from app.production.worker import Worker
from app.rag.retriever import _to_hit
from app.repositories.agent_runs import AgentRunRepository
from app.services.chat import ChatService
from tests.production.test_runs import ctx  # noqa: F401
from tests.production.test_ticket_lookup_persistence import services, turn
from tests.production.test_ticket_progress import comment, setup


def provenance(citations):
    return {"schema_version": 1, "sources": [
        {key: item[key] for key in ("document_id", "index_revision", "chunk_index")} for item in citations]}


async def knowledge(c, count=1, access="employee"):
    revision = uuid4().hex
    hits = []
    async with c.factory.begin() as session:
        session.add(KnowledgeRevision(id=revision, collection_name=revision, status="ready", document_count=count,
            chunk_count=count, embedding_model="test", embedding_revision="test", dimensions=2,
            pipeline_config={}, created_by=c.principal.user_id))
        for index in range(count):
            identifier = uuid4().hex
            content = f"SYNTHETIC_KNOWLEDGE_CONTEXT_{index}_{identifier}"
            digest = hashlib.sha256(content.encode()).hexdigest()
            session.add(KnowledgeSource(id=identifier, title=f"Synthetic source {index}", source_path=f"{identifier}.md",
                content_hash=digest, content=content, access_level=access, status="active"))
            await session.flush()
            session.add(KnowledgeSnapshot(revision_id=revision, document_id=identifier, title=f"Synthetic original {index}",
                source_path=f"{identifier}.md", content=content, content_hash=digest, access_level=access))
            chunk = RevisionChunk(revision_id=revision, document_id=identifier, chunk_index=0, content=content,
                source_title=f"Synthetic original {index}", source_path=f"{identifier}.md", char_start=0,
                char_end=len(content), access_level=access)
            session.add(chunk)
            await session.flush()
            hits.append(_to_hit(chunk, "", .9, .9, .9, .9))
    return hits, [asdict(hit.citation) for hit in hits]


async def completed(c, citations, *, with_provenance=True):
    content = "VPN错误如何处理"
    client_id, key = uuid4().hex, uuid4().hex
    queued = await c.service.enqueue(c.principal, c.conversation, content, client_id, key)
    job = await c.service.claim("synthetic-replay-worker")
    metadata = {"knowledge_context": provenance(citations)} if with_provenance else {}
    assert await c.service.append_event(job["id"], job["lease_token"], "citations", {"citations": citations, **metadata})
    result = {"final_state": "answered", "answer": citations[0]["excerpt"], "citations": citations, **metadata}
    assert await c.service.finalize(job["id"], job["lease_token"], "completed", result,
        usage={"input_tokens": 1, "output_tokens": 1})
    return queued["id"], content, client_id, key


async def views(c, run_id, content, client_id, key):
    return {"run": await c.service.get_run(c.principal, run_id),
            "events": await c.service.events(c.principal, run_id),
            "messages": await c.service.messages(c.principal, c.conversation),
            "history": await c.service.history(run_id),
            "runs": await c.service.conversation_runs(c.principal, c.conversation),
            "retry": await c.service.enqueue(c.principal, c.conversation, content, client_id, key),
            "cancel": await c.service.cancel(c.principal, run_id)}


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["inactive", "restricted", "snapshot_restricted", "chunk_missing", "revision_missing", "role_demoted"])
async def test_knowledge_replay_reauthorizes_all_sources_and_keeps_original_evidence(ctx, change):
    if change == "role_demoted":
        async with ctx.factory.begin() as session:
            await session.execute(update(User).where(User.id == ctx.principal.user_id).values(access_level="support"))
        ctx.principal = Principal(ctx.principal.user_id, "Synthetic", "support")
    _, citations = await knowledge(ctx, access="support" if change == "role_demoted" else "employee")
    run_id, content, client_id, key = await completed(ctx, citations)
    marker = citations[0]["excerpt"]
    before = await ctx.service.get_run(ctx.principal, run_id)
    assert marker in str(before) and marker in str(await ctx.service.messages(ctx.principal, ctx.conversation))
    async with ctx.factory.begin() as session:
        document = citations[0]["document_id"]
        if change == "snapshot_restricted":
            await session.execute(update(KnowledgeSnapshot).where(KnowledgeSnapshot.document_id == document).values(access_level="admin"))
        elif change == "chunk_missing":
            await session.execute(delete(RevisionChunk).where(RevisionChunk.document_id == document))
        elif change == "revision_missing":
            await session.execute(delete(KnowledgeSnapshot).where(KnowledgeSnapshot.revision_id == citations[0]["index_revision"]))
            await session.execute(delete(RevisionChunk).where(RevisionChunk.revision_id == citations[0]["index_revision"]))
            await session.execute(delete(KnowledgeRevision).where(KnowledgeRevision.id == citations[0]["index_revision"]))
        elif change == "role_demoted":
            await session.execute(update(User).where(User.id == ctx.principal.user_id).values(access_level="employee"))
        else:
            await session.execute(update(KnowledgeSource).where(KnowledgeSource.id == document).values(
                **({"status": "inactive"} if change == "inactive" else {"access_level": "admin"})))
    replay = await views(ctx, run_id, content, client_id, key)
    assert all(marker not in str(value) for value in replay.values())
    assert replay["run"]["result"]["access_redacted"] is True
    assert next(item for item in replay["events"] if item["type"] == "citations")["data"]["citations"] == []
    async with ctx.factory() as session:
        original = await session.get(ctx.models.ProductionRun, run_id)
        assert original.result["answer"] == marker
        assert (await session.get(Message, original.result_message_id)).content == marker


@pytest.mark.asyncio
async def test_actual_http_sse_retry_and_cancel_do_not_reexpose_revoked_knowledge(ctx):
    _, citations = await knowledge(ctx)
    run_id, content, client_id, key = await completed(ctx, citations)
    principal = Principal(ctx.principal.user_id, "Synthetic", "employee", "test-session", "test-csrf")
    app = FastAPI()
    app.state.run_service = ctx.service
    async def authenticated():
        return principal
    async def session_principal(_session):
        return principal
    app.dependency_overrides[require_principal] = authenticated
    app.state.identity_service = SimpleNamespace(principal=session_principal)
    app.include_router(router)
    async with ctx.factory.begin() as session:
        await session.execute(update(KnowledgeSource).where(KnowledgeSource.id == citations[0]["document_id"]).values(status="inactive"))
    forbidden = [citations[0][key] for key in ("excerpt", "source_title", "source_path")]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
        for path in (f"/runs/{run_id}", f"/runs/{run_id}/events", f"/conversations/{ctx.conversation}/runs",
                     f"/conversations/{ctx.conversation}/messages"):
            response = await api.get("/api/v1" + path)
            assert response.status_code == 200 and all(value not in response.text for value in forbidden)
        retry = await api.post(f"/api/v1/conversations/{ctx.conversation}/runs",
            json={"content": content, "client_message_id": client_id}, headers={"Idempotency-Key": key})
        cancel = await api.post(f"/api/v1/runs/{run_id}/cancel")
        assert retry.status_code == 202 and retry.json()["id"] == run_id and cancel.status_code == 200
        assert all(value not in retry.text + cancel.text for value in forbidden)


@pytest.mark.asyncio
async def test_actual_graph_worker_tracks_uncited_prompt_source_and_never_reuses_answer_as_model_history(ctx):
    hits, citations = await knowledge(ctx, 2)
    tickets, _ = services(ctx)
    provider = AsyncMock(complete=AsyncMock(return_value=ChatResult(json.dumps({
        "answer": citations[1]["excerpt"] + " [1]", "citation_ids": [1]}), 10, 5, "controlled")))
    chat = ChatService(ctx.factory, GraphDependencies(retriever=AsyncMock(retrieve=AsyncMock(return_value=hits)),
        chat_provider=provider, ticket_service=tickets, require_structured_citations=True), tickets)
    queued = await ctx.service.enqueue(ctx.principal, ctx.conversation, "VPN如何处理", uuid4().hex, uuid4().hex)
    job = await ctx.service.claim("provenance-worker")
    await Worker(ctx.service.settings, ctx.service, tickets, None, chat_factory=lambda *_args, **_kwargs: chat).execute(job)
    result = await ctx.service.get_run(ctx.principal, queued["id"])
    assert result["result"]["final_state"] == "answered" and len(result["result"]["citations"]) == 1
    assert result["result"]["knowledge_context"] == provenance(citations)
    history = await ctx.service.history(queued["id"])
    assert all(item["excerpt"] not in str(history) for item in citations)
    assert citations[1]["excerpt"] in provider.complete.call_args.args[0]
    async with ctx.factory.begin() as session:
        await session.execute(update(KnowledgeSource).where(KnowledgeSource.id == citations[1]["document_id"]).values(status="inactive"))
    hidden = await ctx.service.get_run(ctx.principal, queued["id"])
    assert hidden["result"]["access_redacted"] is True and citations[1]["excerpt"] not in str(hidden)
    chat._graph_dependencies.retriever.retrieve.return_value = [hits[0]]
    provider.complete.return_value = ChatResult(json.dumps({"answer": "重新检索当前允许来源 [1]", "citation_ids": [1]}), 10, 5, "controlled")
    following = await ctx.service.enqueue(ctx.principal, ctx.conversation, "下一步如何处理", uuid4().hex, uuid4().hex)
    next_job = await ctx.service.claim("provenance-next-worker")
    await Worker(ctx.service.settings, ctx.service, tickets, None, chat_factory=lambda *_args, **_kwargs: chat).execute(next_job)
    assert citations[1]["excerpt"] not in provider.complete.call_args.args[0]
    assert (await ctx.service.get_run(ctx.principal, following["id"]))["result"]["answer"] == "重新检索当前允许来源 [1]"


@pytest.mark.asyncio
async def test_knowledge_with_unknown_prompt_provenance_is_hidden_even_if_displayed_citation_is_allowed(ctx):
    _, citations = await knowledge(ctx)
    run_id, content, client_id, key = await completed(ctx, citations, with_provenance=False)
    assert all(citations[0]["excerpt"] not in str(item) for item in (await views(ctx, run_id, content, client_id, key)).values())


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["production_link_missing", "legacy_linked", "legacy_link_missing"])
async def test_legacy_and_orphan_ticket_messages_fail_closed_without_rewriting_originals(ctx, origin):
    marker = "SYNTHETIC_UNVERIFIABLE_INTERNAL_PROGRESS"
    if origin == "production_link_missing":
        _, number, tid, staff = await setup(ctx)
        await comment(ctx, tid, marker, "internal")
        ctx.principal = staff
        ctx.conversation = (await ctx.service.create_conversation(staff, "Synthetic legacy"))["id"]
        result = await turn(ctx, number)
        async with ctx.factory.begin() as session:
            run = await session.get(ctx.models.ProductionRun, result["id"])
            message_id = run.result_message_id
            original_content = run.result["answer"]
            run.result_message_id = None
            await session.execute(update(User).where(User.id == staff.user_id).values(access_level="employee"))
        run_id = result["id"]
    else:
        original_content = marker
        repository = AgentRunRepository(ctx.factory)
        started = await repository.start(conversation_id=ctx.conversation, content="查询工单", trace_id="synthetic-legacy")
        message_id = await repository.finish(run_id=started.run_id, conversation_id=ctx.conversation, answer=marker,
            citations=[], intent="ticket_lookup", final_state="ticket_status", handoff_reason=None, error_type=None,
            node_history=[], latency_ms=1, model_name=None, input_tokens=0, output_tokens=0)
        if origin == "legacy_link_missing":
            async with ctx.factory.begin() as session:
                await session.execute(update(AgentRun).where(AgentRun.id == started.run_id).values(result_message_id=None))
        run_id = (await ctx.service.enqueue(ctx.principal, ctx.conversation, "新问题", uuid4().hex, uuid4().hex))["id"]
    messages = await ctx.service.messages(ctx.principal, ctx.conversation)
    assert marker not in str(messages) and marker not in str(await ctx.service.history(run_id))
    assert any(item["role"] == "user" and item["content"] for item in messages["messages"])
    app = FastAPI()
    app.state.run_service = ctx.service
    async def authenticated():
        return ctx.principal
    app.dependency_overrides[require_principal] = authenticated
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
        response = await api.get(f"/api/v1/conversations/{ctx.conversation}/messages")
        assert response.status_code == 200 and marker not in response.text
    async with ctx.factory() as session:
        assert (await session.get(Message, message_id)).content == original_content
