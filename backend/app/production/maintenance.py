"""Retention and orphan-index maintenance. Defaults to a reviewable dry run."""
import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlalchemy import text
from app.core.config import get_settings
from app.runtime import open_runtime, close_runtime


async def retention(runtime, apply):
    statements = {
        "chat_messages": ("SELECT count(*) FROM messages WHERE created_at<now()-interval '90 days' AND content<>'[聊天内容已按90天保留策略清理]'", "UPDATE messages SET content='[聊天内容已按90天保留策略清理]', citations='[]'::jsonb,user_feedback=NULL WHERE created_at<now()-interval '90 days' AND content<>'[聊天内容已按90天保留策略清理]'"),
        "run_events": ("SELECT count(*) FROM production_run_events WHERE created_at<now()-interval '90 days'", "DELETE FROM production_run_events WHERE created_at<now()-interval '90 days'"),
        "run_content": ("SELECT count(*) FROM production_runs WHERE finished_at<now()-interval '90 days' AND result IS NOT NULL AND NOT (result ? 'content_expired')", "UPDATE production_runs SET result=jsonb_build_object('run_id',id,'trace_id',trace_id,'status',status,'final_state','content_expired','content_expired',true,'answer','聊天内容已按保留策略清理') WHERE finished_at<now()-interval '90 days' AND result IS NOT NULL AND NOT (result ? 'content_expired')"),
        "business_audits": ("SELECT count(*) FROM production_business_audits WHERE created_at<now()-interval '180 days'", "DELETE FROM production_business_audits WHERE created_at<now()-interval '180 days'"),
        "tool_audits": ("SELECT count(*) FROM tool_audits WHERE created_at<now()-interval '180 days'", "DELETE FROM tool_audits WHERE created_at<now()-interval '180 days'"),
    }
    result = {}
    async with runtime.session_factory.begin() as session:
        # Single maintenance execution across cron instances; active runs never reach these ages.
        await session.execute(text("SELECT pg_advisory_xact_lock(17070140)"))
        for name,(query,mutation) in statements.items():
            result[name] = await session.scalar(text(query))
            if apply:
                await session.execute(text(mutation))
    return result


async def orphan_indexes(runtime, apply):
    async with runtime.session_factory.begin() as session:
        # Publication holds the same pointer row while its external index is built.
        # This lock prevents checking/deleting a collection in the commit gap.
        await session.execute(text("SELECT id FROM production_knowledge_active_pointer WHERE id=1 FOR UPDATE"))
        known = set(await session.scalars(text("SELECT collection_name FROM production_knowledge_revisions")))
        protected = list(await session.scalars(text("SELECT staging_collections FROM production_knowledge_index_jobs WHERE status='running' AND lease_expires_at>now()")))
        known.update(name for collections in protected for name in collections)
        # Only named collections registered by our own jobs are eligible for destructive GC.
        staged = set(name for collections in await session.scalars(text("SELECT staging_collections FROM production_knowledge_index_jobs WHERE status IN ('failed','completed') OR lease_expires_at<now()-interval '1 hour'")) for name in collections)
        existing = {collection.name for collection in (await runtime.qdrant.get_collections()).collections}
        candidates = sorted((staged & existing)-known)
        if apply:
            for name in candidates:
                await runtime.qdrant.delete_collection(name)
        return {"collections":candidates,"protected_published_revisions":len(known)}


def expire_backup_files(directory: Path, apply: bool):
    root = directory.resolve()
    threshold = datetime.now(timezone.utc)-timedelta(days=14)
    eligible=[]
    for path in root.glob("itops-*.tar.gz.age"):
        if path.is_symlink() or path.resolve().parent != root:
            continue
        try:
            date = datetime.strptime(path.name.removeprefix("itops-").removesuffix(".tar.gz.age"),"%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if date < threshold:
            eligible.append(path.name)
            if apply:
                path.unlink()
    return eligible


async def main(args):
    if args.action == "backups":
        print(json.dumps({"apply":args.apply,"expired_backups":expire_backup_files(Path(args.directory),args.apply)}))
        return
    settings = get_settings().model_copy(update={"runtime_role":"worker"})
    runtime = await open_runtime(settings)
    try:
        result = await (retention(runtime,args.apply) if args.action == "retention" else orphan_indexes(runtime,args.apply))
        print(json.dumps({"action":args.action,"apply":args.apply,**result}))
    finally:
        await close_runtime()


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["retention","orphan-indexes","backups"])
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--directory",default="/backups")
    asyncio.run(main(parser.parse_args()))
