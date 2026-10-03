import type { RunStep } from "../types";

interface RunTimelineProps {
  runId: string | null;
  steps: RunStep[];
  active: boolean;
  finalState: string | null;
}

const NODE_LABELS: Record<string, string> = {
  classify_intent: "识别请求类型",
  retrieve_evidence: "检索知识资料",
  decide_next_action: "决定处理路径",
  answer_with_citations: "整理引用答案",
  lookup_ticket: "查询本人工单",
  collect_ticket_draft: "整理工单草稿",
  issue_confirmation: "准备安全确认",
  handoff: "准备人工转交",
};

function nodeLabel(node: string): string {
  return NODE_LABELS[node] ?? "正在处理请求";
}

export function RunTimeline({ runId, steps, active, finalState }: RunTimelineProps) {
  if (!runId && !active) return null;

  return (
    <details className="result-card timeline">
      <summary className="card-title-row">
        <div>
          <span className="section-kicker">RUN TRACE</span>
          <h2>处理过程</h2>
        </div>
        <span className="run-state">{active ? "运行中" : finalState ? "查看步骤" : "已完成"}</span>
      </summary>
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
    </details>
  );
}
