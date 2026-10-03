"""Bounded employee evidence for a handoff; never retain model prose or source excerpts."""
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.agent.ticket_intake import draft_facts_complete, owned_intake
from app.db.models import Conversation, Message, User
from app.production.business_models import TicketDraftRecord
from app.production.identity_models import IdentityAccount
from app.production.knowledge_models import KnowledgeSnapshot, KnowledgeSource, RevisionChunk
from app.production.run_models import ProductionRun
from app.rag.retriever import ACCESS_HIERARCHY, _to_hit


async def citation_content(session, reference: dict, role: str) -> dict[str, Any] | None:
    document, revision, index = (reference.get(key) for key in ("document_id", "index_revision", "chunk_index"))
    if (not isinstance(document, str) or not 1 <= len(document) <= 64
            or not isinstance(revision, str) or not 1 <= len(revision) <= 64
            or type(index) is not int or index < 0):
        return None
    allowed = ACCESS_HIERARCHY.get(role, ())
    chunk = await session.scalar(select(RevisionChunk).join(KnowledgeSource,
        KnowledgeSource.id == RevisionChunk.document_id).join(KnowledgeSnapshot,
        (KnowledgeSnapshot.document_id == RevisionChunk.document_id)
        & (KnowledgeSnapshot.revision_id == RevisionChunk.revision_id)).where(
            RevisionChunk.document_id == document, RevisionChunk.revision_id == revision, RevisionChunk.chunk_index == index,
            RevisionChunk.access_level.in_(allowed), KnowledgeSnapshot.access_level.in_(allowed),
            KnowledgeSource.access_level.in_(allowed), KnowledgeSource.status == "active"))
    if chunk is None:
        return None
    result = asdict(_to_hit(chunk, "", 0, 0, 0, 0).citation)
    result["excerpt"] = chunk.content if len(chunk.content) <= 4000 else None
    result["excerpt_omitted"] = len(chunk.content) > 4000
    result["source_message_id"] = reference.get("source_message_id")
    return result


async def build_handoff_context(session, run: ProductionRun) -> dict[str, Any]:
    if await session.scalar(select(Conversation.user_id).where(Conversation.id == run.conversation_id)) != run.user_id:
        raise PermissionError("conversation is not available")
    prior = await session.scalar(select(ProductionRun).where(ProductionRun.user_id == run.user_id,
        ProductionRun.conversation_id == run.conversation_id, ProductionRun.id != run.id,
        ProductionRun.created_at <= run.created_at, ProductionRun.status.in_(("completed", "failed", "cancelled")))
        .order_by(ProductionRun.created_at.desc(), ProductionRun.id.desc()).limit(1))
    result = {"schema_version": 1, "captured_at": datetime.now(timezone.utc).isoformat(),
        "problem": None, "impact": None, "attempted_steps": None, "source": None,
        "messages": [], "messages_omitted": False, "citations": [], "citations_omitted": False}
    if prior and prior.status == "completed" and isinstance(prior.result, dict):
        intake = owned_intake(prior.result.get("ticket_intake"), run.user_id, run.conversation_id)
        if prior.result.get("final_state") == "ticket_collection" and intake:
            result.update({key: intake[key] for key in ("problem", "impact", "attempted_steps")})
            result["source"] = {"run_id": prior.id, "draft_id": None, "draft_version": None}
        elif prior.result.get("final_state") == "awaiting_confirmation":
            draft_event = prior.result.get("ticket_draft")
            if isinstance(draft_event, dict) and isinstance(draft_event.get("draft_id"), str):
                draft = await session.scalar(select(TicketDraftRecord).where(TicketDraftRecord.id == draft_event["draft_id"],
                    TicketDraftRecord.user_id == run.user_id, TicketDraftRecord.conversation_id == run.conversation_id,
                    TicketDraftRecord.run_id == prior.id))
                if draft and draft_facts_complete(draft.draft):
                    result.update({key: draft.draft[key] for key in ("problem", "impact", "attempted_steps")})
                    result["source"] = {"run_id": prior.id, "draft_id": draft.id, "draft_version": draft.version}
    messages = list(await session.scalars(select(Message).where(Message.conversation_id == run.conversation_id,
        Message.role == "user", Message.created_at <= run.created_at)
        .order_by(Message.created_at.desc(), Message.id.desc()).limit(6)))
    result["messages_omitted"] = len(messages) > 5
    result["messages"] = [{"id": message.id, "content": message.content if len(message.content) <= 2000 else None,
                            "omission": None if len(message.content) <= 2000 else "too_long"}
                           for message in reversed(messages[:5])]
    owner = await session.scalar(select(User).join(IdentityAccount, IdentityAccount.user_id == User.id).where(
        User.id == run.user_id, IdentityAccount.enabled.is_(True)))
    if owner is not None:
        sources = list(await session.scalars(select(Message).where(Message.conversation_id == run.conversation_id,
            Message.role == "assistant", Message.created_at <= run.created_at)
            .order_by(Message.created_at.desc(), Message.id.desc()).limit(6)))
        result["citations_omitted"] = len(sources) > 5
        seen = set()
        for message in sources[:5]:
            candidates = message.citations if isinstance(message.citations, list) else []
            result["citations_omitted"] = result["citations_omitted"] or len(candidates) > 6
            for citation in candidates[:6]:
                if len(result["citations"]) >= 5:
                    result["citations_omitted"] = True
                    break
                if not isinstance(citation, dict):
                    result["citations_omitted"] = True
                    continue
                reference = {key: citation.get(key) for key in ("document_id", "index_revision", "chunk_index")}
                reference["source_message_id"] = message.id
                content = await citation_content(session, reference, owner.access_level)
                if content is None:
                    result["citations_omitted"] = True
                    continue
                key = (reference["document_id"], reference["index_revision"], reference["chunk_index"])
                if key in seen:
                    continue
                seen.add(key)
                if len(result["citations"]) < 5:
                    result["citations"].append(reference)
                else:
                    result["citations_omitted"] = True
    return result


async def readable_handoff_context(session, context: dict | None, role: str) -> dict[str, Any] | None:
    if context is None:
        return None
    if not isinstance(context, dict) or type(context.get("schema_version")) is not int or context["schema_version"] != 1:
        raise ValueError("handoff context is unavailable")
    # Only our schema is exposed; stored reference text is never returned.
    result = {key: context[key] for key in ("schema_version", "captured_at", "problem", "impact", "attempted_steps",
        "source", "messages", "messages_omitted", "citations_omitted")}
    result["citations"], result["citations_unavailable"] = [], 0
    for reference in context.get("citations", []):
        citation = await citation_content(session, reference, role)
        if citation is None:
            result["citations_unavailable"] += 1
        else:
            result["citations"].append(citation)
    return result
