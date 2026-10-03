"""Read-only views of the material that the demo agent can actually use."""

import hashlib
import re
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import KnowledgeChunk, KnowledgeDocument, Ticket, User
from app.rag.ingest import extract_text
from app.rag.retriever import ACCESS_HIERARCHY


class CatalogNotFoundError(Exception):
    pass


class SourceOutOfSyncError(Exception):
    pass


class CatalogService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        knowledge_dir: Path,
    ) -> None:
        self._session_factory = session_factory
        self._knowledge_dir = knowledge_dir.resolve()

    async def list_documents(self, user_id: str) -> dict[str, object]:
        async with self._session_factory() as session:
            allowed = await self._allowed_levels(session, user_id)
            rows = (
                await session.execute(
                    select(KnowledgeDocument, func.count(KnowledgeChunk.id))
                    .join(KnowledgeChunk, KnowledgeChunk.document_id == KnowledgeDocument.id)
                    .where(
                        KnowledgeDocument.status == "active",
                        KnowledgeDocument.access_level.in_(allowed),
                    )
                    .group_by(KnowledgeDocument.id)
                    .order_by(KnowledgeDocument.source_title)
                )
            ).all()
        return {
            "documents": [
                {
                    "id": document.id,
                    "title": document.source_title,
                    "source_path": document.source_path,
                    "version": document.version,
                    "chunk_count": chunk_count,
                }
                for document, chunk_count in rows
            ]
        }

    async def get_document(self, user_id: str, document_id: str) -> dict[str, object]:
        async with self._session_factory() as session:
            allowed = await self._allowed_levels(session, user_id)
            document = await session.scalar(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.id == document_id,
                    KnowledgeDocument.status == "active",
                    KnowledgeDocument.access_level.in_(allowed),
                )
            )
            if document is None:
                raise CatalogNotFoundError

        source = (self._knowledge_dir / document.source_path).resolve()
        if not source.is_relative_to(self._knowledge_dir) or not source.is_file():
            raise SourceOutOfSyncError
        try:
            text = extract_text(source)
        except (OSError, ValueError, RuntimeError) as error:
            raise SourceOutOfSyncError from error
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != document.content_hash:
            raise SourceOutOfSyncError
        return {
            "id": document.id,
            "title": document.source_title,
            "source_path": document.source_path,
            "version": document.version,
            "sections": _article_sections(text),
        }

    async def list_tickets(self, user_id: str) -> dict[str, object]:
        async with self._session_factory() as session:
            await self._allowed_levels(session, user_id)
            tickets = (
                await session.scalars(
                    select(Ticket)
                    .where(Ticket.user_id == user_id)
                    .order_by(Ticket.ticket_number)
                    .limit(100)
                )
            ).all()
        return {
            "tickets": [
                {
                    "ticket_number": ticket.ticket_number,
                    "title": ticket.title,
                    "category": ticket.category,
                    "priority": ticket.priority,
                    "status": ticket.status,
                }
                for ticket in tickets
            ]
        }

    @staticmethod
    async def _allowed_levels(session: AsyncSession, user_id: str) -> tuple[str, ...]:
        user = await session.get(User, user_id)
        if user is None:
            raise CatalogNotFoundError
        return ACCESS_HIERARCHY.get(user.access_level, (user.access_level,))


def _article_sections(text: str) -> list[dict[str, str]]:
    body = re.sub(r"\A---\s*\n.*?\n---\s*(?:\n|\Z)", "", text, flags=re.DOTALL)
    sections: list[dict[str, str]] = []
    heading: str | None = None
    lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("# "):
            continue
        if line.startswith("## "):
            if heading is not None:
                sections.append({"heading": heading, "body": "\n".join(lines).strip()})
            heading = line[3:].strip()
            lines = []
        elif heading is not None:
            lines.append(line)
    if heading is not None:
        sections.append({"heading": heading, "body": "\n".join(lines).strip()})
    if sections:
        return sections
    return [{"heading": "正文", "body": body.strip()}]
