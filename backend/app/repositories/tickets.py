from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Conversation, Ticket, TicketEvent, ToolAudit
from app.schemas import TicketDraft


@dataclass(frozen=True, slots=True)
class StoredTicket:
    ticket_number: str
    status: str
    user_id: str
    request_summary: str


@dataclass(frozen=True, slots=True)
class TicketStatusRecord:
    ticket_number: str
    status: str
    latest_update: str


class TicketRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def get_conversation_user_id(self, conversation_id: str) -> str | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(Conversation.user_id).where(
                    Conversation.id == conversation_id
                )
            )

    async def get_status_for_user(
        self,
        user_id: str,
        ticket_number: str,
    ) -> TicketStatusRecord | None:
        async with self._session_factory() as session:
            ticket = await session.scalar(
                select(Ticket).where(
                    Ticket.user_id == user_id,
                    Ticket.ticket_number == ticket_number,
                )
            )
            if ticket is None:
                return None

            latest_event = await session.scalar(
                select(TicketEvent)
                .where(TicketEvent.ticket_id == ticket.id)
                .order_by(TicketEvent.created_at.desc(), TicketEvent.id.desc())
                .limit(1)
            )
            latest_update = (
                str(latest_event.details.get("summary"))
                if latest_event and latest_event.details.get("summary")
                else "工单状态已更新。"
            )
            return TicketStatusRecord(
                ticket_number=ticket.ticket_number,
                status=ticket.status,
                latest_update=latest_update,
            )

    async def get_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> StoredTicket | None:
        async with self._session_factory() as session:
            return await self._get_by_idempotency_key(session, idempotency_key)

    async def create_ticket(
        self,
        *,
        user_id: str,
        draft: TicketDraft,
        request_summary: str,
        confirmation_token_hash: str,
        idempotency_key: str,
        trace_id: str,
    ) -> StoredTicket:
        async with self._session_factory.begin() as session:
            await session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(:lock_name, 0))"
                ),
                {"lock_name": f"ticket-idempotency:{idempotency_key}"},
            )
            existing = await self._get_by_idempotency_key(
                session,
                idempotency_key,
            )
            if existing is not None:
                return existing

            year = datetime.now(UTC).year
            await session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(:lock_name, 0))"
                ),
                {"lock_name": f"ticket-number:{year}"},
            )
            ticket_number = await self._next_ticket_number(session, year)
            ticket = Ticket(
                ticket_number=ticket_number,
                user_id=user_id,
                title=draft.title,
                category=draft.category,
                priority=draft.priority,
                description=draft.description,
                attempted_steps=list(draft.attempted_steps),
                status="pending",
            )
            session.add(ticket)
            await session.flush()

            session.add_all(
                [
                    TicketEvent(
                        ticket_id=ticket.id,
                        event_type="created",
                        details={
                            "summary": "工单已创建，等待 IT 支持处理。",
                        },
                    ),
                    ToolAudit(
                        ticket_id=ticket.id,
                        tool_name="create_ticket",
                        request_summary=request_summary,
                        result_category="created",
                        confirmation_token_hash=confirmation_token_hash,
                        idempotency_key=idempotency_key,
                        trace_id=trace_id,
                    ),
                ]
            )

            return StoredTicket(
                ticket_number=ticket.ticket_number,
                status=ticket.status,
                user_id=ticket.user_id,
                request_summary=request_summary,
            )

    @staticmethod
    async def _get_by_idempotency_key(
        session: AsyncSession,
        idempotency_key: str,
    ) -> StoredTicket | None:
        audit = await session.scalar(
            select(ToolAudit).where(
                ToolAudit.idempotency_key == idempotency_key
            )
        )
        if audit is None:
            return None
        if audit.ticket_id is None:
            raise PermissionError("idempotency key does not match request")
        ticket = await session.get(Ticket, audit.ticket_id)
        if ticket is None:
            raise PermissionError("idempotency key does not match request")
        return StoredTicket(
            ticket_number=ticket.ticket_number,
            status=ticket.status,
            user_id=ticket.user_id,
            request_summary=audit.request_summary,
        )

    @staticmethod
    async def _next_ticket_number(
        session: AsyncSession,
        year: int,
    ) -> str:
        prefix = f"IT-{year}-"
        latest = await session.scalar(
            select(Ticket.ticket_number)
            .where(Ticket.ticket_number.like(f"{prefix}%"))
            .order_by(Ticket.ticket_number.desc())
            .limit(1)
        )
        sequence = int(latest.rsplit("-", 1)[1]) + 1 if latest else 1
        return f"{prefix}{sequence:04d}"
