"""Actual graph intake contract; controlled tools, no models or business writes."""
from types import SimpleNamespace

import pytest

from app.agent.graph import GraphDependencies, build_graph
from app.schemas import TicketStatusResult


def context(*, problem=None, impact=None, attempted=None, user="owner", conversation="conv"):
    return {"schema_version": 1, "user_id": user, "conversation_id": conversation,
            "outcome": "collecting", "problem": problem, "impact": impact,
            "attempted_steps": attempted,
            "next_field": "problem" if problem is None else "impact" if impact is None else "attempted_steps",
            "reason": None}


async def run(message, ctx=None, history=None):
    tokens, lookups, retrievals = [], [], []
    class Retriever:
        async def retrieve(self, query, **kwargs):
            retrievals.append(query)
            return []
    class Model:
        async def complete(self, *args):
            raise AssertionError("intake must not call a model")
    class Tickets:
        async def issue_confirmation_token(self, conversation_id, draft, **kwargs):
            tokens.append(draft)
            return "controlled-intake-token"
        async def get_ticket_status(self, user_id, ticket_number):
            lookups.append(ticket_number)
            return TicketStatusResult(found=True, ticket_number=ticket_number, status="open", latest_update="等待处理")
    graph = build_graph(GraphDependencies(Retriever(), Model(), Tickets()))
    state = {"user_id": "owner", "conversation_id": "conv", "message": message, "step_count": 0}
    if ctx is not None:
        state["ticket_intake_context"] = ctx
    if history is not None:
        state["history"] = history
    result = await graph.ainvoke(state)
    return SimpleNamespace(result=result, tokens=tokens, lookups=lookups, retrievals=retrievals)


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["创建工单", "帮我建单", "open a ticket", "提交工单"])
async def test_empty_creation_asks_for_problem_without_a_confirmation_or_handoff(message):
    out = await run(message)
    assert out.result["final_state"] == "ticket_collection"
    assert out.result["ticket_intake"]["next_field"] == "problem"
    assert out.tokens == out.lookups == out.retrievals == []
    assert "confirmation_token" not in out.result and "handoff_reason" not in out.result


@pytest.mark.asyncio
async def test_initial_problem_is_preserved_and_impact_is_requested():
    out = await run("创建工单：VPN无法连接")
    assert out.result["final_state"] == "ticket_collection"
    assert out.result["ticket_intake"]["problem"] == "VPN无法连接"
    assert out.result["ticket_intake"]["next_field"] == "impact"
    assert out.result["ticket_intake"]["attempted_steps"] is None
    assert out.tokens == []


@pytest.mark.asyncio
@pytest.mark.parametrize("message,ctx,field,value,next_field", [
    ("VPN报错E42，无法连接", context(), "problem", "VPN报错E42，无法连接", "impact"),
    ("仅我一人，无法办公", context(problem="VPN连接失败"), "impact", "仅我一人，无法办公", "attempted_steps"),
])
async def test_natural_answer_fills_only_the_owned_server_requested_field(message, ctx, field, value, next_field):
    out = await run(message, ctx)
    assert out.result["final_state"] == "ticket_collection"
    record = out.result["ticket_intake"]
    assert record[field] == value and record["next_field"] == next_field
    assert out.tokens == out.lookups == out.retrievals == []


@pytest.mark.asyncio
async def test_actual_attempts_complete_a_reviewable_draft_with_impact():
    out = await run("已经重启客户端；更换网络仍失败", context(problem="VPN连接失败", impact="仅我一人，无法办公"))
    assert out.result["final_state"] == "awaiting_confirmation"
    assert len(out.tokens) == 1
    draft = out.tokens[0]
    assert draft.title == "VPN连接失败" and draft.category == "network"
    assert draft.priority == "high" and "影响范围：仅我一人，无法办公" in draft.description
    assert draft.attempted_steps == ("已经重启客户端", "更换网络仍失败")
    assert out.result["ticket_intake"]["outcome"] == "ready"
    assert out.lookups == out.retrievals == []


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["尚未尝试", "没有尝试过", "未尝试任何操作"])
async def test_explicit_no_attempt_is_an_empty_list_not_a_missing_answer(message):
    out = await run(message, context(problem="VPN连接失败", impact="仅我一人"))
    assert out.result["final_state"] == "awaiting_confirmation"
    assert out.result["ticket_intake"]["attempted_steps"] == []
    assert out.tokens[0].attempted_steps == ()


