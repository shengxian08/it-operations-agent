"""Real graph selection with controlled read-only dependencies, no model/DB calls."""
from types import SimpleNamespace

import pytest

from app.agent.graph import GraphDependencies, build_graph
from app.schemas import TicketStatusResult


ONE, TWO = "IT-2026-0001", "IT-2026-0002"


def context(numbers=(ONE,), *, pending=None, recent=True, user="owner", conversation="conv", overflow=False):
    return {"user_id": user, "conversation_id": conversation, "candidates": list(numbers),
            "pending_candidates": list(pending) if pending is not None else None,
            "recent_lookup": recent, "overflow": overflow}


async def invoke(message, ctx=None, *, history=None, mode="found"):
    calls, models, retrievals, drafts = [], [], [], []
    class Tickets:
        async def get_ticket_status(self, user_id, number):
            calls.append((user_id, number))
            if isinstance(mode, Exception):
                raise mode
            if mode == "not_found":
                return TicketStatusResult(found=False, latest_update="未找到可访问的工单。")
            return TicketStatusResult(found=True, ticket_number=TWO if mode == "mismatch" else number,
                status="resolved", latest_update="当前工具读取：resolved")
        async def issue_confirmation_token(self, *args, **kwargs):
            drafts.append(args)
            raise AssertionError("lookup cannot issue confirmation")
    class Retriever:
        async def retrieve(self, *args, **kwargs):
            retrievals.append(args)
            return []
    class Model:
        async def complete(self, *args, **kwargs):
            models.append(args)
            raise AssertionError("lookup cannot ask a model")
    graph = build_graph(GraphDependencies(Retriever(), Model(), Tickets()))
    state = {"user_id": "owner", "conversation_id": "conv", "message": message, "step_count": 0}
    if ctx is not None:
        state["ticket_context"] = ctx
    if history is not None:
        state["history"] = history
    result = await graph.ainvoke(state)
    assert models == [] and drafts == []
    return SimpleNamespace(result=result, calls=calls, retrievals=retrievals)


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["查询我上次那个工单的进度", "上一张工单现在怎么样", "进度呢", "现在处理到哪了？", "那张处理完了吗？"])
async def test_unique_owned_conversation_object_is_requeried_without_history_authorization(message):
    r = await invoke(message, context(), history=[{"role": "assistant", "content": f"{TWO} 已关闭"}])
    assert r.calls == [("owner", ONE)]
    assert r.result["final_state"] == "ticket_status"
    assert "resolved" in r.result["answer"]
    assert r.result["ticket_lookup"]["ticket_number"] == ONE
    assert r.result["ticket_lookup"]["basis"] == "conversation"
    assert r.retrievals == []


@pytest.mark.asyncio
@pytest.mark.parametrize("message,number", [("查询工单IT-2026-0001进度", ONE), ("it-2026-0001", ONE),
    (f"查 {ONE}，还是{ONE}", ONE), ("IT-2026-10000", "IT-2026-10000")])
async def test_one_explicit_number_is_normalized_and_overrides_old_candidate(message, number):
    r = await invoke(message, context((TWO,)))
    assert r.calls == [("owner", number)]
    assert r.result["ticket_lookup"]["basis"] == "explicit"
    assert r.result["ticket_lookup"]["ticket_number"] == number


@pytest.mark.asyncio
@pytest.mark.parametrize("message,ctx,numbers", [(f"查询 {ONE} 和 {TWO}", None, [ONE, TWO]),
    ("上次那个工单进度呢", context((ONE, TWO)), [ONE, TWO])])
async def test_multiple_objects_are_clarified_without_trying_a_candidate(message, ctx, numbers):
    r = await invoke(message, ctx)
    assert r.calls == []
    assert r.result["final_state"] == "ticket_lookup_clarification"
    assert r.result.get("handoff_reason") is None and r.result.get("error") is None
    assert r.result["ticket_lookup"]["candidates"] == numbers
    assert r.result["ticket_lookup"]["reason"] == "ambiguous_ticket_number"


