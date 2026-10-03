import hashlib
import os
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import KnowledgeChunk, KnowledgeDocument, Ticket, User
from app.main import app
from app.services.catalog import CatalogService


def test_catalog_shows_only_indexed_accessible_sources_and_own_tickets(tmp_path) -> None:
    suffix = uuid4().hex[:10]
    employee_id = f"catalog-employee-{suffix}"
    other_id = f"catalog-other-{suffix}"
    source_name = f"vpn-catalog-{suffix}.md"
    article_text = (
        '---\nversion: "1.0"\naccess_level: employee\n---\n\n'
        '# VPN 演示资料\n\n## 操作步骤\n\n先检查网络，再重新连接 VPN。\n'
    )
    (tmp_path / source_name).write_text(article_text, encoding="utf-8")
    document_ids = [str(uuid4()) for _ in range(3)]
    ticket_number = f"IT-2026-{int(suffix[:8], 16):010d}"
    other_ticket_number = f"IT-2026-{int(suffix[:8], 16) + 1:010d}"
    database_url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://itops:itops-local-only@localhost:15432/itops",
    )
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    original_service = getattr(app.state, "catalog_service", None)
    app.state.catalog_service = CatalogService(session_factory, tmp_path)

    async def setup() -> None:
        async with session_factory.begin() as session:
            session.add_all(
                [
                    User(id=employee_id, display_name="员工", access_level="employee"),
                    User(id=other_id, display_name="其他员工", access_level="employee"),
                ]
            )
            await session.flush()
            for document_id, access_level, status in zip(
                document_ids,
                ("employee", "support", "employee"),
                ("active", "active", "inactive"),
                strict=True,
            ):
                session.add(
                    KnowledgeDocument(
                        id=document_id,
                        source_title="VPN 演示资料",
                        source_path=source_name,
                        version=document_id[:8],
                        content_hash=hashlib.sha256(article_text.encode()).hexdigest(),
                        access_level=access_level,
                        status=status,
                    )
                )
            await session.flush()
            for document_id in document_ids:
                session.add(
                    KnowledgeChunk(
                        id=str(uuid4()),
                        document_id=document_id,
                        chunk_index=0,
                        content="先检查网络，再重新连接 VPN。",
                        source_title="VPN 演示资料",
                        source_path=source_name,
                        char_start=0,
                        char_end=18,
                        access_level="employee",
                    )
                )
            session.add_all(
                [
                    Ticket(
                        ticket_number=ticket_number,
                        user_id=employee_id,
                        title="VPN 无法连接",
                        category="network",
                        priority="medium",
                        description="无法连接",
                        attempted_steps=[],
                        status="pending",
                    ),
                    Ticket(
                        ticket_number=other_ticket_number,
                        user_id=other_id,
                        title="其他人的工单",
                        category="network",
                        priority="high",
                        description="无法连接",
                        attempted_steps=[],
                        status="in_progress",
                    ),
                ]
            )

    async def cleanup() -> None:
        async with session_factory.begin() as session:
            await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id.in_(document_ids)))
            await session.execute(delete(KnowledgeDocument).where(KnowledgeDocument.id.in_(document_ids)))
            await session.execute(delete(Ticket).where(Ticket.ticket_number.in_([ticket_number, other_ticket_number])))
            await session.execute(delete(User).where(User.id.in_([employee_id, other_id])))
        await engine.dispose()

    with TestClient(app) as client:
        client.portal.call(setup)
        try:
            response = client.get("/api/knowledge", params={"user_id": employee_id})
            assert response.status_code == 200
            documents = response.json()["documents"]
            visible_ids = {item["id"] for item in documents}
            assert document_ids[0] in visible_ids
            assert document_ids[1] not in visible_ids
            assert document_ids[2] not in visible_ids
            assert next(item for item in documents if item["id"] == document_ids[0])["chunk_count"] == 1

            article = client.get(f"/api/knowledge/{document_ids[0]}", params={"user_id": employee_id})
            assert article.status_code == 200
            assert article.json()["sections"] == [
                {"heading": "操作步骤", "body": "先检查网络，再重新连接 VPN。"}
            ]
            assert client.get(
                f"/api/knowledge/{document_ids[1]}", params={"user_id": employee_id}
            ).status_code == 404
            assert client.get(
                f"/api/knowledge/{document_ids[2]}", params={"user_id": employee_id}
            ).status_code == 404
            assert client.get("/api/knowledge", params={"user_id": "missing-user"}).status_code == 404

            tickets = client.get("/api/tickets", params={"user_id": employee_id})
            assert tickets.status_code == 200
            assert tickets.json()["tickets"] == [
                {
                    "ticket_number": ticket_number,
                    "title": "VPN 无法连接",
                    "category": "network",
                    "priority": "medium",
                    "status": "pending",
                }
            ]
        finally:
            client.portal.call(cleanup)
            app.state.catalog_service = original_service
