"""Prepared uploads, immutable complete index revisions, and permission checked retrieval."""
import asyncio
import hashlib
import json
import re
import sys
import threading
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from qdrant_client import models
from rank_bm25 import BM25Okapi
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from app.production.business import now, record_dict
from app.production.business_models import BusinessAudit
from app.production.identity import Principal, require_admin, require_principal
from app.production.knowledge_models import KnowledgeActivePointer, KnowledgeIndexJob, KnowledgeJob, KnowledgeRevision, KnowledgeSnapshot, KnowledgeSource, RevisionChunk
from app.rag.ingest import DeterministicEmbedder, SentenceTransformerEmbedder, EmbeddingInputTooLong, chunk_markdown, chunk_search_text, search_text, tokenize
from app.rag.pdf import requires_pdf_reparse
from app.rag.markdown import PARSER_VERSION, markdown_sections, requires_markdown_reparse
from app.rag.retriever import ACCESS_HIERARCHY, LexicalReranker, RetrievalHit, _min_max, _to_hit

router = APIRouter(prefix="/api/v1/knowledge", tags=["production-knowledge"])
admin_router = APIRouter(prefix="/api/v1/admin/knowledge", tags=["production-knowledge-admin"])
JOB_FIELDS = ("id", "status", "title", "access_level", "document_id", "index_job_id", "created_at", "finished_at", "error")
SOURCE_FIELDS = ("id", "title", "access_level", "status", "version", "index_job_id", "created_at")


class ThreadedDeterministicEmbedder(DeterministicEmbedder):
    async def encode(self, texts):
        return await asyncio.to_thread(lambda: [self._encode_one(text) for text in texts])


class ActorAccessChanged(PermissionError):
    pass


_embedder_cache: dict[tuple, Any] = {}
_embedder_lock = threading.Lock()


def _cached_embedder(mode: str, model: str, revision: str, dimensions: int):
    key = (mode, model, revision, dimensions)
    with _embedder_lock:
        if key not in _embedder_cache:
            if mode in {"hash", "deterministic"}:
                embedder = ThreadedDeterministicEmbedder(dimensions=dimensions)
            else:
                if mode not in {"semantic", "sentence-transformer"}:
                    raise ValueError("unknown embedding mode")
                if model != "BAAI/bge-small-zh-v1.5":
                    raise ValueError("production semantic embedding model must be BAAI/bge-small-zh-v1.5")
                if not re.fullmatch(r"[a-fA-F0-9]{40}", revision):
                    raise ValueError("semantic embedding revision must be an immutable 40 character commit hash")
                embedder = SentenceTransformerEmbedder(model, revision=revision)
            if len(_embedder_cache) >= 4:
                _embedder_cache.pop(next(iter(_embedder_cache)))
            _embedder_cache[key] = embedder
        return _embedder_cache[key]


def build_embedder(settings):
    """Shared by imports, serving and evaluation; cached by complete model identity."""
    return _cached_embedder(settings.embedding_mode, settings.embedding_model,
                            settings.embedding_revision, getattr(settings, "embedding_hash_dim", 256))


def embedding_identity(settings) -> tuple[str, str]:
    if settings.embedding_mode in {"hash", "deterministic"}:
        return f"deterministic:{getattr(settings, 'embedding_hash_dim', 256)}", "sha256-token-v1"
    return settings.embedding_model, settings.embedding_revision


def expected_dimensions(settings) -> int:
    return getattr(settings, "embedding_hash_dim", 256) if settings.embedding_mode in {"hash", "deterministic"} else getattr(settings, "embedding_dimensions", 512)


def legacy_pipeline_config(settings) -> dict[str, Any]:
    return {"version": 1, "max_chars": 800, "overlap_chars": 120, "tokenizer": "cjk-bigram-latin-v1",
            "pdf_extraction": "ruled-cells-normalized-page-context-v2", "pdf_chunking": "atomic-row-page-v1", "max_table_chars": 800,
            "legacy_pdf_policy": "exclude-unlocated-sources-v1",
            "normalize_embeddings": True,
            "query_instruction": "" if settings.embedding_mode in {"hash", "deterministic"} else "为这个句子生成表示以用于检索相关文章：",
            "distance": "Cosine", "reranker": "lexical-v1",
            "vector_weight": 0.65, "bm25_weight": 0.35,
            "minimum_evidence_score": float(getattr(settings, "minimum_evidence_score", 0.35))}


def pipeline_config(settings) -> dict[str, Any]:
    return {**legacy_pipeline_config(settings), "version": 2, "markdown_parser": PARSER_VERSION,
            "markdown_chunking": "atomic-block-source-spans-v1", "markdown_search_context": "source-section-path-v1",
            "legacy_markdown_policy": "exclude-unreviewed-sources-v1", "semantic_token_limit": 512}


def revision_matches_settings(revision, settings) -> bool:
    return ((revision.embedding_model, revision.embedding_revision) == embedding_identity(settings)
            and revision.dimensions == expected_dimensions(settings)
            and revision.pipeline_config in (pipeline_config(settings), legacy_pipeline_config(settings)))


def source_requires_reparse(source) -> bool:
    return (requires_pdf_reparse(source.source_path, source.content)
            or requires_markdown_reparse(source.source_path, source.parser_version))


