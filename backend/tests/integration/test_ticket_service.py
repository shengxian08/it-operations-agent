import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.models import Conversation, Ticket, TicketEvent, ToolAudit, User
from app.repositories.tickets import TicketRepository
from app.schemas import TicketDraft
from app.tickets.service import TicketService


@dataclass(frozen=True, slots=True)
class TicketTestContext:
    service: TicketService
    session_factory: async_sessionmaker[AsyncSession]
    redis: Redis
    confirmation_key_prefix: str
    owner_id: str
    other_user_id: str
    conversation_id: str


@pytest_asyncio.fixture
async def ticket_context() -> AsyncIterator[TicketTestContext]:
    suffix = uuid4().hex
    owner_id = f"test-owner-{suffix}"
    other_user_id = f"test-other-{suffix}"
    conversation_id = f"test-conversation-{suffix}"
    confirmation_key_prefix = f"test:ticket-confirmation:{suffix}"
    database_url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://itops:itops-local-only@localhost:15432/itops",
    )
    redis_url = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/15")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(redis_url, decode_responses=True)

    async with session_factory.begin() as session:
        session.add_all(
            [
                User(id=owner_id, display_name="测试员工甲"),
                User(id=other_user_id, display_name="测试员工乙"),
            ]
        )
        await session.flush()
        session.add(Conversation(id=conversation_id, user_id=owner_id))

    context = TicketTestContext(
        service=TicketService(
            TicketRepository(session_factory),
            redis,
            confirmation_key_prefix=confirmation_key_prefix,
        ),
        session_factory=session_factory,
        redis=redis,
        confirmation_key_prefix=confirmation_key_prefix,
        owner_id=owner_id,
        other_user_id=other_user_id,
        conversation_id=conversation_id,
    )

    try:
        yield context
    finally:
        keys = await redis.keys(f"{confirmation_key_prefix}:*")
        if keys:
            await redis.delete(*keys)

        async with session_factory.begin() as session:
            ticket_ids = select(Ticket.id).where(
                Ticket.user_id.in_([owner_id, other_user_id])
            )
            await session.execute(
                delete(ToolAudit).where(ToolAudit.ticket_id.in_(ticket_ids))
            )
            await session.execute(
                delete(ToolAudit).where(
                    ToolAudit.idempotency_key.like(f"%{suffix}%")
                )
            )
            await session.execute(
                delete(Ticket).where(
                    Ticket.user_id.in_([owner_id, other_user_id])
                )
            )
            await session.execute(
                delete(Conversation).where(Conversation.id == conversation_id)
            )
            await session.execute(
                delete(User).where(User.id.in_([owner_id, other_user_id]))
            )

        await redis.aclose()
        await engine.dispose()


def ticket_draft(**changes: object) -> TicketDraft:
    values: dict[str, object] = {
        "title": "VPN 无法连接",
        "category": "network",
        "priority": "medium",
        "description": "客户端提示身份验证失败",
        "attempted_steps": ["重新输入密码", "重启 VPN 客户端"],
    }
    values.update(changes)
    return TicketDraft.model_validate(values)


async def write_counts(context: TicketTestContext) -> tuple[int, int, int]:
    async with context.session_factory() as session:
        ticket_ids = select(Ticket.id).where(
            Ticket.user_id.in_([context.owner_id, context.other_user_id])
        )
        tickets = await session.scalar(
            select(func.count()).select_from(Ticket).where(
                Ticket.user_id.in_([context.owner_id, context.other_user_id])
            )
        )
        events = await session.scalar(
            select(func.count()).select_from(TicketEvent).where(
                TicketEvent.ticket_id.in_(ticket_ids)
            )
        )
        audits = await session.scalar(
            select(func.count()).select_from(ToolAudit).where(
                ToolAudit.ticket_id.in_(ticket_ids)
            )
        )
    return int(tickets or 0), int(events or 0), int(audits or 0)


def test_ticket_draft_is_deeply_immutable() -> None:
    draft = ticket_draft()

    assert draft.attempted_steps == (
        "重新输入密码",
        "重启 VPN 客户端",
    )
    with pytest.raises(ValidationError, match="frozen"):
        draft.title = "被篡改的标题"
    with pytest.raises(AttributeError):
        draft.attempted_steps.append("被篡改的步骤")


@pytest.mark.asyncio
async def test_create_ticket_rejects_unconfirmed_draft_without_writes(
    ticket_context: TicketTestContext,
) -> None:
    with pytest.raises(PermissionError, match="confirmation token is required"):
        await ticket_context.service.create_confirmed(
            user_id=ticket_context.owner_id,
            draft=ticket_draft(),
            confirmation_token=None,
            idempotency_key="unconfirmed-key",
        )

    assert await write_counts(ticket_context) == (0, 0, 0)


