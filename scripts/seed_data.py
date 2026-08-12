import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if (BACKEND_ROOT / "app").is_dir():
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.models import Conversation, Ticket, TicketEvent, User  # noqa: E402
from app.db.session import async_engine, async_session_factory  # noqa: E402


SEED_FILE = Path(
    os.getenv(
        "SEED_FILE",
        str(PROJECT_ROOT / "data" / "tickets" / "seed_tickets.json"),
    )
).resolve()


def load_seed_manifest() -> dict[str, Any]:
    with SEED_FILE.open(encoding="utf-8") as seed_file:
        manifest = json.load(seed_file)
    generation = manifest.get("ticket_generation", {})
    if int(generation.get("count", 0)) <= 0:
        raise ValueError("ticket_generation.count must be positive")
    if not generation.get("templates"):
        raise ValueError("ticket_generation.templates must not be empty")
    return manifest


def build_ticket_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    generation = manifest["ticket_generation"]
    year = int(generation["year"])
    count = int(generation["count"])
    started_at = datetime.fromisoformat(generation["start_date"])
    owner_ids = generation["owner_ids"]
    statuses = generation["statuses"]
    status_updates = generation["status_updates"]
    templates = generation["templates"]
    rows: list[dict[str, Any]] = []

    for offset in range(count):
        template = templates[offset % len(templates)]
        status = statuses[offset % len(statuses)]
        rows.append(
            {
                "ticket_number": f"IT-{year}-{offset + 1:04d}",
                "user_id": owner_ids[offset % len(owner_ids)],
                "title": template["title"],
                "category": template["category"],
                "priority": template["priority"],
                "description": template["description"],
                "attempted_steps": template["attempted_steps"],
                "status": status,
                "created_at": started_at + timedelta(days=offset),
                "updated_at": started_at + timedelta(days=offset, hours=4),
                "latest_update": status_updates[status],
            }
        )
    return rows


async def seed_data() -> tuple[int, int, int]:
    manifest = load_seed_manifest()
    ticket_rows = build_ticket_rows(manifest)
    allowed_owner_ids = {user["id"] for user in manifest["users"]}

    async with async_session_factory.begin() as session:
        for user_data in manifest["users"]:
            user = await session.get(User, user_data["id"])
            if user is None:
                session.add(User(**user_data))
            else:
                user.display_name = user_data["display_name"]
                user.access_level = user_data["access_level"]
        await session.flush()

        for conversation_data in manifest["conversations"]:
            conversation = await session.get(
                Conversation,
                conversation_data["id"],
            )
            if conversation is None:
                session.add(Conversation(**conversation_data))
            else:
                conversation.user_id = conversation_data["user_id"]
                conversation.summary = conversation_data["summary"]
        await session.flush()

        for row in ticket_rows:
            ticket = await session.scalar(
                select(Ticket).where(
                    Ticket.ticket_number == row["ticket_number"]
                )
            )
            if ticket is not None and ticket.user_id not in allowed_owner_ids:
                raise RuntimeError(
                    f"refusing to overwrite non-seed ticket {ticket.ticket_number}"
                )
            if ticket is None:
                ticket = Ticket(
                    ticket_number=row["ticket_number"],
                    user_id=row["user_id"],
                    title=row["title"],
                    category=row["category"],
                    priority=row["priority"],
                    description=row["description"],
                    attempted_steps=row["attempted_steps"],
                    status=row["status"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
                session.add(ticket)
                await session.flush()
            else:
                ticket.user_id = row["user_id"]
                ticket.title = row["title"]
                ticket.category = row["category"]
                ticket.priority = row["priority"]
                ticket.description = row["description"]
                ticket.attempted_steps = row["attempted_steps"]
                ticket.status = row["status"]
                ticket.created_at = row["created_at"]
                ticket.updated_at = row["updated_at"]

            event = await session.scalar(
                select(TicketEvent).where(
                    TicketEvent.ticket_id == ticket.id,
                    TicketEvent.event_type == "seeded",
                )
            )
            if event is None:
                session.add(
                    TicketEvent(
                        ticket_id=ticket.id,
                        event_type="seeded",
                        details={"summary": row["latest_update"]},
                        created_at=row["updated_at"],
                    )
                )
            else:
                event.details = {"summary": row["latest_update"]}
                event.created_at = row["updated_at"]

    return len(manifest["users"]), len(manifest["conversations"]), len(ticket_rows)


async def main() -> None:
    try:
        users, conversations, tickets = await seed_data()
        print(
            f"Seeded {users} users, {conversations} conversations, "
            f"{tickets} tickets."
        )
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
