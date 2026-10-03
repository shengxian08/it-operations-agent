"""Add nullable source structure without certifying historical content.

Revision ID: 0004_markdown_structure
Revises: 7faac2b3f1a9
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0004_markdown_structure"
down_revision = "7faac2b3f1a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("production_knowledge_sources", sa.Column("parser_version", sa.String(100), nullable=True))
    op.add_column("production_knowledge_snapshots", sa.Column("parser_version", sa.String(100), nullable=True))
    op.add_column("production_knowledge_chunks", sa.Column("structure", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("production_knowledge_chunks", "structure")
    op.drop_column("production_knowledge_snapshots", "parser_version")
    op.drop_column("production_knowledge_sources", "parser_version")