def job_parser_version(job) -> str | None:
    if job.original_filename.lower().endswith(".md") and job.sections and all(
            isinstance(section, dict) and section.get("parser_version") == PARSER_VERSION for section in job.sections):
        return PARSER_VERSION
    return None


def job_requires_reparse(job) -> bool:
    return (requires_pdf_reparse(job.original_filename, job.content or "")
            or requires_markdown_reparse(job.original_filename, job_parser_version(job)))


def reviewed_job_required(job) -> None:
    if job_requires_reparse(job):
        raise HTTPException(409, "preview uses an older document structure; re-upload, parse and review it before publishing")
    if job.original_filename.lower().endswith(".md") and job.sections != markdown_sections(job.content):
        raise HTTPException(409, "Markdown preview does not match its source; prepare and review it again")


def revision_collection(base: str, revision_id: str) -> str:
    return f"{base}_{revision_id.replace('-', '')}"


def article_sections(content: str, source_path: str = "") -> list[dict[str, Any]]:
    if not source_path.lower().endswith(".pdf"):
        return markdown_sections(content)
    sections = []
    heading, lines = "正文", []
    for line in content.splitlines():
        match = re.match(r"^#{1,6}\s+(.+)$", line)
        if match:
            if lines:
                sections.append({"heading": heading, "content": "\n".join(lines).strip()})
            heading, lines = match.group(1).strip(), []
        else:
            lines.append(line)
    if lines:
        sections.append({"heading": heading, "content": "\n".join(lines).strip()})
    return [section for section in sections if section["content"]]


def source_sections(content: str, source_path: str, title: str, parser_version: str | None):
    if requires_markdown_reparse(source_path, parser_version):
        # A readable raw snapshot is not evidence that it used the new parser.
        return [{"heading": title, "content": content}]
    return article_sections(content, source_path)