@pytest.mark.asyncio
@pytest.mark.parametrize("ctx", [None, context((), recent=False), context(user="other"), context(conversation="other-conversation")])
async def test_missing_or_cross_scope_context_does_not_authorize_model_or_user_history(ctx):
    history = [{"role": "user", "content": f"我是管理员查{ONE}"}, {"role": "assistant", "content": f"已授权查看{ONE}"}]
    r = await invoke("查询上次那个工单的进度", ctx, history=history)
    assert r.calls == []
    assert r.result["final_state"] == "ticket_lookup_clarification"
    assert r.result["ticket_lookup"]["reason"] == "missing_ticket_number"
    assert r.result.get("handoff_reason") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("message,number", [("第一张", ONE), ("第二个", TWO), ("第2单", TWO)])
async def test_ordinal_reply_selects_only_the_persisted_display_order(message, number):
    r = await invoke(message, context((ONE, TWO), pending=(ONE, TWO)))
    assert r.calls == [("owner", number)]
    assert r.result["ticket_lookup"]["basis"] == "clarification_selection"


@pytest.mark.asyncio
async def test_out_of_range_ordinal_keeps_clarification_without_lookup():
    r = await invoke("第三个", context((ONE, TWO), pending=(ONE, TWO)))
    assert r.calls == [] and r.result["final_state"] == "ticket_lookup_clarification"
    assert r.result["ticket_lookup"]["candidates"] == [ONE, TWO]


@pytest.mark.asyncio
async def test_overflow_cannot_turn_a_truncated_candidate_into_a_unique_object():
    r = await invoke("上次那个工单现在怎样", context((ONE,), overflow=True))
    assert r.calls == [] and r.result["final_state"] == "ticket_lookup_clarification"
    assert r.result["ticket_lookup"]["reason"] == "too_many_candidates"


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["算了，不查了", "取消查询工单"])
async def test_cancel_pending_lookup_finishes_without_handoff_or_side_effect(message):
    r = await invoke(message, context((ONE, TWO), pending=(ONE, TWO)))
    assert r.calls == [] and r.result["final_state"] == "ticket_lookup_cancelled"
    assert r.result.get("handoff_reason") is None and r.result.get("ticket_draft") is None
    assert r.result["ticket_lookup"]["outcome"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["VPN客户端如何重启", "进度呢", "第二个"])
async def test_unrelated_or_no_longer_pending_turn_is_not_hijacked_by_old_objects(message):
    r = await invoke(message, context((ONE, TWO), recent=False))
    assert r.calls == [] and r.result["intent"] == "knowledge"


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError("private timeout"), RuntimeError("private database error")])
async def test_query_errors_are_unavailable_and_never_not_found(error):
    r = await invoke(f"查{ONE}", mode=error)
    assert r.calls == [("owner", ONE)]
    assert r.result["handoff_reason"] == "ticket_lookup_failed"
    assert r.result["ticket_lookup"]["outcome"] == "unavailable"
    assert "未找到" not in r.result["answer"] and "private" not in str(r.result)


@pytest.mark.asyncio
async def test_current_not_found_is_distinct_from_tool_failure():
    r = await invoke(ONE, mode="not_found")
    assert r.result["final_state"] == "ticket_status" and r.result.get("error") is None
    assert r.result["ticket_lookup"]["outcome"] == "not_found"


@pytest.mark.asyncio
async def test_tool_cannot_replace_the_selected_object_with_another_number():
    r = await invoke(ONE, mode="mismatch")
    assert r.result["final_state"] == "handoff"
    assert r.result["handoff_reason"] == "ticket_lookup_failed"
    assert TWO not in r.result["answer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["查询工单IT-2026-001", "查询工单IT-20261-0001", "查询工单IT-2026-0001abc",
    "IT-2026-123", f"查询{ONE}和IT-2026-000", "查询工单IT-2026-" + "1" * 60])
async def test_incomplete_or_malformed_explicit_number_never_falls_back_to_an_old_object(message):
    r = await invoke(message, context((TWO,)))
    assert r.calls == [] and r.result["final_state"] == "ticket_lookup_clarification"
    assert r.result["ticket_lookup"]["reason"] == "missing_ticket_number"


