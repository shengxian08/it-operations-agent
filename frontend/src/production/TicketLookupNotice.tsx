import type { TicketLookup } from "../types";

export function TicketLookupNotice({ lookup, disabled, onSelect }: {
  lookup: TicketLookup;
  disabled: boolean;
  onSelect: (number: string) => void;
}) {
  if (lookup.outcome !== "clarification") return null;
  const requiresNumber = lookup.reason === "too_many_candidates" || lookup.candidates.length === 0;
  return <section className="result-card ticket-lookup-notice" aria-label="工单查询澄清">
    <h3>查询哪张工单？</h3>
    {requiresNumber ? <p>{lookup.reason === "too_many_candidates" ? "此会话涉及的工单较多。" : "还没有确定要查询的工单。"}</p> : <p>请选择一个编号，查询当前处理进度。</p>}
    {requiresNumber ? <p>在输入框中填写完整工单号（如 IT-2026-0001），然后发送请求。</p> :
      <div className="ticket-lookup-choices">{lookup.candidates.map((number, index) =>
        <button key={number} disabled={disabled} aria-label={`查询工单 ${number}`} onClick={() => onSelect(number)}>
          <span>第 {index + 1} 张</span><strong>{number}</strong>
        </button>)}</div>}
  </section>;
}
