import type { RunStep } from "../types";

interface RunTimelineProps {
  runId: string | null;
  steps: RunStep[];
  active: boolean;
  finalState: string | null;
}

const NODE_LABELS: Record<string, string> = {
  classify_intent: "正在识别请求",
  retrieve_evidence: "正在检索知识",
  decide_next_action: "正在规划下一步",
  answer_with_citations: "正在整理引用答案",
  lookup_ticket: "正在查询工单",
  collect_ticket_draft: "正在整理工单草稿",
  issue_confirmation: "正在准备安全确认",
  handoff: "正在准备人工转交",
};

function nodeLabel(node: string): string {
  return NODE_LABELS[node] ?? "正在处理请求";
}

export function RunTimeline({ runId, steps, active, finalState }: RunTimelineProps) {
  if (!runId && !active) return null;

  return (
    <section className="result-card timeline" aria-labelledby="timeline-title">
      <div className="card-title-row">
        <div>
          <span className="section-kicker">RUN TRACE</span>
          <h2 id="timeline-title">运行轨迹</h2>
        </div>
        <span className="run-state">{active ? "运行中" : finalState ?? "已完成"}</span>
      </div>
      {runId && <code title={runId}>RUN {runId.slice(0, 8).toUpperCase()}</code>}
      <ol>
        {steps.map((step, index) => (
          <li key={`${step.node}-${index}`}>
            <span aria-hidden="true" />
            <div>
              <strong>{nodeLabel(step.node)}</strong>
              <small>步骤 {step.stepCount}</small>
            </div>
          </li>
        ))}
        {active && (
          <li className="pending-step">
            <span aria-hidden="true" />
            <div><strong>等待下一节点</strong></div>
          </li>
        )}
      </ol>
    </section>
  );
}