@pytest.mark.asyncio
async def test_create_ticket_rejects_blank_idempotency_key_without_writes(
    ticket_context: TicketTestContext,
) -> None:
    draft = ticket_draft()
    token = await ticket_context.service.issue_confirmation_token(
        ticket_context.conversation_id,
        draft,
    )

    with pytest.raises(PermissionError, match="idempotency key is required"):
        await ticket_context.service.create_confirmed(
            ticket_context.owner_id,
            draft,
            token,
            "   ",
        )

    assert await write_counts(ticket_context) == (0, 0, 0)


@pytest.mark.asyncio
async def test_confirmation_token_is_bound_to_exact_draft_without_writes(
    ticket_context: TicketTestContext,
) -> None:
    original = ticket_draft()
    changed = ticket_draft(description="被修改后的问题描述")
    token = await ticket_context.service.issue_confirmation_token(
        ticket_context.conversation_id,
        original,
    )

    with pytest.raises(PermissionError, match="confirmation does not match"):
        await ticket_context.service.create_confirmed(
            ticket_context.owner_id,
            changed,
            token,
            "changed-draft-key",
        )

    assert await write_counts(ticket_context) == (0, 0, 0)


@pytest.mark.asyncio
async def test_confirmation_token_is_bound_to_conversation_user_without_writes(
    ticket_context: TicketTestContext,
) -> None:
    draft = ticket_draft()
    token = await ticket_context.service.issue_confirmation_token(
        ticket_context.conversation_id,
        draft,
    )

    with pytest.raises(PermissionError, match="confirmation does not match"):
        await ticket_context.service.create_confirmed(
            ticket_context.other_user_id,
            draft,
            token,
            "wrong-user-key",
        )

    assert await write_counts(ticket_context) == (0, 0, 0)


@pytest.mark.asyncio
async def test_confirmation_token_has_ten_minute_ttl_and_stores_no_raw_draft(
    ticket_context: TicketTestContext,
) -> None:
    draft = ticket_draft()
    token = await ticket_context.service.issue_confirmation_token(
        ticket_context.conversation_id,
        draft,
    )

    keys = await ticket_context.redis.keys(
        f"{ticket_context.confirmation_key_prefix}:*"
    )
    assert len(keys) == 1
    ttl = await ticket_context.redis.ttl(keys[0])
    stored_confirmation = await ticket_context.redis.get(keys[0])

    assert 590 <= ttl <= 600
    assert token not in keys[0]
    assert token not in (stored_confirmation or "")
    assert draft.title not in (stored_confirmation or "")
    assert draft.description not in (stored_confirmation or "")


@pytest.mark.asyncio
async def test_create_ticket_is_idempotent_and_writes_complete_audit_trail(
    ticket_context: TicketTestContext,
) -> None:
    draft = ticket_draft()
    token = await ticket_context.service.issue_confirmation_token(
        ticket_context.conversation_id,
        draft,
    )

    first = await ticket_context.service.create_confirmed(
        ticket_context.owner_id,
        draft,
        token,
        "idempotent-key",
    )
    second = await ticket_context.service.create_confirmed(
        ticket_context.owner_id,
        draft,
        token,
        "idempotent-key",
    )

    assert first == second
    number_prefix, number_year, number_sequence = first.ticket_number.split("-")
    assert number_prefix == "IT"
    assert len(number_year) == 4 and number_year.isdigit()
    assert len(number_sequence) >= 4 and number_sequence.isdigit()
    assert first.status == "pending"
    assert await write_counts(ticket_context) == (1, 1, 1)

    async with ticket_context.session_factory() as session:
        audit = await session.scalar(
            select(ToolAudit).where(
                ToolAudit.idempotency_key == "idempotent-key"
            )
        )
    assert audit is not None
    assert audit.tool_name == "create_ticket"
    assert audit.result_category == "created"
    assert audit.confirmation_token_hash
    assert draft.title not in audit.request_summary
    assert draft.description not in audit.request_summary


@pytest.mark.asyncio
async def test_reused_idempotency_key_rejects_changed_draft_without_extra_write(
    ticket_context: TicketTestContext,
) -> None:
    original = ticket_draft()
    first_token = await ticket_context.service.issue_confirmation_token(
        ticket_context.conversation_id,
        original,
    )
    await ticket_context.service.create_confirmed(
        ticket_context.owner_id,
        original,
        first_token,
        "reused-key",
    )

    changed = ticket_draft(priority="high")
    changed_token = await ticket_context.service.issue_confirmation_token(
        ticket_context.conversation_id,
        changed,
    )
    with pytest.raises(PermissionError, match="idempotency key does not match"):
        await ticket_context.service.create_confirmed(
            ticket_context.owner_id,
            changed,
            changed_token,
            "reused-key",
        )

    assert await write_counts(ticket_context) == (1, 1, 1)


