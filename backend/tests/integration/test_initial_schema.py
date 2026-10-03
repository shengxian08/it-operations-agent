import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db.models import Base


REQUIRED_TABLES = {
    "users",
    "conversations",
    "messages",
    "agent_runs",
    "tickets",
    "ticket_events",
    "tool_audits",
    "knowledge_documents",
    "knowledge_chunks",
    "bad_cases",
}


@pytest_asyncio.fixture
async def async_engine() -> AsyncIterator[AsyncEngine]:
    database_url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://itops:itops-local-only@localhost:15432/itops",
    )
    engine = create_async_engine(database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


def test_model_metadata_declares_required_tables_and_fields() -> None:
    assert REQUIRED_TABLES <= set(Base.metadata.tables)

    message_columns = set(Base.metadata.tables["messages"].columns.keys())
    assert {"citations", "user_feedback"} <= message_columns

    run_columns = set(Base.metadata.tables["agent_runs"].columns.keys())
    assert {
        "trace_id",
        "intent",
        "final_state",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "handoff_reason",
        "result_message_id",
        "model_name",
    } <= run_columns


@pytest.mark.asyncio
async def test_initial_migration_creates_required_schema(
    async_engine: AsyncEngine,
) -> None:
    async with async_engine.connect() as connection:
        table_result = await connection.exec_driver_sql(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
        tables = {row[0] for row in table_result}

        column_result = await connection.exec_driver_sql(
            """
            SELECT table_name, column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name IN ('messages', 'agent_runs')
            """
        )
        columns = {
            (row[0], row[1]): (row[2], row[3])
            for row in column_result
        }

        index_result = await connection.exec_driver_sql(
            """
            SELECT tablename, indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename IN ('tickets', 'tool_audits')
            """
        )
        indexes = {
            (row[0], row[1]): row[2]
            for row in index_result
        }

    assert REQUIRED_TABLES <= tables
    assert columns[("messages", "citations")] == ("jsonb", "NO")
    assert columns[("messages", "user_feedback")] == ("jsonb", "YES")
    assert {
        "trace_id",
        "intent",
        "final_state",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "handoff_reason",
    } <= {
        column_name
        for table_name, column_name in columns
        if table_name == "agent_runs"
    }
    assert "UNIQUE" in indexes[("tickets", "uq_tickets_ticket_number")]
    assert "UNIQUE" in indexes[
        ("tool_audits", "uq_tool_audits_idempotency_key")
    ]
