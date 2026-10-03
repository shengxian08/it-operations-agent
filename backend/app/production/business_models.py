"""Production business records; legacy demo tables remain independent."""
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, new_id


class TicketDraftRecord(Base):
    __tablename__ = "production_ticket_drafts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), nullable=False, index=True)
    run_id: Mapped[str | None] = mapped_column(String(64))
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    draft: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    token_nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    ticket_number: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TicketCounter(Base):
    __tablename__ = "production_ticket_counters"
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ProductionTicket(Base):
    __tablename__ = "production_tickets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    ticket_number: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("production_ticket_drafts.id"), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    priority: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    attempted_steps: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class TicketConfirmation(Base):
    __tablename__ = "production_ticket_confirmations"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key", name="uq_production_ticket_confirmations_owner_key"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("production_tickets.id"), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProductionTicketComment(Base):
    __tablename__ = "production_ticket_comments"
    __table_args__ = (CheckConstraint("visibility IN ('public','internal','unclassified')", name="ck_ticket_comment_visibility"),
                     Index("ix_ticket_comment_progress", "ticket_id", "created_at", "id"))
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    ticket_id: Mapped[str] = mapped_column(ForeignKey("production_tickets.id"), nullable=False, index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    visibility: Mapped[str] = mapped_column(String(20), nullable=False, server_default="unclassified")
    author_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BusinessAudit(Base):
    __tablename__ = "production_business_audits"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Escalation(Base):
    __tablename__ = "production_escalations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), nullable=False)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    assignee_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
