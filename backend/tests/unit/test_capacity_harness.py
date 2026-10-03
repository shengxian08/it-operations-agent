import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("capacity_harness", Path(__file__).resolve().parents[3] / "scripts/production/load_test.py")
load = importlib.util.module_from_spec(spec)
spec.loader.exec_module(load)


class Response:
    status_code = 202
    def __init__(self, value):
        self.value = value
    def raise_for_status(self):
        pass
    def json(self):
        return self.value


class Client:
    heartbeat_forever = False
    def __init__(self, **kwargs):
        pass
    async def get(self, path):
        if path.endswith("/me"):
            await asyncio.sleep(.04)  # Setup time must not count towards sustained load.
            return Response({"id": "test-employee"})
        if "/knowledge/documents" in path:
            return Response({"documents": [{}], "next_cursor": None})
        if "/runs/" in path:
            return Response({"status": "completed", "result": {"final_state": "answered"}})
        return Response({"tickets": []})
    async def post(self, path, **kwargs):
        return Response({"id": "test-run"})
    def stream(self, *args):
        return Stream(self.heartbeat_forever)
    async def aclose(self):
        pass


class Stream:
    def __init__(self, forever):
        self.forever = forever
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        pass
    def raise_for_status(self):
        pass
    async def aiter_lines(self):
        if self.forever:
            while True:
                await asyncio.sleep(.003)
                yield ": heartbeat"
        await asyncio.sleep(.001)
        yield 'data: {"node":"retrieve_evidence","latency_ms":1}'


def arguments(tmp_path):
    sessions, queries = tmp_path / "sessions.json", tmp_path / "queries.json"
    sessions.write_text(json.dumps({"accounts": [{"cookie": "test-only", "csrf_token": "test-only"}]}))
    queries.write_text('["synthetic question"]')
    return SimpleNamespace(base_url="http://127.0.0.1:9999", environment="test", sessions=str(sessions),
        queries=str(queries), concurrency=1, duration=.035, think_seconds=0, documents=1,
        run_timeout=.025, real_model=False, output=str(tmp_path / "report.json"))


@pytest.mark.asyncio
async def test_preflight_time_is_excluded_and_short_mock_cannot_pass(monkeypatch, tmp_path):
    monkeypatch.setattr(load.httpx, "AsyncClient", Client)
    monkeypatch.setattr(Client, "heartbeat_forever", False)
    args = arguments(tmp_path)
    assert not await load.exercise(args)
    report = json.loads(Path(args.output).read_text())
    assert report["actual_load_seconds"] >= args.duration - .005
    assert report["duration_seconds"] >= report["actual_load_seconds"] + .03
    assert report["completed_dialogues"] > 0 and not report["capacity_passed"]


@pytest.mark.asyncio
async def test_continuous_sse_heartbeats_cannot_hide_a_hung_run(monkeypatch, tmp_path):
    monkeypatch.setattr(load.httpx, "AsyncClient", Client)
    monkeypatch.setattr(Client, "heartbeat_forever", True)
    args = arguments(tmp_path)
    async with asyncio.timeout(.5):
        assert not await load.exercise(args)
    report = json.loads(Path(args.output).read_text())
    assert report["failed_operations"] > 0
    assert report["failures"][0]["error_type"] == "TimeoutError"
