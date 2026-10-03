"""Database-backed queue, admission control, fencing leases, and resumable SSE."""
import asyncio
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app.db.models import Conversation, Message, User
from app.production.identity import Principal, require_principal
from app.production.identity_models import IdentityAccount
from app.production.run_models import ModelBudget, ProductionRun, ProductionRunEvent
from app.repositories.ticket_context import load_ticket_context
from app.production.answer_access import UNKNOWN_ANSWER, history_content, readable_citations, readable_result
from app.production.ticket_progress import current_role


TERMINAL = {"completed", "failed", "cancelled"}
ACTIVE = {"queued", "running"}
RATE_LIMIT_SCRIPT = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return n
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.00000001"), rounding=ROUND_CEILING)


def _cursor_encode(created_at: datetime, item_id: str) -> str:
    return base64.urlsafe_b64encode(json.dumps([created_at.isoformat(), item_id]).encode()).decode().rstrip("=")


def _cursor_decode(value: str) -> tuple[datetime, str]:
    try:
        if len(value) > 1000:
            raise ValueError("cursor length")
        stamp, item_id = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        parsed = datetime.fromisoformat(stamp)
        if parsed.tzinfo is None or not isinstance(item_id, str):
            raise ValueError("cursor shape")
        return parsed, item_id
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise HTTPException(400, "invalid_cursor") from error


