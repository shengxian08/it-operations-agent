"""Add run result linkage and bad-case feedback records.

Revision ID: 0002_observability_feedback
Revises: 0001_initial_schema
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0002_observability_feedback"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs", sa.Column("result_message_id", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "agent_runs", sa.Column("model_name", sa.String(length=200), nullable=True)
    )
    op.create_foreign_key(
        "fk_agent_runs_result_message_id_messages",
        "agent_runs",
        "messages",
        ["result_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "uq_agent_runs_result_message_id",
        "agent_runs",
        ["result_message_id"],
        unique=True,
    )
    op.create_table(
        "bad_cases",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("assistant_message_id", sa.String(length=64), nullable=False),
        sa.Column("agent_run_id", sa.String(length=64), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("final_state", sa.String(length=100), nullable=True),
        sa.Column(
            "citation_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"], ["messages.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bad_cases_trace_id", "bad_cases", ["trace_id"])
    op.create_index(
        "uq_bad_cases_assistant_message_id",
        "bad_cases",
        ["assistant_message_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_bad_cases_assistant_message_id", table_name="bad_cases")
    op.drop_index("ix_bad_cases_trace_id", table_name="bad_cases")
    op.drop_table("bad_cases")
    op.drop_index("uq_agent_runs_result_message_id", table_name="agent_runs")
    op.drop_constraint(
        "fk_agent_runs_result_message_id_messages", "agent_runs", type_="foreignkey"
    )
    op.drop_column("agent_runs", "model_name")
    op.drop_column("agent_runs", "result_message_id")
