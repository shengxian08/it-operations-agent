import importlib
import asyncio
import json

import pytest

from app.agent.decisions import TaskBudget
from app.agent.operations_tools import OperationsTools, OperationsScope, SnapshotReader
from app.llm.providers import ChatResult


def module():
    try:
        return importlib.import_module("app.agent.investigation")
    except ModuleNotFoundError:
        pytest.fail(
            "The bounded single and multi Agent investigation workflows are missing"
        )


class InvestigatorScript:
    async def complete(self, prompt):
        data = json.loads(prompt.split("\n", 1)[1].rsplit("\n", 1)[0])
        purpose = data["purpose"]
        if purpose == "logs_metrics" and not data["observations"]:
            value = {
                "kind": "call_tool",
                "tool": "get_service_health",
                "arguments": {"service": "checkout"},
            }
        elif purpose == "knowledge_changes" and not data["observations"]:
            value = {
                "kind": "call_tool",
                "tool": "get_release_changes",
                "arguments": {
                    "service": "checkout",
                    "start": "2026-10-01T07:00:00Z",
                    "end": "2026-10-01T09:00:00Z",
                },
            }
        else:
            value = {
                "kind": "finish",
                "claims": [
                    {
                        "kind": "conclusion",
                        "text": "这是一次尚未证实的故障归因",
                        "evidence_ids": ["invented"],
                        "quote": "invented",
                    }
                ],
            }
        return ChatResult(json.dumps(value), 10, 10, "scripted-control")


def tools():
    return OperationsTools(
        reader=SnapshotReader(
            {
                "checkout": {
                    "observed_at": "2026-10-01T08:00:00Z",
                    "health": {"reachable": False},
                    "changes": [{"at": "2026-10-01T08:00:00Z", "version": "v2"}],
                }
            },
            "public-fixture",
        )
    )


@pytest.mark.asyncio
async def test_two_specialists_have_independent_loops_and_controller_rejects_invented_evidence():
    m = module()
    result = await m.Investigator(InvestigatorScript(), tools(), strategy="multi").run(
        "checkout investigation",
        scope=OperationsScope("u", "employee", frozenset({"checkout"})),
        budget=TaskBudget(),
    )
    assert {o["tool"] for o in result["observations"]} == {
        "get_service_health",
        "get_release_changes",
    }
    assert len(result["subtasks"]) == 2
    assert result["conclusions"] == []
    assert result["hypotheses"][0]["verification"] == "unsupported"
    assert result["budget"]["model_calls"] == 5


@pytest.mark.asyncio
async def test_child_permissions_cannot_expand_and_shared_budget_does_not_multiply():
    m = module()
    parent = TaskBudget(max_tool_calls=1)
    result = await m.Investigator(InvestigatorScript(), tools(), strategy="multi").run(
        "checkout investigation",
        scope=OperationsScope("u", "employee", frozenset({"checkout"})),
        budget=parent,
    )
    assert parent.tool_calls == 1
    assert len(result["observations"]) == 1
    assert result["unknowns"]


@pytest.mark.asyncio
async def test_cancelled_parent_does_not_start_specialist_model_calls():
    m = module()
    budget = TaskBudget()
    budget.cancel()
    with pytest.raises(__import__("asyncio").CancelledError):
        await m.Investigator(InvestigatorScript(), tools(), strategy="multi").run(
            "goal",
            scope=OperationsScope("u", "employee", frozenset({"checkout"})),
            budget=budget,
        )


@pytest.mark.asyncio
async def test_investigation_mode_is_routed_by_langgraph_and_includes_structured_report():
    from app.agent.graph import GraphDependencies, build_graph

    m = module()
    dependencies = GraphDependencies(
        object(),
        InvestigatorScript(),
        object(),
        investigator=m.Investigator(InvestigatorScript(), tools(), strategy="multi"),
        investigation_scope=OperationsScope("u", "employee", frozenset({"checkout"})),
    )
    result = await build_graph(dependencies).ainvoke(
        {
            "user_id": "u",
            "conversation_id": "c",
            "message": "调查checkout",
            "task_mode": "investigate",
            "step_count": 0,
        }
    )
    assert result["final_state"] == "investigated"
    assert len(result["investigation_report"]["observations"]) == 2
    assert "只读调查报告" in result["answer"]


@pytest.mark.asyncio
async def test_public_mock_demo_uses_independent_tools_and_never_claims_causality():
    from app.llm.providers import MockChatProvider

    result = (
        await module()
        .Investigator(MockChatProvider(), tools(), strategy="multi")
        .run(
            "调查checkout 2026-10-01T07:00:00Z 至 2026-10-01T09:00:00Z",
            scope=OperationsScope("u", "employee", frozenset({"checkout"})),
            budget=TaskBudget(),
        )
    )
    assert {o["tool"] for o in result["observations"]} == {
        "get_service_health",
        "get_release_changes",
    }
    assert result["budget"]["model_calls"] == 5
    assert not any(c["kind"] == "conclusion" for c in result["conclusions"])


@pytest.mark.asyncio
async def test_fenced_progress_stops_all_children_and_propagates_original_error():
    class Fenced(RuntimeError):
        pass

    calls = []

    class Provider(InvestigatorScript):
        async def complete(self, prompt):
            calls.append(prompt)
            return await super().complete(prompt)

    async def fenced(data):
        raise Fenced("stale lease")

    budget = TaskBudget()
    with pytest.raises(Fenced, match="stale lease"):
        await (
            module()
            .Investigator(Provider(), tools(), strategy="multi")
            .run(
                "goal",
                scope=OperationsScope("u", "employee", frozenset({"checkout"})),
                budget=budget,
                on_progress=fenced,
            )
        )
    assert budget.cancelled and len(calls) <= 2
    assert not [
        t
        for t in asyncio.all_tasks()
        if t is not asyncio.current_task() and not t.done()
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["single", "multi"])
async def test_model_timeout_in_any_investigator_retains_uncertain_consumption(
    strategy,
):
    class Provider(InvestigatorScript):
        async def complete(self, prompt):
            purpose = json.loads(prompt.split("\n", 1)[1].rsplit("\n", 1)[0])["purpose"]
            if strategy == "single" or purpose == "logs_metrics":
                raise TimeoutError("public synthetic timeout")
            return await super().complete(prompt)

    budget = TaskBudget(max_tokens=256000)
    report = (
        await module()
        .Investigator(Provider(), tools(), strategy=strategy)
        .run(
            "checkout",
            scope=OperationsScope("u", "employee", frozenset({"checkout"})),
            budget=budget,
        )
    )
    assert report["status"] == "partial"
    assert budget.model_calls and budget.usage_uncertain
