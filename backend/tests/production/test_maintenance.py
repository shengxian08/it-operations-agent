import os
from types import SimpleNamespace
from uuid import uuid4
from datetime import datetime,timedelta,timezone
import pytest
import pytest_asyncio
from sqlalchemy import select,text
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from app.db.models import Base,User,Conversation,Message
from app.production import identity_models,run_models,knowledge_models,business_models
from app.production.maintenance import retention


@pytest_asyncio.fixture
async def ctx():
    schema="maintenance_test_"+uuid4().hex
    admin=create_async_engine(os.environ["TEST_DATABASE_URL"])
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine=create_async_engine(os.environ["TEST_DATABASE_URL"],connect_args={"server_settings":{"search_path":schema}})
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    yield SimpleNamespace(session_factory=factory)
    await engine.dispose()
    async with admin.begin() as connection:
        await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    await admin.dispose()


@pytest.mark.asyncio
async def test_retention_dry_run_then_purges_content_without_removing_identity(ctx):
    now=datetime.now(timezone.utc)
    async with ctx.session_factory.begin() as session:
        session.add(User(id="employee",display_name="Employee")); await session.flush()
        session.add(Conversation(id="conversation",user_id="employee")); await session.flush()
        session.add_all([Message(id="old",conversation_id="conversation",role="user",content="Old private question",created_at=now-timedelta(days=91)),
            Message(id="recent",conversation_id="conversation",role="user",content="Recent question",created_at=now-timedelta(days=89)),
            business_models.BusinessAudit(id="old-audit",entity_type="account",entity_id="employee",actor_id="employee",event_type="changed",details={},created_at=now-timedelta(days=181))])
    counts=await retention(ctx,False)
    assert counts["chat_messages"]==1 and counts["business_audits"]==1
    async with ctx.session_factory() as session:
        assert (await session.get(Message,"old")).content=="Old private question"
    await retention(ctx,True)
    async with ctx.session_factory() as session:
        assert "90天" in (await session.get(Message,"old")).content
        assert (await session.get(Message,"recent")).content=="Recent question"
        assert await session.get(business_models.BusinessAudit,"old-audit") is None
        assert await session.get(User,"employee") is not None