class KnowledgeService:
    def __init__(self, session_factory, qdrant, settings):
        self.session_factory, self.qdrant, self.settings = session_factory, qdrant, settings

    async def active_revision_id(self) -> str | None:
        async with self.session_factory() as session:
            return await session.scalar(select(KnowledgeActivePointer.revision_id).where(KnowledgeActivePointer.id == 1))

    active_revision = active_revision_id

    async def enqueue_upload(self, user_id: str, filename: str, title: str, access_level: str, data: bytes) -> dict[str, Any]:
        extension = Path(filename).suffix.lower()
        if extension not in {".md", ".pdf"}:
            raise HTTPException(415, "only Markdown and PDF uploads are supported")
        if access_level not in ACCESS_HIERARCHY:
            raise HTTPException(422, "unknown access level")
        title = title.strip()
        if not title or len(title) > 300:
            raise HTTPException(422, "title must contain 1 to 300 characters")
        if not data or len(data) > self.settings.knowledge_max_upload_bytes:
            raise HTTPException(413, "knowledge upload size limit exceeded")
        if extension == ".pdf" and not data.startswith(b"%PDF-"):
            raise HTTPException(422, "file is not a PDF")
        content_hash = hashlib.sha256(data).hexdigest()
        storage = Path(self.settings.knowledge_storage_path).resolve()
        await asyncio.to_thread(storage.mkdir, parents=True, exist_ok=True)
        target = storage / f"{content_hash}{extension}"
        def save_immutable():
            try:
                with target.open("xb") as handle:
                    handle.write(data)
            except FileExistsError:
                if hashlib.sha256(target.read_bytes()).hexdigest() != content_hash:
                    raise RuntimeError("immutable knowledge file integrity failure")
        await asyncio.to_thread(save_immutable)
        async with self.session_factory.begin() as session:
            job = KnowledgeJob(id=str(uuid4()), user_id=user_id, title=title, source_path=str(target),
                content_hash=content_hash, original_filename=Path(filename).name[:300], access_level=access_level, status="queued")
            session.add(job)
            await session.flush()
            return record_dict(job, JOB_FIELDS)

    async def _claim_job(self, job_id: str | None = None) -> tuple[str, str, str, str] | None:
        async with self.session_factory.begin() as session:
            query = select(KnowledgeJob).where(or_(KnowledgeJob.status == "queued",
                (KnowledgeJob.status == "running") & (KnowledgeJob.lease_expires_at < now())))
            if job_id is not None:
                query = query.where(KnowledgeJob.id == job_id)
            job = await session.scalar(query.order_by(KnowledgeJob.created_at).with_for_update(skip_locked=True).limit(1))
            if job is None:
                return None
            job.status, job.lease_token = "running", str(uuid4())
            lease = max(getattr(self.settings, "lease_seconds", 30), self.settings.knowledge_parse_timeout_seconds + 10)
            job.lease_expires_at = now() + timedelta(seconds=lease)
            return job.id, job.lease_token, job.source_path, job.content_hash

    async def process_next_job(self) -> bool:
        index_job = await self._claim_index_job()
        if index_job is not None:
            if not index_job.get("rejected"):
                await self._process_index_claim(index_job)
            return True
        claimed = await self._claim_job()
        if claimed is None:
            return False
        await self._process_claim(claimed)
        return True

    async def process_job(self, job_id: str) -> bool:
        claimed = await self._claim_job(job_id)
        if claimed is None:
            return False
        await self._process_claim(claimed)
        return True

    async def _process_claim(self, claimed) -> None:
        job_id, lease_token, path, expected_hash = claimed
        error, content, parsed_sections = None, None, None
        process = None
        try:
            actual_hash = await asyncio.to_thread(lambda: hashlib.sha256(Path(path).read_bytes()).hexdigest())
            if actual_hash != expected_hash:
                raise ValueError("immutable upload integrity verification failed")
            process = await asyncio.create_subprocess_exec(sys.executable, "-m", "app.production.knowledge_parser", path,
                str(self.settings.knowledge_max_pdf_pages), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=self.settings.knowledge_parse_timeout_seconds)
            result = json.loads(stdout)
            if process.returncode != 0 or "error" in result:
                raise ValueError(result.get("error", "document extraction failed"))
            content = result["content"]
            parsed_sections = result.get("sections")
            # Validate chunkability before presenting the publish control.
            await asyncio.to_thread(chunk_markdown, content, source_path=path)
        except asyncio.CancelledError:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
            raise
        except asyncio.TimeoutError:
            if process and process.returncode is None:
                process.kill()
                await process.wait()
            error = "document extraction timed out"
        except Exception as caught:
            error = str(caught)[:1000] or "document extraction failed"
        async with self.session_factory.begin() as session:
            job = await session.scalar(select(KnowledgeJob).where(KnowledgeJob.id == job_id,
                KnowledgeJob.lease_token == lease_token, KnowledgeJob.status == "running").with_for_update())
            if job is not None:
                job.status = "failed" if error else "ready"
                job.error, job.content = error, content
                job.sections = (parsed_sections or article_sections(content, path)) if content else None
                job.finished_at = now()
                job.lease_token, job.lease_expires_at = None, None

    async def job_detail(self, job_id: str) -> dict[str, Any]:
        async with self.session_factory.begin() as session:
            job = await session.get(KnowledgeJob, job_id)
            if job is None:
                raise HTTPException(404, "knowledge job not found")
            if job.status == "ready" and job.previewed_at is None and not job_requires_reparse(job):
                job.previewed_at = now()
            return {**record_dict(job, JOB_FIELDS), "content": job.content, "sections": job.sections or [],
                    "previewed_at": job.previewed_at, "requires_reparse": job_requires_reparse(job)}

    @staticmethod
    def _index_job_dict(job: KnowledgeIndexJob) -> dict[str, Any]:
        return {**record_dict(job, ("id", "kind", "status", "result", "error", "created_at", "finished_at")), "index_job_id": job.id}

    async def index_job_detail(self, job_id: str) -> dict[str, Any]:
        async with self.session_factory() as session:
            job = await session.get(KnowledgeIndexJob, job_id)
            if job is None:
                raise HTTPException(404, "knowledge index job not found")
            return self._index_job_dict(job)

    async def _enqueue_index_job(self, session, actor_id, kind, target_id, version):
        key = f"{kind}:{target_id}:{version}"
        await session.execute(insert(KnowledgeIndexJob).values(id=str(uuid4()), request_key=key, actor_id=actor_id,
            kind=kind, target_id=target_id, target_version=version, status="queued").on_conflict_do_nothing(index_elements=[KnowledgeIndexJob.request_key]))
        job = await session.scalar(select(KnowledgeIndexJob).where(KnowledgeIndexJob.request_key == key).with_for_update())
        if job.status == "failed":
            # An explicit administrator POST retries the same durable operation.
            job.status, job.error, job.finished_at = "queued", None, None
            job.lease_token, job.lease_expires_at, job.deadline_at = None, None, None
        return job

    async def enqueue_publish(self, actor_id: str, job_id: str) -> dict[str, Any]:
        async with self.session_factory.begin() as session:
            upload = await session.get(KnowledgeJob, job_id)
            if upload is None:
                raise HTTPException(404, "knowledge job not found")
            if upload.index_job_id:
                existing = await session.get(KnowledgeIndexJob, upload.index_job_id)
                if existing and existing.status != "failed":
                    return self._index_job_dict(existing)
            upload = await session.scalar(select(KnowledgeJob).where(KnowledgeJob.id == job_id).with_for_update().execution_options(populate_existing=True))
            if upload.status != "ready" or upload.previewed_at is None or not upload.content:
                raise HTTPException(409, "job must be ready and previewed before publishing")
            reviewed_job_required(upload)
            index_job = await self._enqueue_index_job(session, actor_id, "publish", upload.id, 1)
            upload.index_job_id = index_job.id
            return self._index_job_dict(index_job)

    async def enqueue_deactivate(self, actor_id: str, document_id: str) -> dict[str, Any]:
        async with self.session_factory.begin() as session:
            source = await session.get(KnowledgeSource, document_id)
            if source is None:
                raise HTTPException(404, "knowledge document not found")
            if source.index_job_id:
                existing = await session.get(KnowledgeIndexJob, source.index_job_id)
                if existing and (existing.status in {"queued", "running"} or source.status == "inactive"):
                    return self._index_job_dict(existing)
            source = await session.scalar(select(KnowledgeSource).where(KnowledgeSource.id == document_id).with_for_update().execution_options(populate_existing=True))
            index_job = await self._enqueue_index_job(session, actor_id, "deactivate", source.id, source.version)
            source.index_job_id = index_job.id
            return self._index_job_dict(index_job)

    async def _claim_index_job(self):
        async with self.session_factory.begin() as session:
            job = await session.scalar(select(KnowledgeIndexJob).where(or_(KnowledgeIndexJob.status == "queued",
                (KnowledgeIndexJob.status == "running") & (KnowledgeIndexJob.lease_expires_at < now())))
                .order_by(KnowledgeIndexJob.created_at).with_for_update(skip_locked=True).limit(1))
            if job is None:
                return None
            try:
                await self._validate_index_actor(session, job.actor_id)
            except ActorAccessChanged:
                job.status, job.error, job.finished_at = "failed", "actor_access_changed", now()
                job.lease_token, job.lease_expires_at = None, None
                return {"rejected": True}
            job.status, job.lease_token = "running", str(uuid4())
            job.lease_expires_at = now() + timedelta(seconds=getattr(self.settings, "knowledge_index_lease_seconds", 30))
            job.deadline_at = now() + timedelta(seconds=getattr(self.settings, "knowledge_index_timeout_seconds", 600))
            return {"id": job.id, "lease_token": job.lease_token, "kind": job.kind,
                    "target_id": job.target_id, "target_version": job.target_version, "actor_id": job.actor_id}

    async def _index_heartbeat(self, claimed):
        delay = max(0.05, getattr(self.settings, "knowledge_index_lease_seconds", 30) / 3)
        while True:
            async with self.session_factory.begin() as session:
                statement = update(KnowledgeIndexJob).where(KnowledgeIndexJob.id == claimed["id"],
                    KnowledgeIndexJob.status == "running", KnowledgeIndexJob.lease_token == claimed["lease_token"],
                    KnowledgeIndexJob.lease_expires_at > now(), KnowledgeIndexJob.deadline_at > now()).values(
                    lease_expires_at=now() + timedelta(seconds=getattr(self.settings, "knowledge_index_lease_seconds", 30))).returning(KnowledgeIndexJob.id)
                if await session.scalar(statement) is None:
                    raise PermissionError("knowledge index lease was lost")
            await asyncio.sleep(delay)

    async def _process_index_claim(self, claimed):
        execute = self.execute_publish if claimed["kind"] == "publish" else self.execute_deactivate
        processing = asyncio.create_task(execute(claimed["actor_id"], claimed["target_id"],
            index_job_id=claimed["id"], lease_token=claimed["lease_token"], expected_version=claimed["target_version"]))
        heartbeat = asyncio.create_task(self._index_heartbeat(claimed))
        error = None
        try:
            async with asyncio.timeout(getattr(self.settings, "knowledge_index_timeout_seconds", 600)):
                done, _ = await asyncio.wait({processing, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
                if processing in done:
                    await processing
                else:
                    await heartbeat
                    await processing
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            error = "index_timeout"
        except ActorAccessChanged:
            error = "actor_access_changed"
        except PermissionError:
            error = "index_lease_lost"
        except HTTPException:
            error = "index_conflict"
        except EmbeddingInputTooLong:
            error = "embedding_input_too_long"
        except Exception:
            error = "index_build_failed"
        finally:
            processing.cancel()
            heartbeat.cancel()
            await asyncio.gather(processing, heartbeat, return_exceptions=True)
        if error:
            async with self.session_factory.begin() as session:
                job = await session.scalar(select(KnowledgeIndexJob).where(KnowledgeIndexJob.id == claimed["id"],
                    KnowledgeIndexJob.status == "running", KnowledgeIndexJob.lease_token == claimed["lease_token"]).with_for_update())
                if job:
                    job.status, job.error, job.finished_at = "failed", error, now()
                    job.lease_token, job.lease_expires_at = None, None

    async def _index_commit(self, session, index_job_id, lease_token, result):
        if index_job_id is None:
            return
        job = await session.scalar(select(KnowledgeIndexJob).where(KnowledgeIndexJob.id == index_job_id).with_for_update().execution_options(populate_existing=True))
        if (job is None or job.status != "running" or job.lease_token != lease_token
            or job.lease_expires_at is None or job.lease_expires_at <= now()
            or job.deadline_at is None or job.deadline_at <= now()):
            raise PermissionError("knowledge index lease is no longer active")
        job.status, job.result, job.finished_at = "completed", result, now()
        job.lease_token, job.lease_expires_at = None, None

    @staticmethod
    async def _validate_index_actor(session, actor_id, *, lock=False):
        from app.db.models import User
        from app.production.identity_models import IdentityAccount
        account_query = select(IdentityAccount).where(IdentityAccount.user_id == actor_id)
        user_query = select(User).where(User.id == actor_id)
        # Match the identity mutation's account -> user lock order. SHARE locks fence
        # non-key role/enabled edits until the short pointer commit has completed.
        account = await session.scalar(account_query.with_for_update(read=True) if lock else account_query)
        user = await session.scalar(user_query.with_for_update(read=True) if lock else user_query)
        if account is None or not account.enabled or user is None or user.access_level != "admin":
            raise ActorAccessChanged("knowledge index actor access changed")

    async def _pointer_lock(self, session):
        await session.execute(insert(KnowledgeActivePointer).values(id=1, revision_id=None).on_conflict_do_nothing(index_elements=[KnowledgeActivePointer.id]))
        return await session.scalar(select(KnowledgeActivePointer).where(KnowledgeActivePointer.id == 1).with_for_update())

    @asynccontextmanager
    async def _revision_transaction(self):
        staged = []
        try:
            async with self.session_factory.begin() as session:
                yield session, staged
        except BaseException:
            for collection in staged:
                try:
                    await self.qdrant.delete_collection(collection_name=collection)
                except Exception:
                    pass  # Durable index-job manifest permits later orphan collection collection.
            raise

    async def _register_staging(self, index_job_id, lease_token, collection):
        if index_job_id is None:
            return
        async with self.session_factory.begin() as session:
            job = await session.scalar(select(KnowledgeIndexJob).where(KnowledgeIndexJob.id == index_job_id).with_for_update())
            if (job is None or job.status != "running" or job.lease_token != lease_token
                or job.lease_expires_at <= now() or job.deadline_at <= now()):
                raise PermissionError("knowledge index lease is no longer active")
            job.staging_collections = [*job.staging_collections, collection]

    async def _create_revision(self, session, actor_id: str, sources: list[KnowledgeSource], staged=None, index_job_id=None, lease_token=None) -> KnowledgeRevision:
        # Retain legacy sources and old revisions; only evidence with page
        # provenance enters this new snapshot. Several PDFs can be repaired
        # independently, without publication/deactivation deadlocks.
        sources = [source for source in sources if not source_requires_reparse(source)]
        revision_id = str(uuid4())
        collection = revision_collection(self.settings.qdrant_collection, revision_id)
        if staged is not None:
            staged.append(collection)
        await self._register_staging(index_job_id, lease_token, collection)
        embedder = await asyncio.to_thread(build_embedder, self.settings)
        if embedder.dimensions != expected_dimensions(self.settings):
            raise ValueError("embedding dimensions do not match configured model identity")
        model_name, model_revision = embedding_identity(self.settings)
        revision = KnowledgeRevision(id=revision_id, collection_name=collection, status="ready", document_count=len(sources),
            chunk_count=0, embedding_model=model_name, embedding_revision=model_revision,
            dimensions=embedder.dimensions, pipeline_config=pipeline_config(self.settings), created_by=actor_id)
        session.add(revision)
        await session.flush()
        chunks = []
        for source in sources:
            session.add(KnowledgeSnapshot(revision_id=revision_id, document_id=source.id, title=source.title,
                source_path=source.source_path, content=source.content, content_hash=source.content_hash,
                parser_version=source.parser_version, access_level=source.access_level))
            prepared = await asyncio.to_thread(chunk_markdown, source.content, source_path=source.source_path,
                source_title=source.title, access_level=source.access_level, version=source.content_hash)
            for chunk in prepared:
                stored = RevisionChunk(id=str(uuid4()), revision_id=revision_id, document_id=source.id,
                    chunk_index=chunk.chunk_index, content=chunk.content, source_title=source.title,
                    source_path=source.source_path, char_start=chunk.char_start, char_end=chunk.char_end,
                    access_level=source.access_level, structure=chunk.structure)
                session.add(stored)
                chunks.append(stored)
        revision.chunk_count = len(chunks)
        batch_size = getattr(self.settings, "embedding_batch_size", 16)
        if hasattr(embedder, "token_counts"):
            # Validate all complete inputs before creating any vector collection.
            # encode() validates again for other callers and query inputs.
            for start in range(0, len(chunks), batch_size):
                batch = chunks[start:start + batch_size]
                counts = await embedder.token_counts([chunk_search_text(chunk) for chunk in batch])
                if len(counts) != len(batch):
                    raise ValueError("embedding tokenizer returned an unexpected input count")
                for chunk, count in zip(batch, counts, strict=True):
                    if chunk.structure is not None:
                        chunk.structure = {**chunk.structure, "embedding_token_count": count}
        await session.flush()
        try:
            await self.qdrant.create_collection(collection_name=collection,
                vectors_config=models.VectorParams(size=embedder.dimensions, distance=models.Distance.COSINE))
            for start in range(0, len(chunks), batch_size):
                batch = chunks[start:start + batch_size]
                vectors = await embedder.encode([chunk_search_text(chunk) for chunk in batch])
                await self.qdrant.upsert(collection_name=collection, wait=True, points=[models.PointStruct(id=chunk.id, vector=vector,
                    payload={"document_id": chunk.document_id, "access_level": chunk.access_level,
                             "revision_id": revision_id, "structure": chunk.structure})
                    for chunk, vector in zip(batch, vectors, strict=True)])
            count = await self.qdrant.count(collection_name=collection, exact=True)
            if count.count != len(chunks):
                raise RuntimeError("knowledge vector snapshot count mismatch")
        except BaseException:
            try:
                await self.qdrant.delete_collection(collection_name=collection)
            except Exception:
                pass
            raise
        return revision

    async def execute_publish(self, actor_id: str, job_id: str, *, index_job_id=None, lease_token=None, expected_version=None) -> dict[str, Any]:
        async with self._revision_transaction() as (session, staged):
            pointer = await self._pointer_lock(session)
            job = await session.scalar(select(KnowledgeJob).where(KnowledgeJob.id == job_id).with_for_update())
            if job is None:
                raise HTTPException(404, "knowledge job not found")
            if job.status != "ready" or not job.content or job.previewed_at is None:
                raise HTTPException(409, "job must be ready and previewed before publishing")
            reviewed_job_required(job)
            if requires_pdf_reparse(job.original_filename, job.content):
                raise HTTPException(409, "PDF preview uses legacy flattened text; re-upload and review the new parse")
            # Stable filenames identify articles; immutable snapshots retain earlier content.
            source = await session.scalar(select(KnowledgeSource).where(KnowledgeSource.source_path == job.original_filename)
                .order_by(KnowledgeSource.created_at.desc(), KnowledgeSource.id).with_for_update().limit(1))
            if source is None:
                source = KnowledgeSource(id=str(uuid4()), title=job.title, source_path=job.original_filename,
                    content_hash=job.content_hash, content=job.content, parser_version=job_parser_version(job),
                    access_level=job.access_level, status="active", version=1)
                session.add(source)
            else:
                source.title, source.content_hash, source.content = job.title, job.content_hash, job.content
                source.parser_version = job_parser_version(job)
                source.access_level, source.status, source.version = job.access_level, "active", source.version + 1
                await session.execute(update(KnowledgeSource).where(KnowledgeSource.source_path == job.original_filename,
                    KnowledgeSource.id != source.id, KnowledgeSource.status == "active").values(status="inactive", version=KnowledgeSource.version + 1))
            await session.flush()
            sources = list(await session.scalars(select(KnowledgeSource).where(KnowledgeSource.status == "active").order_by(KnowledgeSource.id)))
            revision = await self._create_revision(session, actor_id, sources, staged, index_job_id, lease_token)
            pointer.revision_id = revision.id
            job.status, job.document_id = "published", source.id
            session.add(BusinessAudit(entity_type="knowledge", entity_id=source.id, actor_id=actor_id,
                event_type="published", details={"job_id": job.id, "revision_id": revision.id}))
            result = {"revision_id": revision.id, "status": "active"}
            excluded = [item.source_path for item in sources if source_requires_reparse(item)]
            if excluded:
                result["excluded_documents"] = excluded
            await self._validate_index_actor(session, actor_id, lock=True)
            await self._index_commit(session, index_job_id, lease_token, result)
            return result

    async def execute_deactivate(self, actor_id: str, document_id: str, *, index_job_id=None, lease_token=None, expected_version=None) -> dict[str, Any]:
        async with self._revision_transaction() as (session, staged):
            pointer = await self._pointer_lock(session)
            source = await session.scalar(select(KnowledgeSource).where(KnowledgeSource.id == document_id).with_for_update())
            if source is None:
                raise HTTPException(404, "knowledge document not found")
            if expected_version is not None and source.version != expected_version:
                raise HTTPException(409, "knowledge document version changed")
            source.status = "inactive"
            source.version += 1
            await session.flush()
            sources = list(await session.scalars(select(KnowledgeSource).where(KnowledgeSource.status == "active").order_by(KnowledgeSource.id)))
            revision = await self._create_revision(session, actor_id, sources, staged, index_job_id, lease_token)
            pointer.revision_id = revision.id
            session.add(BusinessAudit(entity_type="knowledge", entity_id=source.id, actor_id=actor_id,
                event_type="deactivated", details={"revision_id": revision.id}))
            result = {"revision_id": revision.id, "status": "active"}
            excluded = [item.source_path for item in sources if source_requires_reparse(item)]
            if excluded:
                result["excluded_documents"] = excluded
            await self._validate_index_actor(session, actor_id, lock=True)
            await self._index_commit(session, index_job_id, lease_token, result)
            return result

    async def activate_revision(self, actor_id: str, revision_id: str) -> dict[str, Any]:
        async with self.session_factory.begin() as session:
            pointer = await self._pointer_lock(session)
            revision = await session.get(KnowledgeRevision, revision_id)
            if revision is None or revision.status != "ready":
                raise HTTPException(404, "ready knowledge revision not found")
            if not revision_matches_settings(revision, self.settings):
                raise HTTPException(409, "revision uses another embedding model")
            count = await self.qdrant.count(collection_name=revision.collection_name, exact=True)
            if count.count != revision.chunk_count:
                raise HTTPException(409, "revision vector snapshot is incomplete")
            document_ids = set(await session.scalars(select(KnowledgeSnapshot.document_id).where(KnowledgeSnapshot.revision_id == revision_id)))
            sources = await session.scalars(select(KnowledgeSource).with_for_update())
            for source in sources:
                desired = "active" if source.id in document_ids else "inactive"
                if desired != source.status:
                    source.status, source.version = desired, source.version + 1
            pointer.revision_id = revision_id
            await self._validate_index_actor(session, actor_id, lock=True)
            session.add(BusinessAudit(entity_type="knowledge_revision", entity_id=revision_id, actor_id=actor_id,
                event_type="activated", details={"document_count": revision.document_count}))
            return {"revision_id": revision_id, "status": "active"}

    async def readiness(self) -> dict[str, Any]:
        async with self.session_factory() as session:
            revision_id = await session.scalar(select(KnowledgeActivePointer.revision_id).where(KnowledgeActivePointer.id == 1))
            if revision_id is None:
                return {"ready": True, "revision_id": None, "document_count": 0}
            revision = await session.get(KnowledgeRevision, revision_id)
            if revision is None:
                return {"ready": False, "revision_id": revision_id, "reason": "missing_revision"}
            model_matches = revision_matches_settings(revision, self.settings)
            count = await self.qdrant.count(collection_name=revision.collection_name, exact=True)
            collection = await self.qdrant.get_collection(collection_name=revision.collection_name)
            vectors = collection.config.params.vectors
            database_count = await session.scalar(select(func.count()).select_from(RevisionChunk).where(RevisionChunk.revision_id == revision_id))
            vectors_match = getattr(vectors, "size", None) == revision.dimensions and getattr(vectors, "distance", None) == models.Distance.COSINE
            return {"ready": model_matches and vectors_match and count.count == revision.chunk_count == database_count, "revision_id": revision_id,
                    "document_count": revision.document_count, "chunk_count": revision.chunk_count}


# Immutable BM25 indexes are shared by all per-run retrievers; bounded to 32 revision/access combinations.
_lexical_cache: OrderedDict = OrderedDict()
_cache_locks: dict[tuple, asyncio.Lock] = {}


class RevisionRetriever:
    def __init__(self, session_factory, qdrant, settings, revision):
        self.session_factory, self.qdrant, self.settings, self.revision = session_factory, qdrant, settings, revision

    def _search_text(self, chunk):
        if self.revision.pipeline_config["version"] == 1:
            return search_text(chunk.source_title, chunk.content)
        if chunk.source_path.lower().endswith(".md") and (
                not isinstance(chunk.structure, dict) or chunk.structure.get("parser_version") != PARSER_VERSION):
            raise ValueError("Markdown revision chunk is missing its source structure")
        return chunk_search_text(chunk)

    async def _lexical_index(self, allowed):
        key = (id(self.session_factory), self.revision.id, tuple(allowed))
        if key in _lexical_cache:
            _lexical_cache.move_to_end(key)
            return _lexical_cache[key]
        lock = _cache_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key not in _lexical_cache:
                async with self.session_factory() as session:
                    chunks = list(await session.scalars(select(RevisionChunk).where(RevisionChunk.revision_id == self.revision.id,
                        RevisionChunk.access_level.in_(allowed)).order_by(RevisionChunk.source_path, RevisionChunk.chunk_index)))
                def prepare():
                    texts = [self._search_text(chunk) for chunk in chunks]
                    corpus = [tokenize(content) or ["_empty_"] for content in texts]
                    return chunks, BM25Okapi(corpus) if corpus else None
                _lexical_cache[key] = await asyncio.to_thread(prepare)
                while len(_lexical_cache) > 32:
                    expired, _ = _lexical_cache.popitem(last=False)
                    _cache_locks.pop(expired, None)
            return _lexical_cache[key]

    async def _accessible_documents(self, allowed):
        async with self.session_factory() as session:
            return set(await session.scalars(select(KnowledgeSource.id).where(KnowledgeSource.status == "active",
                KnowledgeSource.access_level.in_(allowed))))

    async def retrieve(self, query: str, *, user_access_level: str, limit: int = 5) -> list[RetrievalHit]:
        if not query.strip() or limit <= 0 or self.revision is None:
            return []
        if user_access_level not in ACCESS_HIERARCHY:
            raise ValueError("unknown access level")
        allowed = ACCESS_HIERARCHY[user_access_level]
        chunks, bm25 = await self._lexical_index(allowed)
        accessible = await self._accessible_documents(allowed)
        by_id = {chunk.id: chunk for chunk in chunks if chunk.document_id in accessible}
        if not by_id:
            return []
        embedder = await asyncio.to_thread(build_embedder, self.settings)
        query_vector = (await embedder.encode_query(query)) if hasattr(embedder, "encode_query") else (await embedder.encode([query]))[0]
        result = await self.qdrant.query_points(collection_name=self.revision.collection_name, query=query_vector,
            query_filter=models.Filter(must=[models.FieldCondition(key="access_level", match=models.MatchAny(any=list(allowed)))]),
            limit=40, with_payload=False)
        vector_scores = {str(point.id): float(point.score) for point in result.points if str(point.id) in by_id}
        def score_bm25():
            scores = bm25.get_scores(tokenize(query))
            return dict(sorted([(chunk.id, float(score)) for chunk, score in zip(chunks, scores, strict=True)
                if chunk.id in by_id], key=lambda item: item[1], reverse=True)[:40])
        lexical_scores = await asyncio.to_thread(score_bm25)
        vectors, lexical = _min_max(vector_scores), _min_max(lexical_scores)
        combined = {key: 0.65 * vectors.get(key, 0) + 0.35 * lexical.get(key, 0) for key in set(vectors) | set(lexical)}
        ids = sorted(combined, key=combined.get, reverse=True)
        scores = await LexicalReranker().score(query, [self._search_text(by_id[key]) for key in ids])
        ranked = sorted(zip(ids, scores, strict=True), key=lambda item: (item[1], combined[item[0]]), reverse=True)
        # Recheck current authorization after embedding/vector calls, including pinned older run revisions.
        accessible = await self._accessible_documents(allowed)
        selected, document_counts = [], {}
        for key, score in ranked:
            chunk = by_id[key]
            if chunk.document_id not in accessible or document_counts.get(chunk.document_id, 0) >= 2:
                continue
            document_counts[chunk.document_id] = document_counts.get(chunk.document_id, 0) + 1
            selected.append(_to_hit(chunk, query, score, vector_scores.get(key, 0), lexical_scores.get(key, 0), combined[key]))
            if len(selected) >= min(limit, 5):
                break
        return selected


async def build_retriever(session_factory, qdrant, settings, index_revision=None):
    async with session_factory() as session:
        revision_id = index_revision
        if revision_id is None:
            revision_id = await session.scalar(select(KnowledgeActivePointer.revision_id).where(KnowledgeActivePointer.id == 1))
        revision = await session.get(KnowledgeRevision, revision_id) if revision_id else None
        if revision_id and (revision is None or revision.status != "ready"):
            raise ValueError("knowledge revision is not ready")
        if revision and not revision_matches_settings(revision, settings):
            raise ValueError("knowledge revision embedding model differs from configured model")
    return RevisionRetriever(session_factory, qdrant, settings, revision)


def service(request: Request) -> KnowledgeService:
    return request.app.state.knowledge_service


@router.post("/uploads", status_code=202)
async def upload(request: Request, file: UploadFile = File(...), access_level: str = Form(...), title: str = Form(...), principal: Principal = Depends(require_admin)):
    maximum = service(request).settings.knowledge_max_upload_bytes
    data = await file.read(maximum + 1)
    return await service(request).enqueue_upload(principal.user_id, file.filename or "", title, access_level, data)


@router.get("/jobs")
async def jobs(request: Request, cursor: str | None = None, limit: int = 50, principal: Principal = Depends(require_admin)):
    async with service(request).session_factory() as session:
        query = select(KnowledgeJob)
        if cursor:
            query = query.where(KnowledgeJob.id > cursor)
        limit = max(1, min(limit, 100))
        rows = list(await session.scalars(query.order_by(KnowledgeJob.id).limit(limit + 1)))
        return {"jobs": [record_dict(row, JOB_FIELDS) for row in rows[:limit]], "next_cursor": rows[limit - 1].id if len(rows) > limit else None}


@router.get("/jobs/{job_id}")
async def job_detail(job_id: str, request: Request, principal: Principal = Depends(require_admin)):
    return await service(request).job_detail(job_id)


@router.post("/jobs/{job_id}/publish", status_code=202)
async def publish(job_id: str, request: Request, principal: Principal = Depends(require_admin)):
    return await service(request).enqueue_publish(principal.user_id, job_id)


@router.get("/documents")
async def documents(request: Request, cursor: str | None = None, limit: int = 50, principal: Principal = Depends(require_principal)):
    async with service(request).session_factory() as session:
        query = select(KnowledgeSource).where(KnowledgeSource.access_level.in_(ACCESS_HIERARCHY[principal.role]))
        if principal.role != "admin":
            query = query.where(KnowledgeSource.status == "active")
        if cursor:
            query = query.where(KnowledgeSource.id > cursor)
        limit = max(1, min(limit, 100))
        rows = list(await session.scalars(query.order_by(KnowledgeSource.id).limit(limit + 1)))
        return {"documents": [{**record_dict(row, SOURCE_FIELDS),
            "requires_reparse": source_requires_reparse(row),
            "reparse_reason": "markdown_structure" if requires_markdown_reparse(row.source_path, row.parser_version)
                else "pdf_provenance" if requires_pdf_reparse(row.source_path, row.content) else None} for row in rows[:limit]],
            "next_cursor": rows[limit - 1].id if len(rows) > limit else None}


@router.get("/documents/{document_id}")
async def document_detail(document_id: str, request: Request, principal: Principal = Depends(require_principal), index_revision: str | None = None):
    async with service(request).session_factory() as session:
        source = await session.get(KnowledgeSource, document_id)
        if source is None or source.access_level not in ACCESS_HIERARCHY[principal.role] or (source.status != "active" and principal.role != "admin"):
            raise HTTPException(404, "knowledge document not found")
        if index_revision is not None:
            snapshot = await session.scalar(select(KnowledgeSnapshot).where(
                KnowledgeSnapshot.document_id == document_id,
                KnowledgeSnapshot.revision_id == index_revision,
                KnowledgeSnapshot.access_level.in_(ACCESS_HIERARCHY[principal.role])))
            if snapshot is None:
                raise HTTPException(404, "knowledge document not found")
            return {**record_dict(source, SOURCE_FIELDS), "title": snapshot.title,
                    "version": snapshot.content_hash, "index_revision": index_revision,
                    "content": snapshot.content, "parser_version": snapshot.parser_version,
                    "sections": source_sections(snapshot.content, snapshot.source_path, snapshot.title, snapshot.parser_version)}
        return {**record_dict(source, SOURCE_FIELDS), "requires_reparse": source_requires_reparse(source),
                "content": source.content, "parser_version": source.parser_version,
                "sections": source_sections(source.content, source.source_path, source.title, source.parser_version)}


@router.post("/documents/{document_id}/deactivate", status_code=202)
async def deactivate(document_id: str, request: Request, principal: Principal = Depends(require_admin)):
    return await service(request).enqueue_deactivate(principal.user_id, document_id)


@router.get("/revisions")
async def revisions(request: Request, principal: Principal = Depends(require_admin)):
    async with service(request).session_factory() as session:
        active = await session.scalar(select(KnowledgeActivePointer.revision_id).where(KnowledgeActivePointer.id == 1))
        rows = await session.scalars(select(KnowledgeRevision).order_by(KnowledgeRevision.created_at.desc()).limit(100))
        return {"revisions": [{**record_dict(row, ("id", "created_at", "document_count", "chunk_count")), "active": row.id == active} for row in rows]}


@router.post("/revisions/{revision_id}/activate")
async def activate(revision_id: str, request: Request, principal: Principal = Depends(require_admin)):
    return await service(request).activate_revision(principal.user_id, revision_id)


@admin_router.get("/index-jobs/{job_id}")
async def index_job_detail(job_id: str, request: Request, principal: Principal = Depends(require_admin)):
    return await service(request).index_job_detail(job_id)