@pytest.mark.asyncio
async def test_idempotency_key_used_by_another_tool_is_rejected_without_writes(
    ticket_context: TicketTestContext,
) -> None:
    idempotency_key = f"foreign-tool-{ticket_context.owner_id}"
    async with ticket_context.session_factory.begin() as session:
        session.add(
            ToolAudit(
                tool_name="get_ticket_status",
                request_summary="read-only lookup",
                result_category="not_found",
                idempotency_key=idempotency_key,
                trace_id="foreign-tool-trace",
            )
        )

    draft = ticket_draft()
    token = await ticket_context.service.issue_confirmation_token(
        ticket_context.conversation_id,
        draft,
    )
    with pytest.raises(PermissionError, match="idempotency key does not match"):
        await ticket_context.service.create_confirmed(
            ticket_context.owner_id,
            draft,
            token,
            idempotency_key,
        )

    assert await write_counts(ticket_context) == (0, 0, 0)


@pytest.mark.asyncio
async def test_ticket_status_hides_existence_from_non_owner(
    ticket_context: TicketTestContext,
) -> None:
    draft = ticket_draft()
    token = await ticket_context.service.issue_confirmation_token(
        ticket_context.conversation_id,
        draft,
    )
    created = await ticket_context.service.create_confirmed(
        ticket_context.owner_id,
        draft,
        token,
        "status-key",
    )

    owner_result = await ticket_context.service.get_ticket_status(
        ticket_context.owner_id,
        created.ticket_number,
    )
    unauthorized_result = await ticket_context.service.get_ticket_status(
        ticket_context.other_user_id,
        created.ticket_number,
    )
    missing_result = await ticket_context.service.get_ticket_status(
        ticket_context.other_user_id,
        "IT-1999-9999",
    )

    assert owner_result.found is True
    assert owner_result.ticket_number == created.ticket_number
    assert owner_result.status == "pending"
    assert owner_result.latest_update == "工单已创建，等待 IT 支持处理。"
    assert unauthorized_result == missing_result
    assert unauthorized_result.found is False
    assert unauthorized_result.ticket_number is None


@pytest.mark.asyncio
async def test_seed_script_is_idempotent_and_creates_complete_demo_catalog(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[3]
    script_path = project_root / "scripts" / "seed_data.py"
    manifest = json.loads(
        (project_root / "data" / "tickets" / "seed_tickets.json").read_text(
            encoding="utf-8"
        )
    )
    suffix = uuid4().hex
    seed_user_ids = [f"seed-test-owner-{suffix}", f"seed-test-other-{suffix}"]
    for user, user_id in zip(manifest["users"], seed_user_ids, strict=True):
        user["id"] = user_id
    for conversation, user_id in zip(
        manifest["conversations"],
        seed_user_ids,
        strict=True,
    ):
        conversation["id"] = f"seed-test-conversation-{uuid4().hex}"
        conversation["user_id"] = user_id
    manifest["ticket_generation"]["year"] = 1901
    manifest["ticket_generation"]["owner_ids"] = seed_user_ids
    test_seed_file = tmp_path / "seed_tickets.json"
    test_seed_file.write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    database_url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://itops:itops-local-only@localhost:15432/itops",
    )
    environment = os.environ.copy()
    environment["DATABASE_URL"] = database_url
    environment["SEED_FILE"] = str(test_seed_file)
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def run_seed_script() -> tuple[int, str]:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script_path),
            cwd=project_root,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await process.communicate()
        return process.returncode or 0, output.decode("utf-8", errors="replace")

    try:
        first_code, first_output = await run_seed_script()
        second_code, second_output = await run_seed_script()

        assert first_code == 0, first_output
        assert second_code == 0, second_output
        assert "100 tickets" in first_output
        assert "100 tickets" in second_output

        async with session_factory() as session:
            ticket_ids = select(Ticket.id).where(
                Ticket.user_id.in_(seed_user_ids)
            )
            ticket_count = await session.scalar(
                select(func.count()).select_from(Ticket).where(
                    Ticket.user_id.in_(seed_user_ids)
                )
            )
            event_count = await session.scalar(
                select(func.count()).select_from(TicketEvent).where(
                    TicketEvent.ticket_id.in_(ticket_ids)
                )
            )
            statuses = set(
                (
                    await session.scalars(
                        select(Ticket.status).where(
                            Ticket.user_id.in_(seed_user_ids)
                        )
                    )
                ).all()
            )

        assert ticket_count == 100
        assert event_count == 100
        assert statuses == {"pending", "in_progress", "resolved", "closed"}
    finally:
        async with session_factory.begin() as session:
            await session.execute(
                delete(Ticket).where(Ticket.user_id.in_(seed_user_ids))
            )
            await session.execute(
                delete(Conversation).where(
                    Conversation.user_id.in_(seed_user_ids)
                )
            )
            await session.execute(
                delete(User).where(User.id.in_(seed_user_ids))
            )
        await engine.dispose()
