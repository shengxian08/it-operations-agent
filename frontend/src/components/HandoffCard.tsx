interface HandoffCardProps {
  reason: string;
  userInput: string;
}

export function HandoffCard({ reason, userInput }: HandoffCardProps) {
  return (
    <section className="result-card handoff-card" aria-labelledby="handoff-title">
      <span className="section-kicker">HUMAN HANDOFF</span>
      <h2 id="handoff-title">需要人工支持介入</h2>
      <p>当前请求无法由自动流程可靠完成，建议将以下摘要交给 IT 支持团队。</p>
      <dl>
        <div><dt>转交原因</dt><dd>{reason}</dd></div>
        <div><dt>用户输入</dt><dd>{userInput}</dd></div>
      </dl>
    </section>
  );
}
