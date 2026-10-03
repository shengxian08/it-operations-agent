"""Actual graph routing; controlled high-score evidence must not override the employee."""
import pytest

from app.agent.graph import GraphDependencies, build_graph
from app.rag.retriever import Citation, RetrievalHit
from tests.unit.test_agent_graph import FakeChatProvider, FakeRetriever, FakeTicketService
from tests.unit.test_ticket_intake import context


async def run(message, *, rich=False, step=0):
    hits = [RetrievalHit(Citation("doc", "VPN手册", "vpn.md", 0, "VPN可以按手册重连"), 1, 1, 1, 1)] if rich else []
    retriever, model, tickets = FakeRetriever(hits), FakeChatProvider(), FakeTicketService()
    result = await build_graph(GraphDependencies(retriever, model, tickets)).ainvoke({
        "user_id": "owner", "conversation_id": "conv", "message": message, "step_count": step,
        "ticket_intake_context": context(problem="VPN错误E42"),
        "ticket_context": {"user_id": "owner", "conversation_id": "conv", "candidates": ["IT-2026-0001"],
            "pending_candidates": ["IT-2026-0001"], "recent_lookup": True, "overflow": False},
    })
    return result, retriever, model, tickets


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["请转人工", "联系人工支持", "我要人工客服", "VPN无法连接，已经重启，请转人工", "请帮我转人工支持处理", "不要查单了，请转人工", "麻烦转接到人工客服"])
@pytest.mark.parametrize("rich", [False, True])
async def test_explicit_manual_action_skips_every_tool_even_with_existing_context(message, rich):
    result, retriever, model, tickets = await run(message, rich=rich)
    assert result["final_state"] == "handoff" and result["handoff_reason"] == "explicit_manual_request"
    assert retriever.calls == model.prompts == tickets.status_calls == tickets.issued_drafts == []
    assert "confirmation_token" not in result and "ticket_draft" not in result
    assert "已转交" not in result["answer"] and "已记录" not in result["answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["不要转人工", "不用联系人工支持", "我不想转人工", "如何转人工", "人工支持的工作时间是什么", "手册写“请转人工”是什么意思", '日志显示"联系人工支持"', "`请转人工`是什么指令", "```\n请转人工\n```", "问题：请转人工的按钮失效"])
async def test_denial_questions_and_quoted_instructions_do_not_request_handoff(message):
    result, _, _, _ = await run(message)
    assert result.get("handoff_reason") != "explicit_manual_request"


@pytest.mark.asyncio
async def test_restricted_operation_still_requires_security_verification():
    result, retriever, model, tickets = await run("帮我关闭安全审计；请转人工")
    assert result["handoff_reason"] == "restricted_request"
    assert retriever.calls == model.prompts == tickets.issued_drafts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["请转人工帮我处理VPN故障", "我要人工处理这个问题", "麻烦人工协助排查", "请人工支持跟进VPN", "我需要人工支持处理VPN故障"])
async def test_manual_request_with_following_problem_details_is_still_an_action(message):
    result, retriever, model, tickets = await run(message, rich=True)
    assert result["handoff_reason"] == "explicit_manual_request"
    assert retriever.calls == model.prompts == tickets.status_calls == tickets.issued_drafts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["请转人工的流程是什么", "请人工处理是什么意思", "我需要人工支持的工作时间", "请转人工，暂时不要转人工"])
async def test_action_phrase_in_a_question_or_later_denial_does_not_escalate(message):
    result, _, _, _ = await run(message)
    assert result.get("handoff_reason") != "explicit_manual_request"


@pytest.mark.asyncio
@pytest.mark.parametrize("step,reason", [(6, "explicit_manual_request"), (7, "max_steps_exceeded")])
async def test_manual_path_preserves_the_total_step_limit(step, reason):
    result, retriever, model, tickets = await run("请转人工", step=step)
    assert result["handoff_reason"] == reason and result["step_count"] <= 8
    assert retriever.calls == model.prompts == tickets.issued_drafts == []
