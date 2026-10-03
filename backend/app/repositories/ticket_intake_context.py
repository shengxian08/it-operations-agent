"""Latest owned terminal collection, independent of model history text."""
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.ticket_intake import owned_intake
from app.db.models import AgentRun, Conversation


async def load_ticket_intake_context(session: AsyncSession, user_id: str, conversation_id: str,
                                    exclude_run_id: str, *, production: bool = True,
                                    before: datetime | None = None) -> dict[str, Any] | None:
    owner = await session.scalar(select(Conversation.user_id).where(Conversation.id == conversation_id))
    if owner != user_id:
        raise PermissionError("conversation is not available")
    if production:
        from app.production.run_models import ProductionRun
        query = select(ProductionRun.status, ProductionRun.result).where(
            ProductionRun.user_id == user_id, ProductionRun.conversation_id == conversation_id,
            ProductionRun.id != exclude_run_id, ProductionRun.status.in_(("completed", "failed", "cancelled")))
        if before is not None:
            query = query.where(ProductionRun.created_at <= before)
        query = query.order_by(ProductionRun.created_at.desc(), ProductionRun.id.desc()).limit(1)
        row = (await session.execute(query)).first()
        if row is None or row[0] != "completed" or not isinstance(row[1], dict) or row[1].get("final_state") != "ticket_collection":
            return None
        raw = row[1].get("ticket_intake")
    else:
        query = select(AgentRun.status, AgentRun.final_state, AgentRun.node_history).where(
            AgentRun.conversation_id == conversation_id, AgentRun.id != exclude_run_id,
            AgentRun.status.in_(("completed", "handoff")))
        if before is not None:
            query = query.where(AgentRun.created_at <= before)
        query = query.order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).limit(1)
        row = (await session.execute(query)).first()
        if row is None or row[0] != "completed" or row[1] != "ticket_collection":
            return None
        raw = next((item["ticket_intake"] for item in reversed(row[2] or []) if isinstance(item, dict)
                    and item.get("node") == "collect_ticket_draft" and "ticket_intake" in item), None)
    return owned_intake(raw, user_id, conversation_id)
