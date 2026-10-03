"""Persistent production jobs and replayable events, separate from demo graph runs."""
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, new_id


class ProductionRun(Base):
    __tablename__ = "production_runs"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_production_run_idempotency"),
        UniqueConstraint("conversation_id", "client_message_id", name="uq_production_run_client_message"),
        Index("uq_production_run_active_conversation", "conversation_id", unique=True,
              postgresql_where=text("status IN ('queued','running')")),
        Index("ix_production_run_queue", "status", "created_at"),
        Index("ix_production_run_lease", "status", "lease_expires_at"),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), nullable=False, unique=True)
    result_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), unique=True)
    client_message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued", server_default="queued")
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    user_access_level: Mapped[str] = mapped_column(String(50), nullable=False)
    index_revision: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    next_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(100))
    lease_token: Mapped[str | None] = mapped_column(String(64))
    worker_id: Mapped[str | None] = mapped_column(String(200))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    budget_month: Mapped[str] = mapped_column(String(7), nullable=False)
    reserved_cost: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    settled_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    input_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    output_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ProductionRunEvent(Base):
    __tablename__ = "production_run_events"
    run_id: Mapped[str] = mapped_column(ForeignKey("production_runs.id", ondelete="CASCADE"), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ModelBudget(Base):
    __tablename__ = "production_model_budgets"
    month: Mapped[str] = mapped_column(String(7), primary_key=True)
    reserved: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, default=Decimal(0), server_default="0")
    spent: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, default=Decimal(0), server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
