"""Immutable knowledge snapshots and a single authoritative active pointer."""
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base, new_id


class KnowledgeSource(Base):
    __tablename__ = "production_knowledge_sources"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source_path: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    parser_version: Mapped[str | None] = mapped_column(String(100))
    access_level: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    index_job_id: Mapped[str | None] = mapped_column(ForeignKey("production_knowledge_index_jobs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeJob(Base):
    __tablename__ = "production_knowledge_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source_path: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(300), nullable=False)
    access_level: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued", index=True)
    content: Mapped[str | None] = mapped_column(Text)
    sections: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(String(1000))
    document_id: Mapped[str | None] = mapped_column(ForeignKey("production_knowledge_sources.id"))
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    previewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    index_job_id: Mapped[str | None] = mapped_column(ForeignKey("production_knowledge_index_jobs.id"))


class KnowledgeRevision(Base):
    __tablename__ = "production_knowledge_revisions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    collection_name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ready")
    document_count: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(200), nullable=False)
    embedding_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    pipeline_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeActivePointer(Base):
    __tablename__ = "production_knowledge_active_pointer"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision_id: Mapped[str | None] = mapped_column(ForeignKey("production_knowledge_revisions.id"))


class KnowledgeSnapshot(Base):
    __tablename__ = "production_knowledge_snapshots"
    __table_args__ = (UniqueConstraint("revision_id", "document_id", name="uq_production_knowledge_snapshot_document"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    revision_id: Mapped[str] = mapped_column(ForeignKey("production_knowledge_revisions.id"), nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("production_knowledge_sources.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source_path: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    parser_version: Mapped[str | None] = mapped_column(String(100))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    access_level: Mapped[str] = mapped_column(String(30), nullable=False)


class RevisionChunk(Base):
    __tablename__ = "production_knowledge_chunks"
    __table_args__ = (UniqueConstraint("revision_id", "document_id", "chunk_index", name="uq_production_revision_chunk"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    revision_id: Mapped[str] = mapped_column(ForeignKey("production_knowledge_revisions.id"), nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("production_knowledge_sources.id"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    structure: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source_title: Mapped[str] = mapped_column(String(300), nullable=False)
    source_path: Mapped[str] = mapped_column(String(500), nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    access_level: Mapped[str] = mapped_column(String(30), nullable=False)


class KnowledgeIndexJob(Base):
    __tablename__ = "production_knowledge_index_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_id)
    request_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued", index=True)
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(String(100))
    staging_collections: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