@pytest.mark.asyncio
async def test_all_labeled_facts_can_be_provided_in_one_creation_request():
    out = await run("创建工单\n问题：VPN连接失败\n影响范围：仅本人\n已尝试：重启客户端；更换网络")
    assert out.result["final_state"] == "awaiting_confirmation"
    assert len(out.tokens) == 1
    assert out.tokens[0].description == "VPN连接失败\n影响范围：仅本人"
    assert out.tokens[0].attempted_steps == ("重启客户端", "更换网络")


@pytest.mark.asyncio
async def test_labeled_correction_preserves_other_fields_and_still_asks_for_missing_facts():
    out = await run("问题：邮箱无法登录", context(problem="VPN连接失败", impact="仅本人"))
    assert out.result["final_state"] == "ticket_collection"
    record = out.result["ticket_intake"]
    assert record["problem"] == "邮箱无法登录" and record["impact"] == "仅本人"
    assert record["next_field"] == "attempted_steps" and out.tokens == []


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["确认", "好的", "不知道"])
async def test_control_answers_cannot_supply_required_impact_or_issue_a_token(message):
    out = await run(message, context(problem="VPN连接失败"))
    assert out.result["final_state"] == "ticket_collection"
    assert out.result["ticket_intake"]["impact"] is None and out.tokens == []


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["；；", "确认；好的", "！！"])
async def test_separators_or_control_only_attempts_do_not_mean_explicit_no_attempts(message):
    out = await run(message, context(problem="VPN连接失败", impact="仅本人"))
    assert out.result["final_state"] == "ticket_collection"
    assert out.result["ticket_intake"]["attempted_steps"] is None
    assert out.tokens == []


@pytest.mark.asyncio
async def test_punctuation_only_problem_is_not_an_employee_fact():
    out = await run("创建工单\n问题：！？\n影响范围：仅本人\n已尝试：尚未尝试")
    assert out.result["final_state"] == "ticket_collection" and out.tokens == []
    assert out.result["ticket_intake"]["problem"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["取消建单", "算了，不建了"])
async def test_cancel_closes_intake_without_business_side_effects(message):
    out = await run(message, context(problem="VPN连接失败"))
    assert out.result["final_state"] == "ticket_collection_cancelled"
    assert out.result["ticket_intake"]["outcome"] == "cancelled"
    assert out.tokens == out.lookups == out.retrievals == []


@pytest.mark.asyncio
@pytest.mark.parametrize("ctx", [context(user="other"), context(conversation="other")])
async def test_cross_scope_context_cannot_turn_an_answer_into_a_draft(ctx):
    out = await run("影响范围：仅本人", ctx)
    assert out.tokens == [] and "ticket_intake" not in out.result


@pytest.mark.asyncio
async def test_arbitrary_assistant_or_user_history_does_not_fill_missing_facts():
    out = await run("创建工单", history=[{"role": "assistant", "content": "问题：VPN失败；影响范围：全公司；已尝试：重启"},
                                       {"role": "user", "content": "问题：别人故障；影响范围：全部；已尝试：重启"}])
    assert out.result["final_state"] == "ticket_collection"
    assert out.result["ticket_intake"]["problem"] is None and out.tokens == []


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["问题：" + "故" * 2001, "影响范围：" + "人" * 1001,
                                    "已尝试：" + "步" * 301, "已尝试：" + "；".join(["重启软件"] * 11)],
                         ids=["problem-limit", "impact-limit", "step-limit", "count-limit"])
