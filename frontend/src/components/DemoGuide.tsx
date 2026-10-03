import { DEMO_EXAMPLES } from "../demoContent";

interface DemoGuideProps {
  onSelectPrompt: (prompt: string) => void;
}

export function DemoGuide({ onSelectPrompt }: DemoGuideProps) {
  return (
    <section className="guide-section" id="try" aria-labelledby="guide-title">
      <div className="section-heading">
        <div>
          <span className="section-kicker">START HERE</span>
          <h2 id="guide-title">四条路径看懂这个项目</h2>
        </div>
        <p>以下案例以默认身份 u-001 / c-001 为准。选择案例会填入对话框，点击发送后再对照右侧结果验证。</p>
      </div>
      <div className="example-grid">
        {DEMO_EXAMPLES.map((example) => (
          <button
            className="example-card"
            type="button"
            key={example.id}
            aria-label={`使用${example.title === "问知识" ? "知识问答" : example.title}示例`}
            onClick={() => onSelectPrompt(example.prompt)}
          >
            <span className="example-number">{example.number}</span>
            <strong>{example.title}</strong>
            <span className="example-prompt">{example.prompt}</span>
            <span className="example-expected">预期：{example.expected}</span>
            <span className="example-action">填入对话框 ↗</span>
          </button>
        ))}
      </div>
      <div className="input-guide">
        <div>
          <strong>输入格式</strong>
          <p>直接用自然语言描述问题，不需要 JSON。写清故障现象、错误代码、影响范围和已经尝试的操作，会更容易得到有依据的回答。</p>
        </div>
        <div>
          <strong>查单格式</strong>
          <p>写成“查询工单 IT-2026-0001 的进度”。工单号至少四位流水号；截图中的 <code>IT-2005-001</code> 只有三位，不是本项目的演示工单。</p>
        </div>
        <div>
          <strong>演示身份</strong>
          <p><code>u-001 / c-001</code> 是演示员工甲与其会话；<code>u-002 / c-002</code> 是员工乙。两人都是 employee，只能查询本人资料和工单。</p>
        </div>
      </div>
    </section>
  );
}
