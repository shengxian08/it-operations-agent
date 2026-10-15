"""Read views of immutable answers must retain every model source's permission boundary."""

from app.production.handoff_context import citation_content
from app.production.ticket_progress import (
    HISTORY_PLACEHOLDER,
    readable_result as readable_ticket_result,
)

UNKNOWN_ANSWER = "这条历史回答缺少可核验来源或来源已不可访问，请根据当前问题重新查询。"
KNOWLEDGE_HISTORY = "此轮知识问答已完成；再次回答须从当前可访问的知识来源重新检索。"
SAFE_STATES = {
    "handoff",
    "ticket_collection",
    "ticket_collection_cancelled",
    "ticket_lookup_clarification",
    "ticket_lookup_cancelled",
    "awaiting_confirmation",
}


async def readable_observation(
    session, user_id, role, observation, allowed_services=None
):
    if not isinstance(observation, dict):
        return False
    if observation.get("status") != "ok":
        return True
    data = observation.get("data")
    if not isinstance(data, dict):
        return False
    if observation.get("tool") == "search_knowledge":
        for hit in data.get("hits", []):
            reference = hit.get("citation") if isinstance(hit, dict) else None
            if (
                reference_identity(reference) is None
                or await citation_content(session, reference, role) is None
            ):
                return False
        return True
    if observation.get("tool") == "lookup_ticket":
        from sqlalchemy import select
        from app.production.business_models import ProductionTicket

        number = data.get("ticket_number")
        if data.get("found") is False:
            return True
        ticket = await session.scalar(
            select(ProductionTicket).where(ProductionTicket.ticket_number == number)
        )
        return ticket is not None and (
            role in {"admin", "support"} or ticket.user_id == user_id
        )
    if observation.get("tool") in {
        "get_service_health",
        "query_error_logs",
        "query_metrics",
        "get_release_changes",
    }:
        return observation.get("resource") in (
            allowed_services if allowed_services is not None else ()
        )
    return False


def hidden_result(result):
    return {
        **{
            key: value
            for key, value in result.items()
            if key in {"run_id", "status", "trace_id", "message_id", "final_state"}
        },
        "answer": UNKNOWN_ANSWER,
        "access_redacted": True,
    }


def reference_identity(reference):
    if not isinstance(reference, dict):
        return None
    document, revision, index = (
        reference.get(key) for key in ("document_id", "index_revision", "chunk_index")
    )
    if (
        not isinstance(document, str)
        or not 1 <= len(document) <= 64
        or not isinstance(revision, str)
        or not 1 <= len(revision) <= 64
        or type(index) is not int
        or index < 0
    ):
        return None
    return document, revision, index


async def knowledge_sources(session, role, context):
    if (
        not isinstance(context, dict)
        or type(context.get("schema_version")) is not int
        or context["schema_version"] != 1
        or not isinstance(context.get("sources"), list)
        or not 1 <= len(context["sources"]) <= 10
    ):
        return None
    sources = {}
    for reference in context["sources"]:
        identity = reference_identity(reference)
        if (
            identity is None
            or identity in sources
            or set(reference) != {"document_id", "index_revision", "chunk_index"}
        ):
            return None
        resolved = await citation_content(session, reference, role)
        if resolved is None:
            return None
        sources[identity] = resolved
    return sources


async def readable_result(session, user_id, role, result, allowed_services=None):
    if not isinstance(result, dict):
        return result
    if result.get("final_state") == "ticket_status":
        return await readable_ticket_result(session, user_id, role, result)
    if result.get("final_state") == "investigated":
        report = result.get("investigation_report")
        if not isinstance(report, dict) or not isinstance(
            report.get("observations"), list
        ):
            return hidden_result(result)
        for observation in report["observations"]:
            if not await readable_observation(
                session, user_id, role, observation, allowed_services
            ):
                return hidden_result(result)
        return result
    if result.get("final_state") == "knowledge_clarification":
        context = result.get("knowledge_context")
        if (
            context
            and context.get("sources")
            and await knowledge_sources(session, role, context) is None
        ):
            return hidden_result(result)
        return result
    if result.get("final_state") != "answered":
        return (
            result
            if result.get("final_state") in SAFE_STATES
            else hidden_result(result)
        )
    sources = await knowledge_sources(session, role, result.get("knowledge_context"))
    citations = result.get("citations")
    if (
        sources is None
        or not isinstance(citations, list)
        or not citations
        or any(reference_identity(item) not in sources for item in citations)
    ):
        return hidden_result(result)
    # Resolve titles/excerpts from authorized original chunks; do not trust serialized prose as evidence.
    return result | {
        "citations": [sources[reference_identity(item)] for item in citations]
    }


async def readable_citations(session, user_id, role, data, result):
    identifiers = {
        key: value for key, value in data.items() if key in {"run_id", "trace_id"}
    }
    if data.get("citations") == []:
        return identifiers | {"citations": []}
    context = (
        data.get("knowledge_context")
        if "knowledge_context" in data
        else (result or {}).get("knowledge_context")
    )
    filtered = await readable_result(
        session,
        user_id,
        role,
        {
            "final_state": "answered",
            "answer": "",
            "citations": data.get("citations"),
            "knowledge_context": context,
        },
    )
    return identifiers | {
        "citations": filtered.get("citations", []),
        **({"access_redacted": True} if filtered.get("access_redacted") else {}),
    }


def history_content(message, prior):
    if message.role == "user":
        return message.content
    if not isinstance(prior, dict):
        return UNKNOWN_ANSWER
    state = prior.get("final_state")
    if state == "ticket_status":
        return HISTORY_PLACEHOLDER
    if state == "answered":
        return KNOWLEDGE_HISTORY
    return message.content if state in SAFE_STATES else UNKNOWN_ANSWER
