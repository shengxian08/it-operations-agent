import { describe, expect, it } from "vitest";
import { applyRunEvent, emptyRunView, recoverRun } from "./runState";

describe("durable run projection", () => {
  it("preserves the exact clarification object and order through final replay and durable recovery", () => {
    const lookup = { schema_version: 1 as const, user_id: "u", conversation_id: "c", outcome: "clarification" as const,
      ticket_number: null, basis: null, reason: "ambiguous_ticket_number" as const, candidates: ["IT-2026-0002", "IT-2026-0001"] };
    const result = { run_id: "r", answer: "请选择工单", message_id: "m", final_state: "ticket_lookup_clarification", trace_id: "t", ticket_lookup: lookup };
    const fromStream = applyRunEvent(emptyRunView("r"), { ...result, type: "final", sequence: 4 });
    const fromStorage = recoverRun(emptyRunView("r"), { id: "r", status: "completed", trace_id: "t", events: [], result });
    expect(fromStream.final?.ticket_lookup).toEqual(lookup);
    expect(fromStorage.final?.ticket_lookup).toEqual(lookup);
    expect(applyRunEvent(fromStream, { ...result, type: "final", sequence: 4 })).toBe(fromStream);
    expect(fromStorage.handoff).toBeNull();
  });
  it("ignores duplicate replay events and stores final exactly once", () => {
    const event = { sequence: 5, type: "final" as const, run_id: "r", answer: "已完成", message_id: "answer-1", final_state: "answered", trace_id: "t" };
    const first = applyRunEvent(emptyRunView("r"), event);
    expect(applyRunEvent(first, event)).toEqual(first);
    expect(first.final?.answer).toBe("已完成");
    expect(first.sequence).toBe(5);
  });

  it("recovers a missing final frame from the durable run result", () => {
    const run = { id: "r", status: "completed" as const, trace_id: "t", events: [], result: { run_id: "r", answer: "持久结果", message_id: "m", final_state: "answered", trace_id: "t" } };
    expect(recoverRun(emptyRunView("r"), run).final?.answer).toBe("持久结果");
  });

  it("marks a terminal run without a durable final as incomplete", () => {
    const run = { id: "r", status: "completed" as const, trace_id: "t", events: [], result: null };
    expect(recoverRun(emptyRunView("r"), run).problem).toMatch(/缺少最终结果/);
  });

  it("recovers the durable human support record number when the stream ended early", () => {
    const run = { id: "r", status: "completed" as const, trace_id: "t", events: [],
      result: { run_id: "r", answer: "需要人工检查", message_id: "m", final_state: "handoff", trace_id: "t", escalation_id: "esc-42" } };
    expect(recoverRun(emptyRunView("r"), run).escalationId).toBe("esc-42");
  });

  it("preserves unanswered intake fields and explicit no-attempts after durable recovery", () => {
    const intake = { schema_version: 1 as const, user_id: "u", conversation_id: "c", outcome: "collecting" as const,
      problem: "VPN错误E42", impact: null, attempted_steps: [] as string[], next_field: "impact" as const, reason: null };
    const result = { run_id: "r", answer: "请说明影响范围", message_id: "m", final_state: "ticket_collection", trace_id: "t", ticket_intake: intake };
    const view = recoverRun(emptyRunView("r"), { id: "r", status: "completed", trace_id: "t", events: [], result });
    expect(view.final?.ticket_intake?.impact).toBeNull();
    expect(view.final?.ticket_intake?.attempted_steps).toEqual([]);
    expect(view.handoff).toBeNull();
    expect(view.problem).toBeNull();
  });
});
