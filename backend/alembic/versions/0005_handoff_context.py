"""Keep a bounded handoff snapshot; historical records remain explicitly unknown."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_handoff_context"
down_revision = "0004_markdown_structure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("production_escalations", sa.Column("context", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("production_escalations", "context")
