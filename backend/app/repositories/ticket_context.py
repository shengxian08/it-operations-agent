"""Read controlled object records, never infer authorization from message text."""

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.ticket_lookup import MAX_CANDIDATES, empty_context, ticket_numbers, valid_number
from app.db.models import AgentRun, Conversation, Message, Ticket, ToolAudit


def _record(value: object, user_id: str, conversation_id: str, final_state: str | None) -> dict | None:
    if (not isinstance(value, dict) or type(value.get("schema_version")) is not int
            or value["schema_version"] != 1 or value.get("user_id") != user_id
            or value.get("conversation_id") != conversation_id):
        return None
    outcome = value.get("outcome")
    expected = {"found": "ticket_status", "not_found": "ticket_status", "unavailable": "handoff",
                "clarification": "ticket_lookup_clarification", "cancelled": "ticket_lookup_cancelled"}
    if not isinstance(outcome, str) or expected.get(outcome) != final_state:
        return None
    candidates = value.get("candidates")
    if (not isinstance(candidates, list) or len(candidates) > MAX_CANDIDATES
            or any(not valid_number(number) for number in candidates)
            or len(set(candidates)) != len(candidates)):
        return None
    if outcome in {"found", "not_found", "unavailable"}:
        if not valid_number(value.get("ticket_number")) or value.get("basis") not in (
            "explicit", "conversation", "clarification_selection",
        ):
            return None
    elif value.get("ticket_number") is not None or value.get("basis") is not None:
        return None
    if outcome == "clarification" and value.get("reason") not in (
        "missing_ticket_number", "ambiguous_ticket_number", "too_many_candidates",
    ):
        return None
    return value


async def load_ticket_context(session: AsyncSession, user_id: str, conversation_id: str,
                              exclude_run_id: str, *, production: bool = True,
                              before: datetime | None = None) -> dict[str, Any]:
    owner = await session.scalar(select(Conversation.user_id).where(Conversation.id == conversation_id))
    if owner != user_id:
        raise PermissionError("conversation is not available")
    context = empty_context(user_id, conversation_id)
    def add(numbers):
        for number in numbers:
            if number in context["candidates"]:
                continue
            if len(context["candidates"]) == MAX_CANDIDATES:
                context["overflow"] = True
                break
            context["candidates"].append(number)
    if production:
        from app.production.run_models import ProductionRun
        query = select(ProductionRun.result, Message.content).join(Message, Message.id == ProductionRun.message_id).where(
            ProductionRun.user_id == user_id, ProductionRun.conversation_id == conversation_id,
            Message.conversation_id == conversation_id, Message.role == "user",
            ProductionRun.id != exclude_run_id, ProductionRun.status.in_(["completed", "failed", "cancelled"]))
        if before is not None:
            query = query.where(ProductionRun.created_at <= before)
        query = query.order_by(ProductionRun.created_at.desc(), ProductionRun.id.desc())
    else:
        query = select(AgentRun.final_state, AgentRun.node_history, Message.content).join(Message, Message.id == AgentRun.message_id).where(
            AgentRun.conversation_id == conversation_id, Message.conversation_id == conversation_id,
            Message.role == "user", AgentRun.id != exclude_run_id, AgentRun.status.in_(["completed", "handoff"]))
        if before is not None:
            query = query.where(AgentRun.created_at <= before)
        query = query.order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
    rows = await session.stream(query.execution_options(yield_per=100))
    latest = True
    try:
        async for row in rows:
            if production:
                result, content = row
                result = result if isinstance(result, dict) else {}
                final_state = result.get("final_state")
                raw, has_record = result.get("ticket_lookup"), "ticket_lookup" in result
            else:
                final_state, node_history, content = row
                raw = next((node["ticket_lookup"] for node in reversed(node_history or [])
                            if isinstance(node, dict) and node.get("node") == "lookup_ticket"
                            and "ticket_lookup" in node), None)
                has_record = raw is not None
            record = _record(raw, user_id, conversation_id, final_state)
            # Compatibility uses a server-executed lookup's original USER request.
            # A malformed new record never falls back to text and old rows stay intact.
            legacy_numbers = ticket_numbers(content) if not has_record and final_state == "ticket_status" else []
            if latest:
                context["recent_lookup"] = bool(record and record["outcome"] != "cancelled") or bool(legacy_numbers)
                if record and record["outcome"] == "clarification":
                    context["pending_candidates"] = list(record["candidates"])
                latest = False
            if record:
                add(record["candidates"])
                if record.get("ticket_number"):
                    add([record["ticket_number"]])
                if record.get("reason") == "too_many_candidates":
                    context["overflow"] = True
            else:
                add(legacy_numbers)
            if context["overflow"]:
                break
    finally:
        await rows.close()
    if not context["overflow"] and production:
        from app.production.business_models import TicketDraftRecord
        query = select(TicketDraftRecord.ticket_number).where(TicketDraftRecord.user_id == user_id,
            TicketDraftRecord.conversation_id == conversation_id, TicketDraftRecord.status == "confirmed",
            TicketDraftRecord.ticket_number.is_not(None)).order_by(TicketDraftRecord.created_at.desc(), TicketDraftRecord.id.desc())
        numbers = await session.stream_scalars(query.execution_options(yield_per=100))
        try:
            async for number in numbers:
                if valid_number(number):
                    add([number])
                if context["overflow"]:
                    break
        finally:
            await numbers.close()
    elif not context["overflow"]:
        query = select(Ticket.ticket_number, ToolAudit.request_summary).join(ToolAudit, ToolAudit.ticket_id == Ticket.id).where(
            Ticket.user_id == user_id, ToolAudit.tool_name == "create_ticket", ToolAudit.result_category == "created"
        ).order_by(ToolAudit.created_at.desc(), ToolAudit.id.desc())
        records = await session.stream(query.execution_options(yield_per=100))
        try:
            async for number, summary in records:
                try:
                    request = json.loads(summary)
                except (ValueError, TypeError):
                    continue
                if isinstance(request, dict) and request.get("conversation_id") == conversation_id and valid_number(number):
                    add([number])
                if context["overflow"]:
                    break
        finally:
            await records.close()
    return context
