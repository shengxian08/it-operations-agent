"""Owner scoped confirmation transactions and support lifecycle API."""
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, text, tuple_
from sqlalchemy.dialects.postgresql import insert

from app.db.models import Conversation, User
from app.production.identity_models import IdentityAccount
from app.production.business_models import (BusinessAudit, Escalation, ProductionTicket,
    ProductionTicketComment, TicketConfirmation, TicketCounter, TicketDraftRecord)
from app.production.identity import Principal, require_principal, require_support
from app.schemas import TicketCreateResult, TicketDraft, TicketStatusResult
from app.agent.ticket_intake import draft_facts_complete
from app.production.ticket_progress import STAFF, current_role, latest_record, progress_source, safe_audit

router = APIRouter(prefix="/api/v1", tags=["production-business"])


def now() -> datetime:
    return datetime.now(timezone.utc)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def format_ticket_number(year: int, sequence: int) -> str:
    return f"IT-{year}-{sequence:04d}"


def validate_transition(current: str, target: str, role: str) -> None:
    transitions = {"pending": {"in_progress", "closed"}, "in_progress": {"resolved", "closed"},
                   "resolved": {"closed", "in_progress"}, "closed": {"in_progress"}}
    if role not in {"support", "admin"}:
        if not (current in {"resolved", "closed"} and target == "in_progress"):
            raise HTTPException(403, "support role is required for this transition")
    if target != current and target not in transitions.get(current, set()):
        raise HTTPException(409, "invalid status transition")


def record_dict(record: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: getattr(record, field) for field in fields}


TICKET_FIELDS = ("id", "ticket_number", "user_id", "title", "category", "priority", "description",
                 "attempted_steps", "status", "version", "assignee_id", "created_at", "updated_at")
ESCALATION_FIELDS = ("id", "user_id", "conversation_id", "run_id", "reason", "status", "version", "assignee_id", "created_at")