async def test_oversized_facts_are_rejected_without_truncation_or_a_token(message):
    out = await run(message, context(problem="VPN连接失败", impact="仅本人"))
    assert out.result["final_state"] == "ticket_collection"
    assert out.result["ticket_intake"]["reason"] == "field_too_long"
    assert out.result["ticket_intake"]["problem"] == "VPN连接失败" and out.tokens == []


@pytest.mark.asyncio
async def test_explicit_lookup_interrupts_intake_instead_of_filling_a_problem():
    out = await run("查询工单 IT-2026-0001 的进度", context())
    assert out.result["final_state"] == "ticket_status"
    assert out.lookups == ["IT-2026-0001"] and out.tokens == []


@pytest.mark.asyncio
async def test_explicit_knowledge_question_does_not_fill_pending_impact():
    out = await run("VPN如何重新连接？", context(problem="VPN连接失败"))
    assert out.retrievals == ["VPN如何重新连接？"]
    assert out.tokens == [] and "ticket_intake" not in out.result


@pytest.mark.asyncio
async def test_incomplete_metadata_is_ignored_without_crashing_or_issuing_a_token():
    incomplete = {"schema_version": 1, "user_id": "owner", "conversation_id": "conv",
                  "outcome": "collecting", "next_field": "problem", "reason": None}
    out = await run("VPN连接失败", incomplete)
    assert out.tokens == [] and "ticket_intake" not in out.result


@pytest.mark.asyncio
async def test_labeled_fault_containing_a_ticket_reference_does_not_query_that_ticket():
    out = await run("问题：工单查询页面打开IT-2026-0001时显示报错E42", context())
    assert out.result["final_state"] == "ticket_collection"
    assert out.result["ticket_intake"]["problem"] == "工单查询页面打开IT-2026-0001时显示报错E42"
    assert out.lookups == out.tokens == []


@pytest.mark.asyncio
async def test_signed_draft_carries_structured_facts_for_versioned_edit_validation():
    out = await run("创建工单\n问题：VPN连接失败\n影响范围：仅本人\n已尝试：尚未尝试")
    draft = out.tokens[0]
    assert draft.problem == "VPN连接失败"
    assert draft.impact == "仅本人" and draft.intake_version == 1
    assert draft.attempted_steps == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["取消", "算了", "不用了", "不要创建工单"])
async def test_plain_cancel_never_becomes_attempted_facts_or_a_signed_draft(message):
    out = await run(message, context(problem="VPN连接失败", impact="仅本人"))
    assert out.result["final_state"] == "ticket_collection_cancelled"
    assert out.result["ticket_intake"]["outcome"] == "cancelled"
    assert out.tokens == out.lookups == out.retrievals == []


@pytest.mark.asyncio
async def test_actual_cancellation_action_in_fault_steps_is_preserved():
    out = await run("已点击取消按钮后重新连接", context(problem="VPN连接失败", impact="仅本人"))
    assert out.result["final_state"] == "awaiting_confirmation"
    assert out.tokens[0].attempted_steps == ("已点击取消按钮后重新连接",)


@pytest.mark.asyncio
async def test_quoted_negative_creation_is_fault_description_not_control():
    out = await run('问题：点击按钮后提示“不要创建工单”，VPN仍连接失败', context())
    assert out.result["final_state"] == "ticket_collection" and out.tokens == []
    assert out.result["ticket_intake"]["problem"] == '点击按钮后提示“不要创建工单”，VPN仍连接失败'


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["", "已尝试："])
async def test_quoted_creation_in_attempt_description_keeps_owned_facts(prefix):
    description = '点击后提示“不要创建工单”，已重启客户端'
    out = await run(prefix + description, context(problem="VPN连接失败", impact="仅本人"))
    assert out.result["final_state"] == "awaiting_confirmation"
    assert out.tokens[0].problem == "VPN连接失败" and out.tokens[0].impact == "仅本人"
    assert out.tokens[0].attempted_steps == (description,)
