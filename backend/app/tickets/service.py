import hashlib
import json
import secrets
from typing import Any
from uuid import uuid4

from redis.asyncio import Redis

from app.repositories.tickets import StoredTicket, TicketRepository
from app.schemas import TicketCreateResult, TicketDraft, TicketStatusResult


class TicketService:
    def __init__(
        self,
        repository: TicketRepository,
        redis: Redis,
        *,
        confirmation_ttl_seconds: int = 600,
        confirmation_key_prefix: str = "ticket:confirmation",
    ) -> None:
        self._repository = repository
        self._redis = redis
        self._confirmation_ttl_seconds = confirmation_ttl_seconds
        self._confirmation_key_prefix = confirmation_key_prefix.rstrip(":")

    async def get_ticket_status(
        self,
        user_id: str,
        ticket_number: str,
    ) -> TicketStatusResult:
        record = await self._repository.get_status_for_user(
            user_id,
            ticket_number,
        )
        if record is None:
            return TicketStatusResult(
                found=False,
                latest_update="未找到可访问的工单。",
            )
        return TicketStatusResult(
            found=True,
            ticket_number=record.ticket_number,
            status=record.status,
            latest_update=record.latest_update,
        )

    async def issue_confirmation_token(
        self,
        conversation_id: str,
        draft: TicketDraft,
    ) -> str:
        user_id = await self._repository.get_conversation_user_id(conversation_id)
        if user_id is None:
            raise PermissionError("conversation is not available")

        token = secrets.token_urlsafe(32)
        token_hash = self._hash_text(token)
        confirmation = json.dumps(
            {
                "conversation_id": conversation_id,
                "user_id": user_id,
                "draft_hash": self._draft_hash(draft),
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        await self._redis.set(
            self._confirmation_key(token_hash),
            confirmation,
            ex=self._confirmation_ttl_seconds,
        )
        return token

    async def create_confirmed(
        self,
        user_id: str,
        draft: TicketDraft,
        confirmation_token: str | None,
        idempotency_key: str,
    ) -> TicketCreateResult:
        if not confirmation_token or not confirmation_token.strip():
            raise PermissionError("confirmation token is required")
        if not idempotency_key or not idempotency_key.strip():
            raise PermissionError("idempotency key is required")
        idempotency_key = idempotency_key.strip()
        if len(idempotency_key) > 128:
            raise PermissionError("idempotency key is invalid")

        draft_hash = self._draft_hash(draft)
        request_summary = self._request_summary(draft, draft_hash)
        existing = await self._repository.get_by_idempotency_key(idempotency_key)
        if existing is not None:
            return self._validated_result(
                existing,
                user_id=user_id,
                request_summary=request_summary,
            )

        token_hash = self._hash_text(confirmation_token)
        confirmation_key = self._confirmation_key(token_hash)
        stored_confirmation = await self._redis.get(confirmation_key)
        confirmation = self._load_confirmation(stored_confirmation)
        if (
            confirmation.get("user_id") != user_id
            or confirmation.get("draft_hash") != draft_hash
        ):
            raise PermissionError("confirmation does not match request")

        consumed_confirmation = await self._redis.getdel(confirmation_key)
        if self._as_text(consumed_confirmation) != self._as_text(stored_confirmation):
            raise PermissionError("confirmation does not match request")

        stored = await self._repository.create_ticket(
            user_id=user_id,
            draft=draft,
            request_summary=request_summary,
            confirmation_token_hash=token_hash,
            idempotency_key=idempotency_key,
            trace_id=f"ticket-create-{uuid4()}",
        )
        return self._validated_result(
            stored,
            user_id=user_id,
            request_summary=request_summary,
        )

    @staticmethod
    def _validated_result(
        stored: StoredTicket,
        *,
        user_id: str,
        request_summary: str,
    ) -> TicketCreateResult:
        if stored.user_id != user_id or stored.request_summary != request_summary:
            raise PermissionError("idempotency key does not match request")
        return TicketCreateResult(
            ticket_number=stored.ticket_number,
            status=stored.status,
        )

    @staticmethod
    def _draft_hash(draft: TicketDraft) -> str:
        canonical_draft = json.dumps(
            draft.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return TicketService._hash_text(canonical_draft)

    @staticmethod
    def _request_summary(draft: TicketDraft, draft_hash: str) -> str:
        return json.dumps(
            {
                "category": draft.category,
                "draft_hash": draft_hash,
                "priority": draft.priority,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _hash_text(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _confirmation_key(self, token_hash: str) -> str:
        return f"{self._confirmation_key_prefix}:{token_hash}"

    @staticmethod
    def _load_confirmation(stored: Any) -> dict[str, Any]:
        stored_text = TicketService._as_text(stored)
        if stored_text is None:
            raise PermissionError("confirmation does not match request")
        try:
            confirmation = json.loads(stored_text)
        except (TypeError, json.JSONDecodeError) as error:
            raise PermissionError("confirmation does not match request") from error
        if not isinstance(confirmation, dict):
            raise PermissionError("confirmation does not match request")
        return confirmation

    @staticmethod
    def _as_text(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)