class ProductionTicketService:
    def __init__(self, session_factory: Any, settings: Any):
        self.session_factory = session_factory
        self.settings = settings

    def _token(self, draft: TicketDraftRecord) -> str:
        secret = self.settings.session_secret
        if hasattr(secret, "get_secret_value"):
            secret = secret.get_secret_value()
        body = f"{draft.id}:{draft.version}:{draft.token_nonce}"
        return hmac.new(str(secret).encode(), body.encode(), hashlib.sha256).hexdigest()

    def _rotate(self, draft: TicketDraftRecord) -> str:
        draft.token_nonce = secrets.token_hex(24)
        draft.expires_at = now() + timedelta(seconds=getattr(self.settings, "ticket_confirmation_ttl_seconds", 600))
        token = self._token(draft)
        draft.token_hash = digest(token)
        return token

    def _draft_dict(self, draft: TicketDraftRecord) -> dict[str, Any]:
        requires_details = not draft_facts_complete(draft.draft)
        return {"id": draft.id, "draft_id": draft.id, "conversation_id": draft.conversation_id,
                "run_id": draft.run_id,
                "version": draft.version, "draft": draft.draft, "status": draft.status,
                "ticket_number": draft.ticket_number,
                "requires_details": requires_details,
                "confirmation_token": self._token(draft) if draft.status == "pending" and not requires_details else None,
                "expires_at": draft.expires_at}

    @staticmethod
    async def _validate_run(session, user_id: str, conversation_id: str, run_id: str, lease_token: str | None):
        from app.production.run_models import ProductionRun
        run = await session.scalar(select(ProductionRun).where(ProductionRun.id == run_id).with_for_update())
        if (run is None or run.user_id != user_id or run.conversation_id != conversation_id
            or run.status != "running" or run.lease_expires_at is None or run.lease_expires_at <= now()
            or run.deadline_at is None or run.deadline_at <= now()
            or (lease_token is not None and run.lease_token != lease_token)):
            raise PermissionError("run lease is no longer active")

    async def issue_confirmation_token(self, conversation_id: str, draft: TicketDraft,
                                       trace_id: str | None = None, run_id: str | None = None,
                                       lease_token: str | None = None) -> str:
        async with self.session_factory.begin() as session:
            conversation = await session.get(Conversation, conversation_id)
            if conversation is None:
                raise PermissionError("conversation is not available")
            if run_id is not None:
                await self._validate_run(session, conversation.user_id, conversation_id, run_id, lease_token)
            if not draft_facts_complete(draft):
                raise HTTPException(422, "ticket_details_incomplete")
            row = TicketDraftRecord(id=str(uuid4()), user_id=conversation.user_id, conversation_id=conversation_id,
                                    trace_id=trace_id or str(uuid4()), run_id=run_id, version=1,
                                    draft=draft.model_dump(mode="json"), status="pending")
            token = self._rotate(row)
            session.add(row)
        return token

    async def describe_draft_token(self, token: str, user_id: str, conversation_id: str) -> dict[str, Any]:
        async with self.session_factory() as session:
            row = await session.scalar(select(TicketDraftRecord).where(TicketDraftRecord.token_hash == digest(token),
                TicketDraftRecord.user_id == user_id, TicketDraftRecord.conversation_id == conversation_id))
            if row is None:
                raise HTTPException(404, "draft not found")
            return self._draft_dict(row)

    async def list_drafts(self, user_id: str, conversation_id: str | None = None) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            query = select(TicketDraftRecord).where(TicketDraftRecord.user_id == user_id)
            if conversation_id:
                query = query.where(TicketDraftRecord.conversation_id == conversation_id)
            rows = await session.scalars(query.order_by(TicketDraftRecord.created_at.desc()).limit(100))
            return [self._draft_dict(row) for row in rows]

    async def edit_draft(self, user_id: str, draft_id: str, version: int, draft: TicketDraft) -> dict[str, Any]:
        async with self.session_factory.begin() as session:
            row = await session.scalar(select(TicketDraftRecord).where(TicketDraftRecord.id == draft_id,
                TicketDraftRecord.user_id == user_id).with_for_update())
            if row is None:
                raise HTTPException(404, "draft not found")
            if row.version != version or row.status != "pending":
                raise HTTPException(409, "draft version or status changed")
            if not draft_facts_complete(draft):
                raise HTTPException(422, "ticket_details_incomplete")
            row.version += 1
            row.draft = draft.model_dump(mode="json")
            self._rotate(row)
            session.add(BusinessAudit(entity_type="draft", entity_id=row.id, actor_id=user_id,
                                      event_type="edited", details={"version": row.version}))
            return self._draft_dict(row)

    @staticmethod
    def _check_draft(row: TicketDraftRecord, version: int) -> None:
        if row.version != version or row.status != "pending":
            raise HTTPException(409, "draft version or status changed")
        if row.expires_at <= now():
            raise HTTPException(410, "confirmation has expired")
        if not draft_facts_complete(row.draft):
            raise HTTPException(422, "ticket_details_incomplete")

    async def confirm_draft(self, user_id: str, draft_id: str, version: int, token: str, idempotency_key: str) -> dict[str, Any]:
        if not idempotency_key.strip() or len(idempotency_key) > 128:
            raise HTTPException(400, "valid Idempotency-Key is required")
        request_hash = digest(json.dumps([draft_id, version, digest(token)], separators=(",", ":")))
        async with self.session_factory.begin() as session:
            # Serializes a user's idempotency key even when concurrent requests target different drafts.
            lock_key = int.from_bytes(hashlib.sha256(f"{user_id}:{idempotency_key}".encode()).digest()[:8], "big", signed=True)
            await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
            existing = await session.scalar(select(TicketConfirmation).where(TicketConfirmation.user_id == user_id,
                TicketConfirmation.idempotency_key == idempotency_key))
            if existing:
                if existing.request_hash != request_hash:
                    raise HTTPException(409, "idempotency key was used for another request")
                return existing.result
            row = await session.scalar(select(TicketDraftRecord).where(TicketDraftRecord.id == draft_id,
                TicketDraftRecord.user_id == user_id).with_for_update())
            if row is None:
                raise HTTPException(404, "draft not found")
            self._check_draft(row, version)
            if not hmac.compare_digest(row.token_hash, digest(token)):
                raise HTTPException(403, "confirmation token does not match draft")
            year = now().year
            statement = insert(TicketCounter).values(year=year, value=1).on_conflict_do_update(
                index_elements=[TicketCounter.year], set_={"value": TicketCounter.value + 1}).returning(TicketCounter.value)
            sequence = await session.scalar(statement)
            ticket = ProductionTicket(id=str(uuid4()), ticket_number=format_ticket_number(year, sequence),
                user_id=user_id, draft_id=row.id,
                **{key: row.draft[key] for key in ("title", "category", "priority", "description", "attempted_steps")},
                status="pending", version=1)
            session.add(ticket)
            await session.flush()
            result = {"ticket_number": ticket.ticket_number, "status": ticket.status}
            session.add(TicketConfirmation(user_id=user_id, idempotency_key=idempotency_key,
                request_hash=request_hash, ticket_id=ticket.id, result=result))
            row.status, row.ticket_number = "confirmed", ticket.ticket_number
            session.add(BusinessAudit(entity_type="ticket", entity_id=ticket.id, actor_id=user_id,
                event_type="created", details={"draft_id": row.id, "trace_id": row.trace_id}))
            return result

    async def get_ticket_status(self, user_id: str, ticket_number: str) -> TicketStatusResult:
        async with self.session_factory.begin() as session:
            try:
                role = await current_role(session, user_id)
            except HTTPException as error:
                raise PermissionError("account access is not available") from error
            query = select(ProductionTicket).where(ProductionTicket.ticket_number == ticket_number)
            if role not in STAFF:
                query = query.where(ProductionTicket.user_id == user_id)
            row = await session.scalar(query.with_for_update())
            # A blocked row lock can outlive a role change; never reuse pre-wait authorization.
            try:
                role = await current_role(session, user_id)
            except HTTPException as error:
                raise PermissionError("account access is not available") from error
            if row is None or role not in STAFF and row.user_id != user_id:
                return TicketStatusResult(found=False, latest_update="未找到可访问的工单。")
            record = await latest_record(session, row.id, role)
            return TicketStatusResult(found=True, ticket_number=row.ticket_number, status=row.status,
                latest_update=record["summary"] if record else "暂无可见处理记录。",
                updated_at=record["updated_at"] if record else None, update_kind=record["update_kind"] if record else None,
                progress_source=progress_source(row, record))

    async def record_handoff(self, user_id: str, conversation_id: str, run_id: str, reason: str, trace_id: str,
                             lease_token: str | None = None) -> dict[str, Any]:
        async with self.session_factory.begin() as session:
            conversation = await session.get(Conversation, conversation_id)
            if conversation is None or conversation.user_id != user_id:
                raise PermissionError("conversation is not available")
            await self._validate_run(session, user_id, conversation_id, run_id, lease_token)
            existing = await session.scalar(select(Escalation).where(Escalation.run_id == run_id, Escalation.user_id == user_id))
            if existing is not None:
                return record_dict(existing, ESCALATION_FIELDS)
            from app.production.handoff_context import build_handoff_context
            from app.production.run_models import ProductionRun
            context = await build_handoff_context(session, await session.get(ProductionRun, run_id))
            statement = insert(Escalation).values(id=str(uuid4()), user_id=user_id, conversation_id=conversation_id,
                run_id=run_id, reason=reason[:10000], trace_id=trace_id, status="pending", version=1, context=context).on_conflict_do_nothing(index_elements=[Escalation.run_id]).returning(Escalation.id)
            inserted_id = await session.scalar(statement)
            if inserted_id is not None:
                session.add(BusinessAudit(entity_type="escalation", entity_id=inserted_id, actor_id=user_id,
                    event_type="created", details={"run_id": run_id, "trace_id": trace_id, "version": {"from": None, "to": 1}}))
            row = await session.scalar(select(Escalation).where(Escalation.run_id == run_id, Escalation.user_id == user_id))
            if row is None:
                raise PermissionError("run is not available")
            return record_dict(row, ESCALATION_FIELDS)

    @staticmethod
    async def _assignee(session: Any, assignee_id: str | None) -> None:
        if assignee_id is not None:
            user = await session.scalar(select(User).join(IdentityAccount, IdentityAccount.user_id == User.id).where(
                User.id == assignee_id, IdentityAccount.enabled.is_(True)).with_for_update())
            if user is None or user.access_level not in {"support", "admin"}:
                raise HTTPException(400, "assignee must be a support user")

    @staticmethod
    async def current_actor(session, principal: Principal) -> Principal:
        user = (await session.execute(select(User.id, User.display_name, User.access_level).join(IdentityAccount, IdentityAccount.user_id == User.id).where(
            User.id == principal.user_id, IdentityAccount.enabled.is_(True)))).first()
        if user is None:
            raise HTTPException(403, "account_access_changed")
        if user.access_level not in {"employee", "support", "admin"}:
            raise HTTPException(403, "account_access_changed")
        return Principal(user.id, user.display_name, user.access_level, getattr(principal, "session_id", ""), getattr(principal, "csrf_token", ""))

    async def escalation_detail(self, principal: Principal, identifier: str, audit_cursor: str | None = None) -> dict[str, Any]:
        from app.production.handoff_context import readable_handoff_context
        async with self.session_factory() as session:
            principal = await self.current_actor(session, principal)
            row = await session.get(Escalation, identifier)
            if row is None or (principal.role not in {"support", "admin"} and row.user_id != principal.user_id):
                raise HTTPException(404, "escalation not found")
            query = select(BusinessAudit).where(BusinessAudit.entity_type == "escalation", BusinessAudit.entity_id == row.id)
            if audit_cursor:
                cursor = await session.scalar(query.where(BusinessAudit.id == audit_cursor))
                if cursor is None:
                    raise HTTPException(400, "invalid_audit_cursor")
                query = query.where(tuple_(BusinessAudit.created_at, BusinessAudit.id) < (cursor.created_at, cursor.id))
            audits = list(await session.scalars(query.order_by(BusinessAudit.created_at.desc(), BusinessAudit.id.desc()).limit(51)))
            return {**record_dict(row, ESCALATION_FIELDS), "context": await readable_handoff_context(session, row.context, principal.role),
                "audit": [{**record_dict(item, ("id", "actor_id", "event_type", "created_at")),
                    "details": {key: item.details[key] for key in ("status", "assignee_id", "version") if key in item.details}}
                    for item in audits[:50]], "next_audit_cursor": audits[49].id if len(audits) > 50 else None}

    async def ticket_detail(self, principal: Principal, number: str) -> dict[str, Any]:
        async with self.session_factory.begin() as session:
            principal = await self.current_actor(session, principal)
            row = await self._owned_ticket(session, principal, number, True)
            principal = await self.current_actor(session, principal)
            if principal.role not in STAFF and row.user_id != principal.user_id:
                raise HTTPException(404, "ticket not found")
            result = record_dict(row, TICKET_FIELDS)
            query = select(ProductionTicketComment).where(ProductionTicketComment.ticket_id == row.id)
            if principal.role not in STAFF:
                query = query.where(ProductionTicketComment.visibility == "public")
            comments = list(await session.scalars(query.order_by(ProductionTicketComment.created_at, ProductionTicketComment.id)))
            audit = await session.scalars(select(BusinessAudit).where(BusinessAudit.entity_type == "ticket", BusinessAudit.entity_id == row.id).order_by(BusinessAudit.created_at, BusinessAudit.id))
            result["comments"] = [record_dict(item, ("id", "author_id", "content", "visibility", "author_role", "created_at")) for item in comments]
            visible_comments = {item.id: item for item in comments}
            result["audit"] = []
            for item in audit:
                safe = safe_audit(item)
                if safe:
                    details = safe[1]
                elif item.event_type == "commented" and item.details.get("comment_id") in visible_comments:
                    details = {"comment_id": item.details["comment_id"]}
                elif principal.role in STAFF and item.event_type == "comment_visibility_changed":
                    details = {key: item.details[key] for key in ("comment_id", "from", "to") if key in item.details}
                else:
                    continue
                result["audit"].append({**record_dict(item, ("id", "actor_id", "event_type", "created_at")), "details": details})
            if principal.role not in STAFF:
                record = await latest_record(session, row.id, principal.role)
                result["updated_at"] = max(row.created_at, record["updated_at"]) if record else row.created_at
            return result

    @staticmethod
    async def _owned_ticket(session: Any, principal: Principal, number: str, lock: bool = False) -> ProductionTicket:
        query = select(ProductionTicket).where(ProductionTicket.ticket_number == number)
        if principal.role not in {"support", "admin"}:
            query = query.where(ProductionTicket.user_id == principal.user_id)
        row = await session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise HTTPException(404, "ticket not found")
        return row

    async def update_ticket(self, principal: Principal, number: str, patch: "TicketPatch") -> dict[str, Any]:
        async with self.session_factory.begin() as session:
            principal = await self.current_actor(session, principal)
            row = await self._owned_ticket(session, principal, number, True)
            principal = await self.current_actor(session, principal)
            if principal.role not in STAFF and row.user_id != principal.user_id:
                raise HTTPException(404, "ticket not found")
            if row.version != patch.version:
                raise HTTPException(409, "ticket version changed")
            changes = {}
            if patch.status is not None:
                validate_transition(row.status, patch.status, principal.role)
                changes["status"] = {"from": row.status, "to": patch.status}
                row.status = patch.status
            if "assignee_id" in patch.model_fields_set:
                if principal.role not in {"support", "admin"}:
                    raise HTTPException(403, "support role is required to assign tickets")
                await self._assignee(session, patch.assignee_id)
                changes["assignee_id"] = {"from": row.assignee_id, "to": patch.assignee_id}
                row.assignee_id = patch.assignee_id
            row.version += 1
            row.updated_at = now()
            session.add(BusinessAudit(entity_type="ticket", entity_id=row.id, actor_id=principal.user_id,
                event_type="updated", details=changes))
            return record_dict(row, TICKET_FIELDS)

    async def add_comment(self, principal: Principal, number: str, version: int, content: str, visibility: str = "public") -> dict[str, Any]:
        if visibility not in {"public", "internal"} or not content.strip() or len(content) > 10000:
            raise HTTPException(422, "invalid_comment")
        async with self.session_factory.begin() as session:
            principal = await self.current_actor(session, principal)
            if visibility != "public" and principal.role not in STAFF:
                raise HTTPException(403, "support role is required for internal notes")
            row = await self._owned_ticket(session, principal, number, True)
            principal = await self.current_actor(session, principal)
            if principal.role not in STAFF and row.user_id != principal.user_id:
                raise HTTPException(404, "ticket not found")
            if row.version != version:
                raise HTTPException(409, "ticket version changed")
            if visibility != "public" and principal.role not in STAFF:
                raise HTTPException(403, "support role is required for internal notes")
            comment = ProductionTicketComment(id=str(uuid4()), ticket_id=row.id, author_id=principal.user_id,
                content=content.strip(), visibility=visibility, author_role=principal.role)
            session.add(comment)
            row.version += 1
            row.updated_at = now()
            session.add(BusinessAudit(entity_type="ticket", entity_id=row.id, actor_id=principal.user_id,
                event_type="commented", details={"version": row.version, "comment_id": comment.id}))
            await session.flush()
            return {**record_dict(comment, ("id", "author_id", "content", "visibility", "author_role", "created_at")), "version": row.version}

    async def classify_comment(self, principal: Principal, number: str, comment_id: str, version: int, visibility: str) -> dict[str, Any]:
        if visibility not in {"public", "internal"}:
            raise HTTPException(422, "invalid_comment_visibility")
        async with self.session_factory.begin() as session:
            principal = await self.current_actor(session, principal)
            if principal.role not in STAFF:
                raise HTTPException(403, "support_access_required")
            row = await self._owned_ticket(session, principal, number, True)
            principal = await self.current_actor(session, principal)
            if principal.role not in STAFF and row.user_id != principal.user_id:
                raise HTTPException(404, "ticket not found")
            if row.version != version:
                raise HTTPException(409, "ticket version changed")
            if principal.role not in STAFF:
                raise HTTPException(403, "support_access_required")
            comment = await session.scalar(select(ProductionTicketComment).where(
                ProductionTicketComment.id == comment_id, ProductionTicketComment.ticket_id == row.id).with_for_update())
            if comment is None:
                raise HTTPException(404, "comment not found")
            previous = comment.visibility
            comment.visibility = visibility
            row.version += 1
            row.updated_at = now()
            session.add(BusinessAudit(entity_type="ticket", entity_id=row.id, actor_id=principal.user_id,
                event_type="comment_visibility_changed", details={"comment_id": comment.id, "from": previous, "to": visibility, "version": row.version}))
            return {"id": comment.id, "visibility": visibility, "version": row.version}


