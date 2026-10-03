#!/usr/bin/env python3
"""Post-restore functional smoke, restricted to a disposable test database.

This validates the restored PG data against existing test Qdrant. It does not certify
full host, files, identity, Qdrant, offsite, RPO or RTO recovery.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))


async def probe(args):
    from sqlalchemy import select, func
    from app.core.config import Settings
    from app.db.models import User
    from app.production.identity_models import IdentityAccount
    from app.production.identity import Principal
    from app.production.run_models import ProductionRun
    from app.production.business_models import ProductionTicket
    from app.production.runs import RunService
    from app.production.worker import Worker
    from app.runtime import open_runtime, close_runtime

    database = urlsplit(args.database_url).path.lstrip("/")
    if "test" not in database.lower() or urlsplit(args.database_url).hostname not in {"127.0.0.1", "localhost", "postgres"}:
        raise ValueError("Functional restore smoke is restricted to a local disposable *test* database")
    settings = Settings(_env_file=None, environment="test", demo_enabled=False, runtime_role="worker",
                        database_url=args.database_url, redis_url=args.redis_url, qdrant_url=args.qdrant_url,
                        model_mode="mock", embedding_mode="deterministic", cookie_secure=False,
                        session_secret="restore-probe-test-secret-not-for-production-123456789", user_rate_limit=100)
    runtime = await open_runtime(settings)
    try:
        async with runtime.session_factory() as session:
            employee = await session.scalar(select(User).join(IdentityAccount, IdentityAccount.user_id == User.id)
                .where(User.access_level == "employee", IdentityAccount.enabled.is_(True)))
            if employee is None:
                raise ValueError("Restored database has no enabled test employee")
            initial_tickets = await session.scalar(select(func.count()).select_from(ProductionTicket))
        principal = Principal(employee.id, employee.display_name, employee.access_level)
        runs = RunService(settings, runtime.session_factory, runtime.redis)
        worker = Worker(settings, runs, runtime.ticket_service, runtime.knowledge_service)
        conversation = await runs.create_conversation(principal, "隔离恢复验收")
        results = []
        for content in ("公司VPN错误619应该先检查什么？", "请创建工单\n问题：VPN一直连接失败\n影响范围：仅本人无法办公\n已尝试：重启客户端后仍失败"):
            key = uuid4().hex
            submitted = await runs.enqueue(principal, conversation["id"], content, key, key)
            job = await runs.claim("restore-probe-" + uuid4().hex)
            if job is None or job["id"] != submitted["id"]:
                raise ValueError("Restore probe requires an idle isolated run queue")
            await worker.execute(job)
            current = await runs.get_run(principal, job["id"])
            if current["status"] != "completed":
                raise ValueError("Restored execution did not complete")
            results.append(current["result"])
        assert results[0]["final_state"] == "answered" and results[0]["citations"], "Restored knowledge lookup failed"
        draft = results[1].get("ticket_draft")
        assert results[1]["final_state"] == "awaiting_confirmation" and draft, "Restored draft issuance failed"
        key = uuid4().hex
        created = await runtime.ticket_service.confirm_draft(principal.user_id, draft["draft_id"], draft["version"], draft["confirmation_token"], key)
        retried = await runtime.ticket_service.confirm_draft(principal.user_id, draft["draft_id"], draft["version"], draft["confirmation_token"], key)
        assert created == retried, "Restored confirmation is not idempotent"
        async with runtime.session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(ProductionTicket)) == initial_tickets + 1
            assert await session.scalar(select(func.count()).select_from(ProductionRun).where(ProductionRun.conversation_id == conversation["id"], ProductionRun.status == "completed")) == 2
        return {"test_only": True, "database": database, "restored_knowledge_answer": True,
                "durable_runs_completed": 2, "confirmed_ticket_delta": 1, "idempotent_confirmation": True,
                "full_host_recovery_verified": False}
    finally:
        await close_runtime()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--redis-url", default="redis://127.0.0.1:16379/14")
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:17333")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = asyncio.run(probe(args))
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
