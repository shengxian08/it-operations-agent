import asyncio
import importlib.util
from types import SimpleNamespace

import pytest

from app.services.chat import ChatEvent


def worker_module():
    assert importlib.util.find_spec("app.production.worker"), "durable background worker is missing"
    from app.production import worker
    return worker


class FakeRuns:
    def __init__(self):
        self.events, self.terminals = [], []
        self.alive = True
    async def history(self, run_id):
        return [{"role": "user", "content": "前一轮"}]
    async def ticket_intake_context(self, run_id, token):
        assert token == "lease"
        return None
    async def ticket_lookup_context(self, run_id, token):
        assert token == "lease"
        return {"user_id": "employee", "conversation_id": "conversation", "candidates": [],
                "pending_candidates": None, "recent_lookup": False, "overflow": False}
    async def heartbeat(self, run_id, token):
        return self.alive
    async def append_event(self, run_id, token, name, data):
        self.events.append((name, data))
        return self.alive
    async def finalize(self, run_id, token, status, result, usage=None):
        self.terminals.append((status, result, usage))
        return self.alive


class FakeBusiness:
    async def describe_draft_token(self, token, user_id, conversation_id):
        assert user_id == "employee"
        return {"draft_id": "draft1", "version": 1, "run_id": "run1"}
    async def record_handoff(self, user_id, conversation_id, run_id, reason, trace_id, lease_token=None):
        assert lease_token == "lease"
        return {"id": "escalation1"}


class FakeChat:
    def __init__(self, mode="success"):
        self.mode = mode
        self.kwargs = None
    async def stream_run(self, **kwargs):
        self.kwargs = kwargs
        if self.mode == "fail":
            raise RuntimeError("private provider details")
        if self.mode == "slow":
            await asyncio.sleep(100)
        yield ChatEvent("run_started", {"run_id": kwargs["run_id"]})
        yield ChatEvent("ticket_draft", {"draft": {"title": "example"}, "confirmation_token": "token"})
        yield ChatEvent("handoff", {"reason": "no_evidence"})
        final = {"answer": "answer", "final_state": "answered", "usage": {"input_tokens": 3, "output_tokens": 4}}
        if self.mode == "error_result":
            final.update(error="model_failed:ProviderDetails", handoff_reason="model_unavailable", final_state="handoff")
        if self.mode == "error_confirmation":
            final.update(error="confirmation_failed:PrivateDetails", handoff_reason="confirmation_unavailable", final_state="handoff")
        yield ChatEvent("final", final)


