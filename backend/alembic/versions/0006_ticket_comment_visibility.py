"""Keep historical comment visibility unclassified until a human reviews it."""
from alembic import op
import sqlalchemy as sa

revision = "0006_comment_visibility"
down_revision = "0005_handoff_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("production_ticket_comments", sa.Column("visibility", sa.String(20), nullable=False, server_default="unclassified"))
    op.add_column("production_ticket_comments", sa.Column("author_role", sa.String(20), nullable=True))
    op.create_check_constraint("ck_ticket_comment_visibility", "production_ticket_comments", "visibility IN ('public','internal','unclassified')")
    op.create_index("ix_ticket_comment_progress", "production_ticket_comments", ["ticket_id", "created_at", "id"])


def downgrade() -> None:
    op.drop_index("ix_ticket_comment_progress", table_name="production_ticket_comments")
    op.drop_constraint("ck_ticket_comment_visibility", "production_ticket_comments", type_="check")
    op.drop_column("production_ticket_comments", "author_role")
    op.drop_column("production_ticket_comments", "visibility")
