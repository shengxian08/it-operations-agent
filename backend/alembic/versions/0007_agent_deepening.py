"""Add task mode/state and versioned relation provenance; preserve all prior rows."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_agent_deepening"
down_revision = "0006_comment_visibility"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "production_runs",
        sa.Column("task_mode", sa.String(20), nullable=False, server_default="support"),
    )
    op.add_column(
        "production_runs", sa.Column("agent_state", postgresql.JSONB(), nullable=True)
    )
    op.create_table(
        "agent_graph_revisions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "knowledge_revision",
            sa.String(64),
            sa.ForeignKey("production_knowledge_revisions.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column(
            "created_by", sa.String(64), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("reviewed_by", sa.String(64), sa.ForeignKey("users.id")),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_agent_graph_revisions_knowledge_revision",
        "agent_graph_revisions",
        ["knowledge_revision"],
    )
    op.create_table(
        "agent_graph_relations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "graph_id",
            sa.String(64),
            sa.ForeignKey("agent_graph_revisions.id"),
            nullable=False,
        ),
        sa.Column("left_entity", sa.String(120), nullable=False),
        sa.Column("right_entity", sa.String(120), nullable=False),
        sa.Column("predicate", sa.String(50), nullable=False),
        sa.Column(
            "document_id",
            sa.String(64),
            sa.ForeignKey("production_knowledge_sources.id"),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("source", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint(
            "graph_id",
            "left_entity",
            "right_entity",
            "predicate",
            "document_id",
            "chunk_index",
            name="uq_agent_graph_edge",
        ),
    )
    for field in ("graph_id", "left_entity", "right_entity"):
        op.create_index(
            f"ix_agent_graph_relations_{field}", "agent_graph_relations", [field]
        )
    op.create_table(
        "agent_graph_activations",
        sa.Column(
            "knowledge_revision",
            sa.String(64),
            sa.ForeignKey("production_knowledge_revisions.id"),
            primary_key=True,
        ),
        sa.Column(
            "graph_id",
            sa.String(64),
            sa.ForeignKey("agent_graph_revisions.id"),
            nullable=False,
        ),
    )


def downgrade():
    op.drop_table("agent_graph_activations")
    op.drop_table("agent_graph_relations")
    op.drop_table("agent_graph_revisions")
    op.drop_column("production_runs", "agent_state")
    op.drop_column("production_runs", "task_mode")
