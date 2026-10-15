import asyncio
import importlib

import pytest


def contracts():
    try:
        return importlib.import_module("app.agent.decisions")
    except ModuleNotFoundError:
        pytest.fail("The authorized bounded decision and budget contracts are missing")


def test_shared_budget_stops_the_second_child_at_the_parent_limit():
    c = contracts()
    budget = c.TaskBudget(max_tool_calls=2, max_model_calls=3, max_tokens=1000)
    budget.take_tool()
    budget.take_tool()
    with pytest.raises(c.BudgetExceeded, match="tool"):
        budget.take_tool()
    assert budget.snapshot()["tool_calls"] == 2


def test_cancellation_prevents_new_actions():
    c = contracts()
    budget = c.TaskBudget()
    budget.cancel()
    with pytest.raises(asyncio.CancelledError):
        budget.take_tool()


def test_model_reservation_cannot_overspend_tokens():
    c = contracts()
    budget = c.TaskBudget(max_tokens=20)
    with pytest.raises(c.BudgetExceeded, match="token"):
        budget.take_model("a" * 15, output_reserve=10)
    assert budget.snapshot()["model_calls"] == 0


@pytest.mark.parametrize(
    "payload",
    [
        {
            "kind": "call_tool",
            "tool": "search_knowledge",
            "arguments": {"user_id": "admin"},
        },
        {"kind": "finish", "answer": "已重启服务", "tool": "restart_service"},
        {
            "kind": "call_tool",
            "tool": "search_knowledge",
            "arguments": {},
            "role": "admin",
        },
    ],
)
def test_decision_cannot_supply_authority_or_mix_actions(payload):
    c = contracts()
    with pytest.raises(ValueError):
        c.AgentDecision.model_validate(payload)
