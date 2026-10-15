import asyncio
import importlib
from datetime import datetime, timezone

import pytest

from app.agent.decisions import TaskBudget


def module():
    try:
        return importlib.import_module("app.agent.operations_tools")
    except ModuleNotFoundError:
        pytest.fail("Readonly operational tool contracts are missing")


@pytest.mark.asyncio
async def test_snapshot_health_has_origin_observation_time_and_resource_scope():
    m = module()
    reader = m.SnapshotReader(
        {
            "s1": {
                "observed_at": "2026-10-01T08:00:00+00:00",
                "health": {"reachable": False},
            }
        },
        "fixture-1",
    )
    tools = m.OperationsTools(reader=reader)
    scope = m.OperationsScope("u", "employee", frozenset({"s1"}))
    result = await tools.call(
        "get_service_health", {"service": "s1"}, scope, TaskBudget()
    )
    assert result.status == "ok"
    assert result.origin == "snapshot"
    assert result.snapshot_id == "fixture-1"
    assert result.observed_at == datetime(2026, 10, 1, 8, tzinfo=timezone.utc)
    assert result.data == {"reachable": False}


@pytest.mark.asyncio
async def test_out_of_scope_resource_and_model_authority_are_rejected():
    m = module()
    tools = m.OperationsTools(reader=m.SnapshotReader({}, "fixture-1"))
    scope = m.OperationsScope("u", "employee", frozenset({"public"}))
    for args in ({"service": "secret"}, {"service": "public", "user_id": "admin"}):
        result = await tools.call("get_service_health", args, scope, TaskBudget())
        assert result.status == "unknown"
        assert result.error_code in {"forbidden_resource", "invalid_arguments"}
        assert not result.data
    assert len(tools.audit) == 2
    assert all(entry["status"] == "unknown" for entry in tools.audit)


@pytest.mark.asyncio
async def test_timeout_is_unknown_and_not_healthy_or_empty_logs():
    m = module()

    class Slow:
        async def read(self, *args):
            await asyncio.sleep(1)

    tools = m.OperationsTools(reader=Slow(), timeout_seconds=0.01)
    result = await tools.call(
        "get_service_health",
        {"service": "s1"},
        m.OperationsScope("u", "employee", frozenset({"s1"})),
        TaskBudget(),
    )
    assert result.status == "unknown"
    assert result.error_code == "tool_timeout"


@pytest.mark.asyncio
async def test_revocation_after_read_discards_data():
    m = module()
    checks = 0

    async def authorize(scope, tool, args):
        nonlocal checks
        checks += 1
        return checks == 1

    tools = m.OperationsTools(
        reader=m.SnapshotReader(
            {
                "s1": {
                    "observed_at": "2026-10-01T08:00:00Z",
                    "health": {"reachable": True},
                }
            },
            "f",
        ),
        authorize=authorize,
    )
    result = await tools.call(
        "get_service_health",
        {"service": "s1"},
        m.OperationsScope("u", "employee", frozenset({"s1"})),
        TaskBudget(),
    )
    assert result.status == "unknown"
    assert result.error_code == "authorization_changed"
    assert not result.data
