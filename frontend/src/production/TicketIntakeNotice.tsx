import type { TicketIntake } from "../types";

export function TicketIntakeNotice({ intake }: { intake: TicketIntake }) {
  if (intake.outcome !== "collecting") return null;
  const questions = {
    problem: "请在输入框描述具体故障现象或错误信息。",
    impact: "请在输入框说明影响范围，以及是否阻断工作。",
    attempted_steps: "请在输入框说明已尝试的操作及结果；尚未操作可回复“尚未尝试”。",
  };
  return <section className="result-card ticket-intake-notice" aria-label="正在收集工单信息">
    <h3>已提供的工单信息</h3>
    <dl className="intake-facts">
      <dt>故障现象</dt><dd>{intake.problem ?? "尚未回答"}</dd>
      <dt>影响范围</dt><dd>{intake.impact ?? "尚未回答"}</dd>
      <dt>已尝试操作</dt><dd>{intake.attempted_steps === null ? "尚未回答" : intake.attempted_steps.length === 0 ? "员工明确尚未尝试" : <ul>{intake.attempted_steps.map((step, index) => <li key={index}>{step}</li>)}</ul>}</dd>
    </dl>
    {intake.next_field ? <p>{questions[intake.next_field]}</p> : null}
    <p className="field-help">信息补齐后将生成待确认草稿。你也可以回复“取消建单”。</p>
  </section>;
}
