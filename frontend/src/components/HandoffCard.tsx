interface HandoffCardProps {
  reason: string;
  userInput: string;
}

const REASONS: Record<string, { title: string; action: string }> = {
  insufficient_evidence: {
    title: "资料不足，无法可靠回答",
    action: "请核对问题描述、工单编号，或补充错误代码与已尝试步骤。",
  },
  missing_ticket_number: {
    title: "缺少工单编号",
    action: "请使用“查询工单 IT-2026-0001 的进度”这样的完整问题。",
  },
  invalid_input: { title: "输入信息无效", action: "请检查输入是否为空，工单号是否为 IT-年份-至少四位流水号。" },
  restricted_request: { title: "请求涉及受限操作", action: "需要由 IT 支持人员核对身份与权限。" },
  retrieval_failed: { title: "知识检索暂时不可用", action: "请稍后重试，或联系 IT 支持人员。" },
  ticket_lookup_failed: { title: "工单查询暂时失败", action: "请稍后重试，或联系 IT 支持人员。" },
  max_steps_exceeded: { title: "处理步骤达到安全上限", action: "请联系 IT 支持人员继续排查。" },
};

export function HandoffCard({ reason, userInput }: HandoffCardProps) {
  const explanation = REASONS[reason] ?? {
    title: "自动处理未完成",
    action: "请将问题描述交给 IT 支持人员。",
  };
  const hasShortTicketNumber = /\bIT-\d{4}-\d{1,3}\b/i.test(userInput);
  return (
    <section className="result-card handoff-card" aria-labelledby="handoff-title">
      <span className="section-kicker">HUMAN HANDOFF</span>
      <h2 id="handoff-title">需要人工支持介入</h2>
      <p>{explanation.title}。{explanation.action}</p>
      {hasShortTicketNumber && (
        <p className="handoff-hint">你输入的工单号流水号不足四位。当前演示工单为 <code>IT-2026-0001</code> 这样的格式。</p>
      )}
      <dl>
        <div><dt>原因代码</dt><dd><code>{reason}</code></dd></div>
        <div><dt>用户输入</dt><dd>{userInput}</dd></div>
      </dl>
    </section>
  );
}
