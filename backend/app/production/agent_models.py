"""Immutable graph versions with reviewed source-backed edges."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, new_id


class GraphRevision(Base):
    __tablename__ = "agent_graph_revisions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    knowledge_revision: Mapped[str] = mapped_column(
        ForeignKey("production_knowledge_revisions.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="candidate")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GraphRelation(Base):
    __tablename__ = "agent_graph_relations"
    __table_args__ = (
        UniqueConstraint(
            "graph_id",
            "left_entity",
            "right_entity",
            "predicate",
            "document_id",
            "chunk_index",
            name="uq_agent_graph_edge",
        ),
    )
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    graph_id: Mapped[str] = mapped_column(
        ForeignKey("agent_graph_revisions.id"), nullable=False, index=True
    )
    left_entity: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    right_entity: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    predicate: Mapped[str] = mapped_column(String(50), nullable=False)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("production_knowledge_sources.id"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)


class GraphActivation(Base):
    __tablename__ = "agent_graph_activations"
    knowledge_revision: Mapped[str] = mapped_column(
        String(64), ForeignKey("production_knowledge_revisions.id"), primary_key=True
    )
    graph_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("agent_graph_revisions.id"), nullable=False
    )
