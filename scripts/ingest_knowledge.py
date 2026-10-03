"""Prepare production uploads; opt-in demo import never deletes by default."""
import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(os.getenv("KNOWLEDGE_DIR", PROJECT_ROOT / "data" / "knowledge")))
    parser.add_argument("--actor-id", help="Enabled production administrator; required unless --demo.")
    parser.add_argument("--access-level", choices=("employee", "support", "admin"), help="Production override; otherwise honor Markdown frontmatter, falling back to employee.")
    parser.add_argument("--demo", action="store_true", help="Use the legacy demo collection only in development/test.")
    parser.add_argument("--dry-run", action="store_true", help="Show the exact plan without changing files, DB or vectors.")
    parser.add_argument("--sync-delete", action="store_true", help="Demo only: delete sources absent from this mounted directory.")
    parser.add_argument("--plan-file", type=Path, help="Save/review a demo deletion plan, then use it for confirmation.")
    parser.add_argument("--confirm-plan", help="SHA256 of a reviewed --plan-file; required to apply demo deletions.")
    return parser


def corpus_files(directory):
    directory = directory.resolve()
    if not directory.is_dir():
        raise ValueError(f"knowledge directory does not exist: {directory}")
    paths = sorted(path for path in directory.iterdir() if path.is_file())
    if not paths:
        raise ValueError("knowledge directory is empty; verify the mounted corpus")
    if any(path.is_symlink() or path.suffix.lower() not in {".md", ".pdf"} for path in paths):
        raise ValueError("corpus must contain regular Markdown/PDF files only")
    for path in paths:
        if path.stat().st_size == 0:
            raise ValueError(f"knowledge file is empty: {path.name}")
    return paths


def build_plan(paths, collection, existing_sources):
    names = {path.name for path in paths}
    plan = {"schema_version": 1, "collection": collection, "directory": str(paths[0].parent.resolve()),
        "files": [{"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths],
        "deletions": sorted((dict(item) for item in existing_sources if item["source_path"] not in names), key=lambda item: item["id"])}
    plan["sha256"] = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return plan


def file_access_level(path, data, override):
    from app.rag.ingest import parse_frontmatter
    metadata = parse_frontmatter(data.decode("utf-8")) if path.suffix.lower() == ".md" else {}
    level = override or metadata.get("access_level", "employee")
    if level not in {"employee", "support", "admin"}:
        raise ValueError(f"unknown knowledge access level: {path.name}")
    return level


async def run_demo(args, settings, ingestor, *, existing_sources, delete_planned=None):
    if settings.environment in {"production", "staging"} or not settings.demo_enabled:
        raise ValueError("legacy import and directory synchronization are forbidden in formal production/staging")
    paths = corpus_files(args.directory)
    plan = build_plan(paths, settings.qdrant_collection, existing_sources if args.sync_delete else [])
    if args.dry_run:
        if args.plan_file:
            args.plan_file.parent.mkdir(parents=True, exist_ok=True)
            args.plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(plan, ensure_ascii=False))
        return plan
    if args.sync_delete:
        if not args.plan_file or not args.confirm_plan:
            raise ValueError("demo deletion requires a reviewed --plan-file and explicit --confirm-plan SHA256")
        saved = json.loads(args.plan_file.read_text(encoding="utf-8"))
        if saved != plan or args.confirm_plan != plan["sha256"]:
            raise ValueError("corpus or indexed sources changed; plan does not match, run a new dry-run")
        delete_planned = delete_planned or getattr(ingestor, "delete_planned", None)
        if delete_planned is None:
            raise ValueError("exact planned deletion executor is required")
    summary = await ingestor.ingest_paths(paths)
    removed = await delete_planned(plan["deletions"]) if args.sync_delete else 0
    print(json.dumps({"documents": summary.document_count, "chunks": summary.chunk_count,
        "removed_documents": removed, "collection": settings.qdrant_collection}))
    return summary


async def existing_demo_sources(factory, collection):
    from sqlalchemy import select
    from app.db.models import KnowledgeDocument, KnowledgeChunk
    async with factory() as session:
        rows = await session.execute(select(KnowledgeDocument.id, KnowledgeDocument.source_path,
            KnowledgeDocument.version, KnowledgeDocument.status).join(KnowledgeChunk,
            KnowledgeChunk.document_id == KnowledgeDocument.id).where(
                KnowledgeChunk.chunk_metadata["collection_name"].astext == collection).distinct())
        return [dict(row._mapping) for row in rows]


async def remove_planned_sources(factory, qdrant, collection, deletions):
    """Delete only reviewed document IDs, including when other imports race."""
    from qdrant_client import models
    from sqlalchemy import delete, select
    from app.db.models import KnowledgeDocument, KnowledgeChunk
    removed = 0
    for expected in deletions:
        async with factory.begin() as session:
            document = await session.scalar(select(KnowledgeDocument).where(KnowledgeDocument.id == expected["id"]).with_for_update())
            if document is None or any(getattr(document, field) != expected[field] for field in ("source_path", "version", "status")):
                raise ValueError("indexed sources changed after review; run a new dry-run")
            removed += document.status == "active"
            document.status = "inactive"
        await qdrant.delete(collection_name=collection, points_selector=models.Filter(must=[
            models.FieldCondition(key="document_id", match=models.MatchValue(value=expected["id"]))]), wait=True)
        async with factory.begin() as session:
            await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == expected["id"],
                KnowledgeChunk.chunk_metadata["collection_name"].astext == collection))
    return removed