@pytest.mark.asyncio
async def test_worker_consumes_job_without_http_and_enriches_draft_and_escalation():
    module = worker_module()
    runs, chat = FakeRuns(), FakeChat()
    async def factory(settings, user_access_level, index_revision):
        assert user_access_level == "employee" and index_revision == "fixed-revision"
        return chat
    worker = module.Worker(SimpleNamespace(run_timeout_seconds=1, lease_seconds=30), runs, FakeBusiness(), None, chat_factory=factory)
    job = {"id": "run1", "lease_token": "lease", "user_id": "employee", "conversation_id": "conversation",
           "user_access_level": "employee", "index_revision": "fixed-revision", "content": "当前轮", "trace_id": "trace"}
    await worker.execute(job)
    assert chat.kwargs["history"] == [{"role": "user", "content": "前一轮"}]
    assert all(name != "run_started" for name, data in runs.events)
    assert runs.events[0][1]["draft_id"] == "draft1"
    assert runs.events[1][1]["escalation_id"] == "escalation1"
    assert runs.terminals[0][0] == "completed"
    assert runs.terminals[0][1]["ticket_draft"]["version"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected", [("fail", "processing_failed"), ("slow", "run_timeout")])
async def test_worker_errors_and_timeouts_are_controlled_durable_terminal_events(mode, expected):
    module = worker_module()
    runs = FakeRuns()
    async def factory(*args, **kwargs):
        return FakeChat(mode)
    worker = module.Worker(SimpleNamespace(run_timeout_seconds=0.02, lease_seconds=30), runs, FakeBusiness(), None, chat_factory=factory)
    job = {"id": "run1", "lease_token": "lease", "user_id": "employee", "conversation_id": "conversation",
           "user_access_level": "employee", "index_revision": "", "content": "help", "trace_id": "trace"}
    await worker.execute(job)
    assert runs.terminals[0][0] == "failed"
    assert runs.terminals[0][1]["error"] == expected
    assert "private provider details" not in str(runs.terminals)
    assert runs.events[-1][0] == "handoff"
    assert runs.events[-1][1]["escalation_id"] == "escalation1"


@pytest.mark.asyncio
async def test_worker_fenced_by_cancellation_stops_graph_and_cannot_write_terminal():
    module = worker_module()
    runs = FakeRuns()
    runs.alive = False
    async def factory(*args, **kwargs):
        return FakeChat("slow")
    worker = module.Worker(SimpleNamespace(run_timeout_seconds=1, lease_seconds=30), runs, FakeBusiness(), None, chat_factory=factory)
    job = {"id": "run1", "lease_token": "lease", "user_id": "employee", "conversation_id": "conversation",
           "user_access_level": "employee", "index_revision": "", "content": "help", "trace_id": "trace"}
    await asyncio.wait_for(worker.execute(job), 0.5)
    assert runs.terminals == [] and runs.events == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,code", [("error_result", "model_unavailable"), ("error_confirmation", "confirmation_unavailable")])
async def test_provider_error_terminal_uses_controlled_code_and_unknown_usage(mode, code):
    module = worker_module()
    runs = FakeRuns()
    async def factory(*args, **kwargs):
        return FakeChat(mode)
    worker = module.Worker(SimpleNamespace(run_timeout_seconds=1, lease_seconds=30), runs, FakeBusiness(), None, chat_factory=factory)
    job = {"id": "run1", "lease_token": "lease", "user_id": "employee", "conversation_id": "conversation",
           "user_access_level": "employee", "index_revision": "", "content": "help", "trace_id": "trace"}
    await worker.execute(job)
    assert runs.terminals[0][0] == "failed" and runs.terminals[0][2] is None
    assert runs.terminals[0][1]["error"] == code


@pytest.mark.asyncio
async def test_worker_health_file_updates_only_when_queue_loop_progresses_and_clears_on_stop(tmp_path):
    module = worker_module()
    stalled, release = asyncio.Event(), asyncio.Event()

    class Queue:
        calls = 0
        async def reap(self):
            self.calls += 1
            if self.calls == 2:
                stalled.set()
                await release.wait()
        async def claim(self, worker_id):
            return None

    worker = module.Worker(SimpleNamespace(worker_concurrency=1, log_directory=tmp_path), Queue(), None, None)
    task = asyncio.create_task(worker.run())
    health_file = tmp_path / "worker.heartbeat"
    try:
        await asyncio.wait_for(stalled.wait(), 2)
        assert health_file.is_file(), "the daemon must publish its successful polling progress"
        assert health_file.read_bytes() == b""
        previous = health_file.stat().st_mtime_ns
        await asyncio.sleep(0.1)
        assert health_file.stat().st_mtime_ns == previous, "blocked storage must not produce a fresh health signal"
        release.set()
        async with asyncio.timeout(2):
            while health_file.stat().st_mtime_ns == previous:
                await asyncio.sleep(0.01)
    finally:
        release.set()
        worker.stop_event.set()
        await task
    assert not health_file.exists(), "shutdown must stop advertising healthy progress"


@pytest.mark.asyncio
async def test_worker_entrypoint_forces_worker_runtime_role(monkeypatch):
    module = worker_module()
    from app.core.config import Settings
    import app.core.config
    import app.core.telemetry
    import app.runtime

    original = Settings(_env_file=None, environment="test", runtime_role="api")
    captured = []
    closed = []
    monkeypatch.setattr(app.core.config, "get_settings", lambda: original)
    monkeypatch.setattr(app.core.telemetry, "configure_telemetry", lambda settings: None)
    monkeypatch.setattr(module.signal, "signal", lambda *args: None)

    async def open_runtime(settings):
        captured.append(settings.runtime_role)
        return SimpleNamespace(session_factory=None, redis=None, ticket_service=None, knowledge_service=None)
    async def close_runtime():
        closed.append(True)
    class IdleWorker:
        def __init__(self, settings, *args):
            captured.append(settings.runtime_role)
            self.stop_event = asyncio.Event()
        async def run(self):
            pass
    monkeypatch.setattr(app.runtime, "open_runtime", open_runtime)
    monkeypatch.setattr(app.runtime, "close_runtime", close_runtime)
    monkeypatch.setattr(module, "Worker", IdleWorker)
    await module.main()
    assert captured == ["worker", "worker"]
    assert original.runtime_role == "api"
    assert closed == [True]
