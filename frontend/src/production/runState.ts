import type { FinalResult, Run, RunEvent, RunView } from "./types";

export function isActiveRun(status: string) { return status === "queued" || status === "running"; }
export function emptyRunView(id: string): RunView { return { id, sequence: 0, status: "queued", steps: [], citations: [], final: null, handoff: null, problem: null }; }
export function applyRunEvent(view: RunView, event: RunEvent): RunView {
  if (event.sequence <= view.sequence) return view;
  const next = { ...view, sequence: event.sequence, problem: null };
  switch (event.type) {
    case "run_started": return { ...next, status: "running" };
    case "node_completed": return { ...next, steps: [...view.steps, { node: event.node, stepCount: event.step_count }] };
    case "citations": return { ...next, citations: event.citations };
    case "handoff": return { ...next, handoff: event.reason, escalationId: event.escalation_id ?? null };
    case "final": { const final = event as FinalResult; return { ...next, status: final.status ?? "completed", final, escalationId: final.escalation_id ?? view.escalationId }; }
    default: return next;
  }
}
export function recoverRun(view: RunView, run: Run): RunView {
  let next = view;
  for (const event of run.events ?? []) next = applyRunEvent(next, { ...event.data, type: event.type, sequence: event.sequence } as RunEvent);
  if (!next.final && run.result) next = { ...next, final: { ...run.result, type: "final" }, escalationId: run.result.escalation_id ?? next.escalationId, problem: null };
  next = { ...next, status: run.status };
  if (!isActiveRun(run.status) && !next.final && run.status !== "cancelled") next.problem = `运行${run.status === "failed" ? "失败" : "已结束"}，但缺少最终结果。请重新恢复或联系支持。`;
  return next;
}