async def prepare_production(args, settings, factory, service):
    from sqlalchemy import select
    from app.production.knowledge_models import KnowledgeSource, KnowledgeJob
    from app.production.knowledge import source_requires_reparse, job_requires_reparse
    if args.sync_delete or args.confirm_plan:
        raise ValueError("directory synchronization/deletion is forbidden for production knowledge")
    if not args.actor_id:
        raise ValueError("production import requires --actor-id of an enabled administrator")
    paths = corpus_files(args.directory)
    async with factory.begin() as session:
        await service._validate_index_actor(session, args.actor_id, lock=True)
    prepared = []
    for path in paths:
        if path.stat().st_size > settings.knowledge_max_upload_bytes:
            raise ValueError(f"knowledge file exceeds upload size limit: {path.name}")
        data = path.read_bytes()
        access_level = file_access_level(path, data, args.access_level)
        digest = hashlib.sha256(data).hexdigest()
        async with factory() as session:
            source = await session.scalar(select(KnowledgeSource).where(KnowledgeSource.source_path == path.name,
                KnowledgeSource.content_hash == digest, KnowledgeSource.status == "active",
                KnowledgeSource.access_level == access_level))
            pending = await session.scalar(select(KnowledgeJob).where(KnowledgeJob.original_filename == path.name,
                KnowledgeJob.content_hash == digest, KnowledgeJob.access_level == access_level,
                KnowledgeJob.status.in_(("queued", "running", "ready"))).order_by(KnowledgeJob.created_at.desc()))
        if source and not source_requires_reparse(source):
            prepared.append({"filename": path.name, "status": "unchanged", "document_id": source.id})
        elif pending and (pending.status != "ready" or not job_requires_reparse(pending)):
            prepared.append({"filename": path.name, "status": pending.status, "job_id": pending.id})
        elif args.dry_run:
            prepared.append({"filename": path.name, "status": "would_enqueue", "sha256": digest})
        else:
            async with factory.begin() as session:
                await service._validate_index_actor(session, args.actor_id, lock=True)
            job = await service.enqueue_upload(args.actor_id, path.name, path.stem, access_level, data)
            prepared.append({"filename": path.name, "status": job["status"], "job_id": job["id"]})
        prepared[-1]["access_level"] = access_level
    print(json.dumps({"mode": "production_prepare", "jobs": prepared,
        "next_step": "Worker parses uploads. Review ready-job content in the admin UI before enqueueing publication."}, ensure_ascii=False))
    return prepared


async def run(args):
    # Lazy imports keep --help free of settings, DB and model startup.
    from app.core.config import get_settings
    from app.production.knowledge import KnowledgeService, build_embedder
    from qdrant_client import AsyncQdrantClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    settings = get_settings()
    if args.demo and (settings.environment in {"production", "staging"} or not settings.demo_enabled):
        raise ValueError("legacy demo import is forbidden in formal production/staging")
    if not args.demo and (args.sync_delete or args.confirm_plan):
        raise ValueError("directory synchronization/deletion is forbidden for production knowledge")
    corpus_files(args.directory)
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    qdrant = AsyncQdrantClient(url=settings.qdrant_url, check_compatibility=False,
        api_key=settings.qdrant_api_key.get_secret_value() if settings.qdrant_api_key else None)
    try:
        if args.demo:
            from app.rag.ingest import KnowledgeIngestor
            existing = await existing_demo_sources(factory, settings.qdrant_collection)
            # A dry-run reads metadata only and does not load semantic weights.
            ingestor = None if args.dry_run else KnowledgeIngestor(factory, qdrant,
                await asyncio.to_thread(build_embedder, settings), collection_name=settings.qdrant_collection)
            async def delete_planned(deletions):
                return await remove_planned_sources(factory, qdrant, settings.qdrant_collection, deletions)
            return await run_demo(args, settings, ingestor, existing_sources=existing, delete_planned=delete_planned)
        return await prepare_production(args, settings, factory, KnowledgeService(factory, qdrant, settings))
    finally:
        await qdrant.close()
        await engine.dispose()


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        asyncio.run(run(args))
    except (OSError, ValueError, PermissionError) as error:
        print(f"knowledge import error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
