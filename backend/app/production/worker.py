"""Run with ``python -m app.production.worker``. HTTP subscribers never own jobs."""
import asyncio
import contextlib
import inspect
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.core.telemetry import log_json

class LeaseLost(Exception):
    """Another terminal transition fenced this worker out."""


class Worker:
    def __init__(self, settings, run_service, ticket_service, knowledge_service, *, chat_factory=None):
        self.settings = settings
        self.runs = run_service
        self.tickets = ticket_service
        self.knowledge = knowledge_service
        self.chat_factory = chat_factory
        self.worker_id = f"{os.getpid()}-{uuid4().hex}"
        self.stop_event = asyncio.Event()

    async def _heartbeat(self, job):
        while True:
            if not await self.runs.heartbeat(job["id"], job["lease_token"]):
                raise LeaseLost()
            await asyncio.sleep(max(0.05, min(1, self.settings.lease_seconds / 3)))

    async def _process(self, job):
        if self.chat_factory is None:
            from app.runtime import build_chat_service
            factory = build_chat_service
        else:
            factory = self.chat_factory
        chat = factory(self.settings, user_access_level=job["user_access_level"], index_revision=job["index_revision"])
        if inspect.isawaitable(chat):
            chat = await chat
        history = await self.runs.history(job["id"])
        ticket_context = await self.runs.ticket_lookup_context(job["id"], job["lease_token"])
        intake_context = await self.runs.ticket_intake_context(job["id"], job["lease_token"])
        final, draft, escalation_id = None, None, None
        async for event in chat.stream_run(user_id=job["user_id"], conversation_id=job["conversation_id"],
                                          content=job["content"], trace_id=job["trace_id"], run_id=job["id"], history=history,
                                          lease_token=job["lease_token"], ticket_context=ticket_context,
                                          ticket_intake_context=intake_context):
            data = dict(event.data)
            if event.name == "run_started":
                continue  # Claim already committed the canonical start event.
            if event.name == "final":
                final = data
                continue  # Final event and terminal DB status must commit atomically.
            if event.name == "ticket_draft":
                details = await self.tickets.describe_draft_token(data["confirmation_token"], job["user_id"], job["conversation_id"])
                if details.get("run_id") != job["id"]:
                    raise RuntimeError("draft belongs to another run")
                data.update(draft_id=details["draft_id"], version=details["version"])
                draft = data
            if event.name == "handoff":
                if not await self.runs.heartbeat(job["id"], job["lease_token"]):
                    raise LeaseLost()
                escalation = await self.tickets.record_handoff(job["user_id"], job["conversation_id"], job["id"],
                                                                str(data.get("reason") or "processing_failed"), job["trace_id"],
                                                                lease_token=job["lease_token"])
                escalation_id = escalation["id"]
                data["escalation_id"] = escalation_id
            if not await self.runs.append_event(job["id"], job["lease_token"], event.name, data):
                raise LeaseLost()
        if final is None:
            raise RuntimeError("graph returned without a terminal event")
        if draft is not None:
            final["ticket_draft"] = draft
        if escalation_id is not None:
            final["escalation_id"] = escalation_id
        usage = final.pop("usage", None)
        status = "completed"
        if final.get("error") or final.get("handoff_reason") in {"model_unavailable", "processing_failed", "persistence_failed"}:
            status = "failed"
            allowed_codes = {"model_unavailable", "model_empty_response", "processing_failed", "persistence_failed",
                             "confirmation_unavailable", "ticket_lookup_failed", "retrieval_failed", "invalid_input"}
            reason = final.get("handoff_reason")
            final["error"] = reason if reason in allowed_codes else "processing_failed"
            usage = None
        if usage and usage.get("usage_uncertain"):
            usage = None
        if not await self.runs.finalize(job["id"], job["lease_token"], status, final, usage=usage):
            raise LeaseLost()

    async def _failure(self, job, code):
        result = {"answer": "运行中断，请重试或联系 IT 支持。", "final_state": "handoff", "error": code}
        if await self.runs.heartbeat(job["id"], job["lease_token"]):
            try:
                escalation = await self.tickets.record_handoff(job["user_id"], job["conversation_id"], job["id"], code, job["trace_id"],
                                                                lease_token=job["lease_token"])
                result["escalation_id"] = escalation["id"]
                await self.runs.append_event(job["id"], job["lease_token"], "handoff", {"reason": code, "escalation_id": escalation["id"]})
            except Exception as error:
                log_json("handoff_persistence_failed", trace_id=job["trace_id"], run_id=job["id"], error_type=type(error).__name__)
        # Unknown provider consumption is settled conservatively by the run service.
        await self.runs.finalize(job["id"], job["lease_token"], "failed", result)

    async def execute(self, job):
        processing = asyncio.create_task(self._process(job))
        heartbeat = asyncio.create_task(self._heartbeat(job))
        duration = self.settings.run_timeout_seconds
        if job.get("deadline_at"):
            deadline = datetime.fromisoformat(job["deadline_at"])
            duration = min(duration, max(0.001, (deadline - datetime.now(timezone.utc)).total_seconds()))
        try:
            async with asyncio.timeout(duration):
                done, _ = await asyncio.wait({processing, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
                if heartbeat in done:
                    await heartbeat
                await processing
        except LeaseLost:
            log_json("worker_fenced", trace_id=job["trace_id"], run_id=job["id"])
        except TimeoutError:
            processing.cancel()
            await asyncio.gather(processing, return_exceptions=True)
            await self._failure(job, "run_timeout")
        except asyncio.CancelledError:
            processing.cancel()
            await asyncio.gather(processing, return_exceptions=True)
            await asyncio.shield(self._failure(job, "worker_shutdown"))
            raise
        except Exception as error:
            log_json("run_processing_failed", trace_id=job["trace_id"], run_id=job["id"], error_type=type(error).__name__)
            await self._failure(job, "processing_failed")
        finally:
            processing.cancel()
            heartbeat.cancel()
            await asyncio.gather(processing, heartbeat, return_exceptions=True)

    async def _knowledge_loop(self):
        if self.knowledge is None:
            return
        while not self.stop_event.is_set():
            try:
                processed = await self.knowledge.process_next_job()
                if processed:
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log_json("knowledge_worker_failed", error_type=type(error).__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.stop_event.wait(), 1)

    async def run(self):
        active: set[asyncio.Task] = set()
        log_directory = getattr(self.settings, "log_directory", None)
        health_file = Path(log_directory) / "worker.heartbeat" if log_directory is not None else None
        if health_file is not None:
            health_file.parent.mkdir(parents=True, exist_ok=True)
        knowledge = asyncio.create_task(self._knowledge_loop())
        try:
            while not self.stop_event.is_set():
                active = {task for task in active if not task.done()}
                await self.runs.reap()
                while len(active) < self.settings.worker_concurrency and not self.stop_event.is_set():
                    job = await self.runs.claim(self.worker_id)
                    if job is None:
                        break
                    task = asyncio.create_task(self.execute(job))
                    # Retrieve exceptions even if storage fails and execute cannot write terminal state.
                    task.add_done_callback(self._task_finished)
                    active.add(task)
                # Publish completed polling progress. If storage hangs, this stamp
                # stops advancing and the external health check expires it.
                if health_file is not None:
                    health_file.write_bytes(b"")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.stop_event.wait(), 0.5)
        finally:
            if health_file is not None:
                with contextlib.suppress(OSError):
                    health_file.unlink(missing_ok=True)
            knowledge.cancel()
            for task in active:
                task.cancel()
            await asyncio.gather(knowledge, *active, return_exceptions=True)

    @staticmethod
    def _task_finished(task):
        if task.cancelled():
            return
        error = task.exception()
        if error:
            log_json("worker_task_failed", error_type=type(error).__name__)


async def main():
    from app.core.config import get_settings
    from app.core.telemetry import configure_telemetry
    from app.production.runs import RunService
    from app.runtime import close_runtime, open_runtime
    settings = get_settings().model_copy(update={"runtime_role": "worker"})
    configure_telemetry(settings)
    runtime = await open_runtime(settings)
    worker = Worker(settings, RunService(settings, runtime.session_factory, runtime.redis), runtime.ticket_service, runtime.knowledge_service)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.stop_event.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(worker.stop_event.set))
    try:
        await worker.run()
    finally:
        await close_runtime()


if __name__ == "__main__":
    asyncio.run(main())