class DraftPatch(BaseModel):
    version: int = Field(ge=1)
    draft: TicketDraft


class DraftConfirmation(BaseModel):
    version: int = Field(ge=1)
    confirmation_token: str = Field(min_length=1, max_length=200)


class TicketPatch(BaseModel):
    version: int = Field(ge=1)
    status: str | None = None
    assignee_id: str | None = None


class CommentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=10000)
    visibility: Literal["public", "internal"] = "public"

    @field_validator("content")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("comment must not be blank")
        return value.strip()


class CommentVisibility(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    visibility: Literal["public", "internal"]


def service(request: Request) -> ProductionTicketService:
    return request.app.state.ticket_service


@router.get("/ticket-drafts")
async def list_drafts(request: Request, conversation_id: str | None = None, principal: Principal = Depends(require_principal)):
    return {"drafts": await service(request).list_drafts(principal.user_id, conversation_id)}


@router.patch("/ticket-drafts/{draft_id}")
async def patch_draft(draft_id: str, body: DraftPatch, request: Request, principal: Principal = Depends(require_principal)):
    return await service(request).edit_draft(principal.user_id, draft_id, body.version, body.draft)


@router.post("/ticket-drafts/{draft_id}/confirm")
async def confirm_draft(draft_id: str, body: DraftConfirmation, request: Request,
                        idempotency_key: str = Header(alias="Idempotency-Key"), principal: Principal = Depends(require_principal)):
    return await service(request).confirm_draft(principal.user_id, draft_id, body.version, body.confirmation_token, idempotency_key)


@router.get("/tickets")
async def list_tickets(request: Request, cursor: str | None = None, limit: int = 50, principal: Principal = Depends(require_principal)):
    async with service(request).session_factory() as session:
        principal = await service(request).current_actor(session, principal)
        query = select(ProductionTicket)
        if principal.role not in {"support", "admin"}:
            query = query.where(ProductionTicket.user_id == principal.user_id)
        if cursor:
            query = query.where(ProductionTicket.id > cursor)
        limit = max(1, min(limit, 100))
        rows = list(await session.scalars(query.order_by(ProductionTicket.id).limit(limit + 1)))
        records = []
        for row in rows[:limit]:
            payload = record_dict(row, TICKET_FIELDS)
            if principal.role not in STAFF:
                record = await latest_record(session, row.id, principal.role)
                payload["updated_at"] = max(row.created_at, record["updated_at"]) if record else row.created_at
            records.append(payload)
        return {"tickets": records, "next_cursor": rows[limit - 1].id if len(rows) > limit else None}


@router.get("/tickets/{number}")
async def ticket_detail(number: str, request: Request, principal: Principal = Depends(require_principal)):
    return await service(request).ticket_detail(principal, number)


@router.patch("/tickets/{number}")
async def patch_ticket(number: str, body: TicketPatch, request: Request, principal: Principal = Depends(require_principal)):
    return await service(request).update_ticket(principal, number, body)


@router.post("/tickets/{number}/comments")
async def post_comment(number: str, body: CommentBody, request: Request, principal: Principal = Depends(require_principal)):
    return await service(request).add_comment(principal, number, body.version, body.content, body.visibility)


@router.patch("/tickets/{number}/comments/{comment_id}")
async def classify_comment(number: str, comment_id: str, body: CommentVisibility, request: Request, principal: Principal = Depends(require_support)):
    return await service(request).classify_comment(principal, number, comment_id, body.version, body.visibility)


@router.get("/support/assignees")
async def assignees(request: Request, principal: Principal = Depends(require_support)):
    async with service(request).session_factory() as session:
        principal = await service(request).current_actor(session, principal)
        if principal.role not in {"support", "admin"}:
            raise HTTPException(403, "support_access_required")
        users = await session.scalars(select(User).join(IdentityAccount, IdentityAccount.user_id == User.id).where(
            User.access_level.in_(["support", "admin"]), IdentityAccount.enabled.is_(True)).order_by(User.display_name))
        return {"users": [{"id": user.id, "display_name": user.display_name, "role": user.access_level} for user in users]}


@router.get("/escalations")
async def list_escalations(request: Request, cursor: str | None = None, limit: int = 50, principal: Principal = Depends(require_principal)):
    async with service(request).session_factory() as session:
        principal = await service(request).current_actor(session, principal)
        query = select(Escalation)
        if principal.role not in {"support", "admin"}:
            query = query.where(Escalation.user_id == principal.user_id)
        if cursor:
            query = query.where(Escalation.id > cursor)
        limit = max(1, min(limit, 100))
        rows = list(await session.scalars(query.order_by(Escalation.id).limit(limit + 1)))
        return {"escalations": [record_dict(row, ESCALATION_FIELDS) for row in rows[:limit]], "next_cursor": rows[limit - 1].id if len(rows) > limit else None}


@router.get("/escalations/{escalation_id}")
async def get_escalation(escalation_id: str, request: Request, audit_cursor: str | None = None, principal: Principal = Depends(require_principal)):
    return await service(request).escalation_detail(principal, escalation_id, audit_cursor)


@router.patch("/escalations/{escalation_id}")
async def patch_escalation(escalation_id: str, body: TicketPatch, request: Request, principal: Principal = Depends(require_support)):
    async with service(request).session_factory.begin() as session:
        principal = await service(request).current_actor(session, principal)
        if principal.role not in {"support", "admin"}:
            raise HTTPException(403, "support_access_required")
        row = await session.scalar(select(Escalation).where(Escalation.id == escalation_id).with_for_update())
        if row is None:
            raise HTTPException(404, "escalation not found")
        principal = await service(request).current_actor(session, principal)
        if principal.role not in {"support", "admin"}:
            raise HTTPException(403, "support_access_required")
        if row.version != body.version:
            raise HTTPException(409, "escalation version changed")
        details = {"version": {"from": row.version, "to": row.version + 1}}
        if body.status is not None:
            validate_transition(row.status, body.status, principal.role)
            details["status"] = {"from": row.status, "to": body.status}
            row.status = body.status
        if "assignee_id" in body.model_fields_set:
            await service(request)._assignee(session, body.assignee_id)
            details["assignee_id"] = {"from": row.assignee_id, "to": body.assignee_id}
            row.assignee_id = body.assignee_id
        row.version += 1
        session.add(BusinessAudit(entity_type="escalation", entity_id=row.id, actor_id=principal.user_id,
                                  event_type="updated", details=details))
        return record_dict(row, ESCALATION_FIELDS)