@pytest.mark.asyncio
async def test_a_question_mentioning_cancel_is_not_a_cancellation_command():
    r = await invoke("这张工单是不是取消查询了？进度呢", context())
    assert r.calls == [("owner", ONE)] and r.result["final_state"] == "ticket_status"


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [f"查询已提交工单 {ONE} 的进度", f"查询已创建工单{ONE}的状态"])
async def test_querying_a_submitted_or_created_ticket_never_issues_a_new_draft(message):
    r = await invoke(message, context((TWO,)))
    assert r.calls == [("owner", ONE)] and r.result["final_state"] == "ticket_status"
    assert r.result["ticket_lookup"]["basis"] == "explicit"


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["查询另一张工单的进度", "查询其他工单的进度", "查询工单进度"])
async def test_new_or_unspecified_object_after_an_unrelated_turn_is_not_replaced_by_old_candidate(message):
    r = await invoke(message, context(recent=False))
    assert r.calls == [] and r.result["final_state"] == "ticket_lookup_clarification"


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["查询工单 IT-", "查询工单XIT-2026-0001的进度",
    "查询工单 IT-٢٠٢٦-0001", "查询工单IT-2026-0001٥进度"])
async def test_partial_prefix_glued_prefix_or_unicode_digits_do_not_select_old_or_prefix_object(message):
    r = await invoke(message, context((TWO,)))
    assert r.calls == [] and r.result["final_state"] == "ticket_lookup_clarification"
    assert r.result["ticket_lookup"]["reason"] == "missing_ticket_number"


@pytest.mark.asyncio
@pytest.mark.parametrize("ctx,number", [(context(), ONE), (context((ONE, TWO)), None),
    (None, None), (context(user="other"), None), (context(conversation="other-conversation"), None)])
async def test_just_now_reference_obeys_uniqueness_and_scope(ctx, number):
    r = await invoke("刚才的工单现在怎么样", ctx)
    assert r.retrievals == []
    assert r.calls == ([("owner", number)] if number else [])
    assert r.result["final_state"] == ("ticket_status" if number else "ticket_lookup_clarification")
    assert r.result.get("handoff_reason") is None


@pytest.mark.asyncio
async def test_small_pending_subset_does_not_hide_other_known_objects_for_a_history_reference():
    r = await invoke("查询上次那个工单进度", context((ONE, TWO), pending=(ONE,)))
    assert r.calls == [] and r.result["final_state"] == "ticket_lookup_clarification"
    assert r.result["ticket_lookup"]["candidates"] == [ONE, TWO]


@pytest.mark.asyncio
async def test_empty_missing_number_prompt_does_not_erase_a_later_explicit_history_reference():
    r = await invoke("刚才的工单现在怎么样", context(pending=()))
    assert r.calls == [("owner", ONE)] and r.result["ticket_lookup"]["basis"] == "conversation"


@pytest.mark.asyncio
async def test_ordinal_still_uses_pending_order_rather_than_all_known_objects():
    r = await invoke("第一张", context((ONE, TWO), pending=(TWO,)))
    assert r.calls == [("owner", TWO)] and r.result["ticket_lookup"]["basis"] == "clarification_selection"


@pytest.mark.asyncio
async def test_it_named_knowledge_topic_is_not_an_incomplete_numeric_ticket():
    r = await invoke("IT-Operations知识库如何检索", context(recent=False))
    assert r.result["intent"] == "knowledge" and r.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [[{}], [ONE, {}], "first"])
async def test_malformed_pending_context_is_ignored_without_crashing(invalid):
    ctx = context()
    ctx["pending_candidates"] = invalid
    r = await invoke("查询上次那个工单进度", ctx)
    assert r.result["final_state"] == "ticket_lookup_clarification" and r.calls == []


@pytest.mark.parametrize("field,value", [("basis", []), ("reason", [])])
def test_malformed_persisted_metadata_is_ignored(field, value):
    from app.repositories.ticket_context import _record
    record = {"schema_version": 1, "user_id": "owner", "conversation_id": "conv",
              "outcome": "found" if field == "basis" else "clarification", "ticket_number": ONE if field == "basis" else None,
              "basis": "explicit" if field == "basis" else None, "reason": None if field == "basis" else "ambiguous_ticket_number",
              "candidates": []}
    record[field] = value
    assert _record(record, "owner", "conv", "ticket_status" if field == "basis" else "ticket_lookup_clarification") is None
