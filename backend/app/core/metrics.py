"""Low-cardinality metrics exported only on the private API network."""
from prometheus_client import Counter, Histogram, Gauge
from datetime import datetime, timezone
import shutil
from sqlalchemy import text

HTTP_REQUESTS = Counter("itops_http_requests_total", "HTTP responses", ["method", "route", "status"])
HTTP_DURATION = Histogram("itops_http_duration_seconds", "HTTP request latency", ["route"], buckets=(.01,.05,.1,.25,.5,1,2,5,10,30,120))
RUNS = Counter("itops_runs_total", "Finalized runs", ["status", "final_state"])
RUN_DURATION = Histogram("itops_run_duration_seconds", "Run latency", buckets=(1,2,5,10,20,30,60,120))
QUEUED_RUNS = Gauge("itops_queued_runs", "Queued durable jobs")
MODEL_TOKENS = Counter("itops_model_tokens_total", "Model token usage", ["direction"])
DEPENDENCY_ERRORS = Counter("itops_dependency_errors_total", "Readiness probe failures", ["dependency"])
DURABLE_RUNS = Gauge("itops_durable_runs", "Persisted runs by status (shared across workers)", ["status"])
QUEUE_AGE = Gauge("itops_queue_oldest_seconds", "Age of the oldest queued run")
STALE_LEASES = Gauge("itops_stale_run_leases", "Running jobs with expired leases")
KNOWLEDGE_JOBS = Gauge("itops_knowledge_jobs", "Knowledge parser jobs by status", ["status"])
BUDGET_USED = Gauge("itops_model_budget_used", "Current month spent and reserved model budget")
BUDGET_LIMIT = Gauge("itops_model_budget_limit", "Configured current month budget")
PERSISTED_TOKENS = Gauge("itops_persisted_model_tokens", "Known model tokens from retained runs", ["direction"])
DISK_FREE = Gauge("itops_knowledge_disk_free_bytes", "Free bytes on the knowledge volume")
RUN_P95 = Gauge("itops_recent_run_p95_seconds", "Completed run latency P95 in the last hour")
WORKER_HEARTBEAT_AGE = Gauge("itops_worker_heartbeat_age_seconds", "Age of the single-host worker heartbeat")
INDEX_JOBS = Gauge("itops_knowledge_index_jobs", "Knowledge publication jobs by status", ["status"])


async def refresh_durable_metrics(sessions, settings):
    """Read shared durable state; worker process-local counters are not used as evidence."""
    async with sessions() as session:
        for status in ("queued", "running", "completed", "failed", "cancelled"):
            count = await session.scalar(text("SELECT count(*) FROM production_runs WHERE status=:status"), {"status":status})
            DURABLE_RUNS.labels(status).set(count or 0)
            if status == "queued":
                QUEUED_RUNS.set(count or 0)
        age, stale, inp, out, p95 = (await session.execute(text("""
            SELECT coalesce((SELECT extract(epoch FROM now()-min(created_at)) FROM production_runs WHERE status='queued'),0),
              (SELECT count(*) FROM production_runs WHERE status='running' AND lease_expires_at < now()),
              coalesce(sum(input_tokens),0), coalesce(sum(output_tokens),0),
              coalesce(percentile_cont(.95) WITHIN GROUP (ORDER BY extract(epoch FROM finished_at-started_at))
                FILTER (WHERE status='completed' AND finished_at > now()-interval '1 hour'),0)
            FROM production_runs"""))).one()
        QUEUE_AGE.set(age); STALE_LEASES.set(stale)
        PERSISTED_TOKENS.labels("input").set(inp); PERSISTED_TOKENS.labels("output").set(out); RUN_P95.set(p95)
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        used = await session.scalar(text("SELECT spent+reserved FROM production_model_budgets WHERE month=:month"), {"month":month})
        BUDGET_USED.set(used or 0); BUDGET_LIMIT.set(float(settings.model_monthly_budget))
        for status in ("queued", "running", "ready", "published", "failed"):
            count = await session.scalar(text("SELECT count(*) FROM production_knowledge_jobs WHERE status=:status"), {"status":status})
            KNOWLEDGE_JOBS.labels(status).set(count or 0)
        for status in ("queued", "running", "completed", "failed"):
            count = await session.scalar(text("SELECT count(*) FROM production_knowledge_index_jobs WHERE status=:status"), {"status":status})
            INDEX_JOBS.labels(status).set(count or 0)
    DISK_FREE.set(shutil.disk_usage(settings.knowledge_storage_path).free)
    if settings.log_directory:
        heartbeat=settings.log_directory / "worker.heartbeat"
        WORKER_HEARTBEAT_AGE.set(max(0, datetime.now(timezone.utc).timestamp()-heartbeat.stat().st_mtime) if heartbeat.exists() else 86400)