class RunService:
    def __init__(self, settings, session_factory, redis) -> None:
        self.settings, self.session_factory, self.redis = settings, session_factory, redis

    @staticmethod
    def run_payload(run: ProductionRun) -> dict[str, Any]:
        return {"id": run.id, "conversation_id": run.conversation_id, "status": run.status,
                "trace_id": run.trace_id, "result": run.result, "created_at": _iso(run.created_at),
                "started_at": _iso(run.started_at), "finished_at": _iso(run.finished_at)}

    @staticmethod
    def conversation_payload(conversation: Conversation) -> dict[str, Any]:
        return {"id": conversation.id, "status": conversation.status, "title": conversation.summary or "新会话",
                "created_at": _iso(conversation.created_at), "updated_at": _iso(conversation.updated_at)}

    @staticmethod
    async def _owner(session, user_id: str, conversation_id: str, *, lock: bool = False) -> Conversation:
        query = select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user_id)
        if lock:
            query = query.with_for_update()
        conversation = await session.scalar(query)
        if conversation is None:
            raise HTTPException(404, "conversation_not_found")
        return conversation

    async def create_conversation(self, principal: Principal, title: str | None = None) -> dict[str, Any]:
        async with self.session_factory.begin() as session:
            conversation = Conversation(user_id=principal.user_id, status="active", summary=title or "新会话")
            session.add(conversation)
            await session.flush()
            return self.conversation_payload(conversation)

    async def conversations(self, principal: Principal, cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
        query = select(Conversation).where(Conversation.user_id == principal.user_id)
        if cursor:
            stamp, item_id = _cursor_decode(cursor)
            query = query.where(or_(Conversation.created_at < stamp, (Conversation.created_at == stamp) & (Conversation.id < item_id)))
        async with self.session_factory() as session:
            rows = list(await session.scalars(query.order_by(Conversation.created_at.desc(), Conversation.id.desc()).limit(limit + 1)))
        next_cursor = _cursor_encode(rows[limit - 1].created_at, rows[limit - 1].id) if len(rows) > limit else None
        return {"conversations": [self.conversation_payload(item) for item in rows[:limit]], "next_cursor": next_cursor}

    async def archive(self, principal: Principal, conversation_id: str) -> None:
        async with self.session_factory.begin() as session:
            conversation = await self._owner(session, principal.user_id, conversation_id, lock=True)
            active = await session.scalar(select(ProductionRun.id).where(ProductionRun.conversation_id == conversation_id, ProductionRun.status.in_(ACTIVE)))
            if active:
                raise HTTPException(409, "conversation_run_active")
            conversation.status, conversation.updated_at = "archived", _now()

    async def messages(self, principal: Principal, conversation_id: str, cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
        async with self.session_factory() as session:
            await self._owner(session, principal.user_id, conversation_id)
            role = await current_role(session, principal.user_id)
            query = select(Message, ProductionRun.result).outerjoin(ProductionRun, ProductionRun.result_message_id == Message.id).where(Message.conversation_id == conversation_id)
            if cursor:
                stamp, item_id = _cursor_decode(cursor)
                query = query.where(or_(Message.created_at < stamp, (Message.created_at == stamp) & (Message.id < item_id)))
            rows = list((await session.execute(query.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit + 1))).all())
            messages = []
            for row, result in reversed(rows[:limit]):
                if row.role == "assistant" and not isinstance(result, dict):
                    messages.append({"id": row.id, "role": row.role, "content": UNKNOWN_ANSWER,
                                     "citations": [], "created_at": _iso(row.created_at)})
                    continue
                filtered = await readable_result(session, principal.user_id, role, result)
                redacted = isinstance(filtered, dict) and filtered.get("access_redacted")
                messages.append({"id": row.id, "role": row.role,
                                 "content": filtered.get("answer", row.content) if isinstance(filtered, dict) else row.content,
                                 "citations": [] if redacted else filtered.get("citations", row.citations) if isinstance(filtered, dict) else row.citations,
                                 "created_at": _iso(row.created_at)})
        next_cursor = _cursor_encode(rows[limit - 1][0].created_at, rows[limit - 1][0].id) if len(rows) > limit else None
        return {"messages": messages, "next_cursor": next_cursor}

    async def record_feedback(self, principal: Principal, message_id: str, feedback: str) -> None:
        if feedback not in {"resolved", "unresolved"}:
            raise HTTPException(422, "invalid_feedback")
        async with self.session_factory.begin() as session:
            row = (await session.execute(select(Message, ProductionRun).join(ProductionRun, ProductionRun.result_message_id == Message.id)
                .where(Message.id == message_id, Message.role == "assistant", ProductionRun.user_id == principal.user_id)
                .with_for_update(of=Message))).first()
            if row is None:
                raise HTTPException(404, "message_not_found")
            message, run = row
            message.user_feedback = {"feedback": feedback, "recorded_at": _now().isoformat(), "run_id": run.id,
                "trace_id": run.trace_id, "final_state": (run.result or {}).get("final_state"),
                "citation_ids": sorted({str(citation["document_id"]) for citation in message.citations if citation.get("document_id")})}

    async def enqueue(self, principal: Principal, conversation_id: str, content: str, client_message_id: str, idempotency_key: str) -> dict[str, Any]:
        content = content.strip()
        if not content or len(content) > 10000 or len(content.encode()) > self.settings.budget_max_input_tokens // 2:
            raise HTTPException(422, "message_too_large_or_empty")
        if any(not value or len(value) > 128 for value in (idempotency_key, client_message_id)):
            raise HTTPException(422, "invalid_idempotency_key")
        digest = hashlib.sha256(content.encode()).hexdigest()
        try:
            async with self.session_factory.begin() as session:
                # Serializes admission for one conversation and concurrent idempotent replays.
                conversation = await self._owner(session, principal.user_id, conversation_id, lock=True)
                existing = await session.scalar(select(ProductionRun).where(ProductionRun.user_id == principal.user_id,
                    or_(ProductionRun.idempotency_key == idempotency_key,
                        (ProductionRun.conversation_id == conversation_id) & (ProductionRun.client_message_id == client_message_id))))
                if existing:
                    if existing.conversation_id != conversation_id or existing.content_hash != digest or existing.client_message_id != client_message_id:
                        raise HTTPException(409, "idempotency_payload_conflict")
                    return await self._read_payload(session, principal, existing)
                if conversation.status != "active":
                    raise HTTPException(409, "conversation_archived")
                active = await session.scalar(select(ProductionRun.id).where(ProductionRun.conversation_id == conversation_id, ProductionRun.status.in_(ACTIVE)))
                if active:
                    raise HTTPException(409, "conversation_run_active")
                await session.execute(text("SELECT pg_advisory_xact_lock(782634902)"))
                queued = await session.scalar(select(func.count()).select_from(ProductionRun).where(ProductionRun.status == "queued"))
                if queued >= self.settings.queue_limit:
                    raise HTTPException(429, "run_queue_full", headers={"Retry-After": "10"})
                bucket = int(_now().timestamp()) // 60
                rate_key = f"itops:run-rate:{principal.user_id}:{bucket}"
                count = int(await self.redis.eval(RATE_LIMIT_SCRIPT, 1, rate_key, 65))
                if count > self.settings.user_rate_limit:
                    raise HTTPException(429, "user_rate_limit", headers={"Retry-After": "60"})
                month = _now().strftime("%Y-%m")
                input_price, output_price = _money(self.settings.model_input_price), _money(self.settings.model_output_price)
                reserved = _money((Decimal(self.settings.budget_max_input_tokens) * input_price
                                   + Decimal(self.settings.model_max_output_tokens) * output_price)
                                  * Decimal(1 + self.settings.model_max_retries) / Decimal(1_000_000))
                await session.execute(insert(ModelBudget).values(month=month, reserved=0, spent=0).on_conflict_do_nothing(index_elements=["month"]))
                budget = await session.scalar(select(ModelBudget).where(ModelBudget.month == month).with_for_update())
                if budget.reserved + budget.spent + reserved > _money(self.settings.model_monthly_budget):
                    raise HTTPException(429, "monthly_model_budget_exhausted")
                budget.reserved += reserved
                # Empty means fixed empty snapshot; None is reserved for dynamic lookup by the retriever factory.
                from app.production.knowledge_models import KnowledgeActivePointer
                revision = await session.scalar(select(KnowledgeActivePointer.revision_id).where(KnowledgeActivePointer.id == 1))
                message = Message(conversation_id=conversation_id, role="user", content=content, citations=[])
                session.add(message)
                await session.flush()
                run = ProductionRun(user_id=principal.user_id, conversation_id=conversation_id, message_id=message.id,
                    client_message_id=client_message_id, idempotency_key=idempotency_key, content_hash=digest,
                    status="queued", trace_id=uuid4().hex, user_access_level=principal.role,
                    index_revision=revision or "", budget_month=month, reserved_cost=reserved,
                    input_price=input_price, output_price=output_price)
                session.add(run)
                conversation.updated_at = _now()
                if conversation.summary == "新会话":
                    conversation.summary = content[:60]
                await session.flush()
                return self.run_payload(run)
        except IntegrityError as error:
            # A key reused simultaneously in different conversations still cannot duplicate.
            raise HTTPException(409, "idempotency_or_active_run_conflict") from error

    async def _owned_run(self, session, principal: Principal, run_id: str, *, lock: bool = False) -> ProductionRun:
        query = select(ProductionRun).where(ProductionRun.id == run_id, ProductionRun.user_id == principal.user_id)
        if lock:
            query = query.with_for_update()
        run = await session.scalar(query)
        if run is None:
            raise HTTPException(404, "run_not_found")
        return run

    async def _read_payload(self, session, principal, run) -> dict[str, Any]:
        role = await current_role(session, principal.user_id)
        return self.run_payload(run) | {"result": await readable_result(session, principal.user_id, role, run.result)}

    @staticmethod
    async def _read_events(session, principal, events, result):
        role = await current_role(session, principal.user_id)
        filtered = []
        for event in events:
            data = event["data"]
            if event["type"] == "final":
                data = await readable_result(session, principal.user_id, role, data)
            elif event["type"] == "citations":
                data = await readable_citations(session, principal.user_id, role, data, result)
            filtered.append(event | {"data": data})
        return filtered

    async def get_run(self, principal: Principal, run_id: str) -> dict[str, Any]:
        async with self.session_factory() as session:
            run = await self._owned_run(session, principal, run_id)
            events = await self._read_events(session, principal, await self._events(session, run_id, 0, 10000), run.result)
            return await self._read_payload(session, principal, run) | {"events": events}

    async def conversation_runs(self, principal: Principal, conversation_id: str, cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
        async with self.session_factory() as session:
            await self._owner(session, principal.user_id, conversation_id)
            await current_role(session, principal.user_id)
            query = select(ProductionRun).where(ProductionRun.conversation_id == conversation_id)
            if cursor:
                stamp, item_id = _cursor_decode(cursor)
                query = query.where(or_(ProductionRun.created_at < stamp, (ProductionRun.created_at == stamp) & (ProductionRun.id < item_id)))
            rows = list(await session.scalars(query.order_by(ProductionRun.created_at.desc(), ProductionRun.id.desc()).limit(limit + 1)))
            runs = [await self._read_payload(session, principal, row) for row in rows[:limit]]
        next_cursor = _cursor_encode(rows[limit - 1].created_at, rows[limit - 1].id) if len(rows) > limit else None
        return {"runs": runs, "next_cursor": next_cursor}

    @staticmethod
    async def _events(session, run_id: str, after: int, limit: int) -> list[dict[str, Any]]:
        rows = await session.scalars(select(ProductionRunEvent).where(ProductionRunEvent.run_id == run_id, ProductionRunEvent.sequence > after)
                                     .order_by(ProductionRunEvent.sequence).limit(limit))
        return [{"sequence": event.sequence, "type": event.event_type, "data": event.data, "created_at": _iso(event.created_at)} for event in rows]

    async def events(self, principal: Principal, run_id: str, after: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            run = await self._owned_run(session, principal, run_id)
            return await self._read_events(session, principal, await self._events(session, run_id, after, limit), run.result)

    @staticmethod
    async def _append(session, run: ProductionRun, event_type: str, data: dict[str, Any]) -> None:
        data = dict(data) | {"run_id": run.id, "trace_id": run.trace_id}
        session.add(ProductionRunEvent(run_id=run.id, sequence=run.next_sequence, event_type=event_type, data=data))
        run.next_sequence += 1

    @staticmethod
    def _valid_lease(run: ProductionRun | None, token: str, now: datetime) -> bool:
        return bool(run is not None and run.status == "running" and run.lease_token == token
                    and run.lease_expires_at is not None and run.lease_expires_at > now
                    and run.deadline_at is not None and run.deadline_at > now)

    @staticmethod
    async def _access_valid(session, run: ProductionRun) -> bool:
        row = (await session.execute(select(User.access_level, IdentityAccount.enabled)
            .join(IdentityAccount, IdentityAccount.user_id == User.id).where(User.id == run.user_id))).first()
        ranks = {"employee": 0, "support": 1, "admin": 2}
        return bool(row is not None and row.enabled and row.access_level in ranks
                    and ranks[row.access_level] >= ranks.get(run.user_access_level, 100))

    async def _access_failure(self, session, run: ProductionRun) -> None:
        await self._finish(session, run, "failed", {"answer": "账号访问权限已变更，运行已停止。",
            "final_state": "handoff", "error": "account_access_changed"})

    async def claim(self, worker_id: str) -> dict[str, Any] | None:
        async with self.session_factory.begin() as session:
            # The lock establishes a global concurrency ceiling across worker processes.
            await session.execute(text("SELECT pg_advisory_xact_lock(782634901)"))
            running = await session.scalar(select(func.count()).select_from(ProductionRun).where(ProductionRun.status == "running"))
            if running >= self.settings.worker_concurrency:
                return None
            run = await session.scalar(select(ProductionRun).where(ProductionRun.status == "queued")
                                       .order_by(ProductionRun.created_at, ProductionRun.id).with_for_update(skip_locked=True).limit(1))
            if run is None:
                return None
            if not await self._access_valid(session, run):
                await self._access_failure(session, run)
                return None
            now = _now()
            run.status, run.worker_id, run.lease_token = "running", worker_id[:200], uuid4().hex
            run.started_at = now
            run.lease_expires_at = now + timedelta(seconds=self.settings.lease_seconds)
            run.deadline_at = now + timedelta(seconds=self.settings.run_timeout_seconds)
            content = await session.scalar(select(Message.content).where(Message.id == run.message_id))
            # Current DB policy may revoke the queued snapshot, but promotions never
            # silently grant additional knowledge privileges to an older request.
            role = run.user_access_level
            await self._append(session, run, "run_started", {"run_id": run.id, "message_id": run.message_id, "trace_id": run.trace_id})
            return self.run_payload(run) | {"lease_token": run.lease_token, "user_id": run.user_id,
                "user_access_level": role, "index_revision": run.index_revision, "content": content,
                "deadline_at": _iso(run.deadline_at)}

    async def heartbeat(self, run_id: str, token: str) -> bool:
        async with self.session_factory.begin() as session:
            run = await session.scalar(select(ProductionRun).where(ProductionRun.id == run_id).with_for_update())
            now = _now()
            if not self._valid_lease(run, token, now):
                return False
            if not await self._access_valid(session, run):
                await self._access_failure(session, run)
                return False
            run.lease_expires_at = min(run.deadline_at, now + timedelta(seconds=self.settings.lease_seconds))
            return True

    async def append_event(self, run_id: str, token: str, event_type: str, data: dict[str, Any]) -> bool:
        if event_type == "final":
            raise ValueError("final must be committed with terminal state")
        async with self.session_factory.begin() as session:
            run = await session.scalar(select(ProductionRun).where(ProductionRun.id == run_id).with_for_update())
            if not self._valid_lease(run, token, _now()):
                return False
            if not await self._access_valid(session, run):
                await self._access_failure(session, run)
                return False
            await self._append(session, run, event_type, data)
            return True

    async def history(self, run_id: str) -> list[dict[str, str]]:
        async with self.session_factory() as session:
            run = await session.get(ProductionRun, run_id)
            if run is None:
                raise HTTPException(404, "run_not_found")
            await self._owner(session, run.user_id, run.conversation_id)
            await current_role(session, run.user_id)
            rows = list((await session.execute(select(Message, ProductionRun.result).outerjoin(ProductionRun,
                ProductionRun.result_message_id == Message.id).where(Message.conversation_id == run.conversation_id, Message.id != run.message_id)
                .order_by(Message.created_at.desc(), Message.id.desc()).limit(20))).all())
        remaining = self.settings.budget_max_input_tokens // 2
        result: list[dict[str, str]] = []
        for message, prior in rows:
            content = history_content(message, prior)
            size = len(content.encode()) + 32
            if size > remaining:
                break
            remaining -= size
            result.append({"role": message.role, "content": content})
        return list(reversed(result))

    async def ticket_lookup_context(self, run_id: str, lease_token: str) -> dict[str, Any]:
        async with self.session_factory() as session:
            run = await session.get(ProductionRun, run_id)
            if not self._valid_lease(run, lease_token, _now()):
                raise HTTPException(409, "run_lease_lost")
            if not await self._access_valid(session, run):
                raise HTTPException(403, "account_access_changed")
            await self._owner(session, run.user_id, run.conversation_id)
            return await load_ticket_context(session, run.user_id, run.conversation_id, run.id, before=run.created_at)

    async def ticket_intake_context(self, run_id: str, lease_token: str) -> dict[str, Any] | None:
        from app.repositories.ticket_intake_context import load_ticket_intake_context
        async with self.session_factory() as session:
            run = await session.get(ProductionRun, run_id)
            if not self._valid_lease(run, lease_token, _now()):
                raise HTTPException(409, "run_lease_lost")
            if not await self._access_valid(session, run):
                raise HTTPException(403, "account_access_changed")
            await self._owner(session, run.user_id, run.conversation_id)
            return await load_ticket_intake_context(session, run.user_id, run.conversation_id, run.id, before=run.created_at)

    async def _settle(self, session, run: ProductionRun, usage: dict[str, Any] | None, *, never_started: bool = False) -> None:
        if run.settled_cost is not None:
            return
        budget = await session.scalar(select(ModelBudget).where(ModelBudget.month == run.budget_month).with_for_update())
        if never_started:
            cost = Decimal(0)
        elif usage is not None and not usage.get("usage_uncertain") and isinstance(usage.get("input_tokens"), int) and isinstance(usage.get("output_tokens"), int):
            run.input_tokens = max(0, usage["input_tokens"])
            run.output_tokens = max(0, usage["output_tokens"])
            cost = _money((Decimal(run.input_tokens) * run.input_price + Decimal(run.output_tokens) * run.output_price) / Decimal(1_000_000))
        else:
            # Timeout/provider failure may have consumed tokens without reporting usage.
            cost = run.reserved_cost
        budget.reserved = max(Decimal(0), budget.reserved - run.reserved_cost)
        budget.spent += cost
        run.settled_cost = cost

    async def _finish(self, session, run: ProductionRun, status: str, result: dict[str, Any], usage: dict[str, Any] | None = None) -> None:
        await self._settle(session, run, usage, never_started=run.started_at is None)
        result = dict(result) | {"run_id": run.id, "status": status, "trace_id": run.trace_id}
        if status == "failed" and result.get("final_state") == "handoff" and not result.get("escalation_id"):
            # Recovery owns the locked run row; expired leases cannot call the ordinary
            # business worker API. Persist the recovery handoff in this same transaction.
            from app.production.business_models import BusinessAudit, Escalation
            from app.production.handoff_context import build_handoff_context
            code = str(result.get("error") or "processing_failed")[:100]
            existing = await session.scalar(select(Escalation).where(Escalation.run_id == run.id))
            context = None if existing else await build_handoff_context(session, run)
            created_id = await session.scalar(insert(Escalation).values(id=str(uuid4()), user_id=run.user_id,
                conversation_id=run.conversation_id, run_id=run.id, trace_id=run.trace_id, reason=code,
                status="pending", version=1, context=context).on_conflict_do_nothing(index_elements=[Escalation.run_id]).returning(Escalation.id))
            escalation_id = created_id or await session.scalar(select(Escalation.id).where(Escalation.run_id == run.id))
            if created_id:
                session.add(BusinessAudit(entity_type="escalation", entity_id=created_id, actor_id=run.user_id,
                    event_type="created", details={"run_id": run.id, "trace_id": run.trace_id, "reason_code": code, "version": {"from": None, "to": 1}}))
            result["escalation_id"] = escalation_id
            await self._append(session, run, "handoff", {"reason": code, "escalation_id": escalation_id})
        if status == "completed":
            answer = str(result.get("answer") or "")
            assistant = Message(conversation_id=run.conversation_id, role="assistant", content=answer, citations=result.get("citations") or [])
            session.add(assistant)
            await session.flush()
            run.result_message_id = assistant.id
            result["message_id"] = assistant.id
        run.status, run.result = status, result
        run.error_code = result.get("error")
        run.finished_at, run.lease_expires_at, run.lease_token = _now(), None, None
        await self._append(session, run, "final", result)

    async def finalize(self, run_id: str, token: str, status: str, result: dict[str, Any], usage: dict[str, Any] | None = None) -> bool:
        if status not in TERMINAL:
            raise ValueError("invalid terminal state")
        async with self.session_factory.begin() as session:
            run = await session.scalar(select(ProductionRun).where(ProductionRun.id == run_id).with_for_update())
            if not self._valid_lease(run, token, _now()):
                return False
            if not await self._access_valid(session, run):
                await self._access_failure(session, run)
                return False
            await self._finish(session, run, status, result, usage)
            return True

    async def cancel(self, principal: Principal, run_id: str) -> dict[str, Any]:
        async with self.session_factory.begin() as session:
            run = await self._owned_run(session, principal, run_id, lock=True)
            if run.status not in TERMINAL:
                await self._finish(session, run, "cancelled", {"answer": "运行已取消。", "final_state": "cancelled", "error": "run_cancelled"})
            return await self._read_payload(session, principal, run)

    async def reap(self) -> int:
        async with self.session_factory.begin() as session:
            now = _now()
            rows = list(await session.scalars(select(ProductionRun).where(ProductionRun.status == "running",
                or_(ProductionRun.lease_expires_at <= now, ProductionRun.deadline_at <= now))
                .with_for_update(skip_locked=True).limit(100)))
            for run in rows:
                code = "run_timeout" if run.deadline_at <= now else "worker_lease_expired"
                await self._finish(session, run, "failed", {"answer": "运行中断，请重试或联系 IT 支持。", "final_state": "handoff", "error": code})
            return len(rows)


router = APIRouter(prefix="/api/v1", tags=["conversations", "runs"])
Auth = Annotated[Principal, Depends(require_principal)]


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str | None = Field(default=None, min_length=1, max_length=200)


class RunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    content: str = Field(min_length=1, max_length=10000)
    client_message_id: str = Field(min_length=1, max_length=128)


class FeedbackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feedback: Literal["resolved", "unresolved"]


@router.post("/conversations", status_code=201)
async def create_conversation(request: Request, body: ConversationCreate, principal: Auth):
    return await request.app.state.run_service.create_conversation(principal, body.title)


@router.get("/conversations")
async def list_conversations(request: Request, principal: Auth, cursor: str | None = None, limit: int = Query(50, ge=1, le=100)):
    return await request.app.state.run_service.conversations(principal, cursor, limit)


@router.post("/conversations/{conversation_id}/archive", status_code=204)
async def archive_conversation(request: Request, conversation_id: str, principal: Auth):
    await request.app.state.run_service.archive(principal, conversation_id)


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(request: Request, conversation_id: str, principal: Auth, cursor: str | None = None, limit: int = Query(50, ge=1, le=100)):
    return await request.app.state.run_service.messages(principal, conversation_id, cursor, limit)


@router.post("/messages/{message_id}/feedback", status_code=204)
async def feedback_message(request: Request, message_id: str, body: FeedbackCreate, principal: Auth):
    await request.app.state.run_service.record_feedback(principal, message_id, body.feedback)


@router.post("/conversations/{conversation_id}/runs", status_code=202)
async def create_run(request: Request, conversation_id: str, body: RunCreate, principal: Auth,
                     idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]):
    return await request.app.state.run_service.enqueue(principal, conversation_id, body.content, body.client_message_id, idempotency_key)


@router.get("/conversations/{conversation_id}/runs")
async def list_runs(request: Request, conversation_id: str, principal: Auth, cursor: str | None = None, limit: int = Query(50, ge=1, le=100)):
    return await request.app.state.run_service.conversation_runs(principal, conversation_id, cursor, limit)


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: str, principal: Auth):
    return await request.app.state.run_service.get_run(principal, run_id)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(request: Request, run_id: str, principal: Auth):
    return await request.app.state.run_service.cancel(principal, run_id)


@router.get("/runs/{run_id}/events")
async def stream_events(request: Request, run_id: str, principal: Auth, after: int = Query(0, ge=0),
                        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None):
    service: RunService = request.app.state.run_service
    await service.get_run(principal, run_id)
    if last_event_id is not None:
        try:
            after = max(after, int(last_event_id))
            if after < 0:
                raise ValueError()
        except ValueError as error:
            raise HTTPException(400, "invalid_last_event_id") from error

    async def iterate():
        sequence, idle = after, 0
        while not await request.is_disconnected():
            # Revalidate server session during long-lived subscriptions and after logout.
            current = await request.app.state.identity_service.principal(principal.session_id)
            events = await service.events(current, run_id, sequence)
            for event in events:
                sequence = event["sequence"]
                payload = json.dumps(event["data"], ensure_ascii=False, separators=(",", ":"))
                yield f"id: {sequence}\nevent: {event['type']}\ndata: {payload}\n\n"
            if events and events[-1]["type"] == "final":
                return
            state = await service.get_run(current, run_id)
            if state["status"] in TERMINAL:
                # A reconnect after the final sequence ends immediately.
                if not events:
                    return
                continue
            idle += 1
            if idle % 10 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.5)
    return StreamingResponse(iterate(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
