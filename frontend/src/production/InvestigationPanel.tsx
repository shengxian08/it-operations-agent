import type { RunView } from "./types";

const TOOLS: Record<string,string>={search_knowledge:"知识检索",get_service_health:"服务状态",query_error_logs:"错误日志",query_metrics:"运行指标",get_release_changes:"发布变更",lookup_ticket:"工单查询"};
export function InvestigationPanel({run}:{run:RunView}) {
  const report=run.final?.investigation_report;
  const observations=report?.observations ?? run.observations ?? [];
  const budget=report?.budget ?? run.agentBudget;
  if(!report && !observations.length) return null;
  return <section className="result-card" aria-label="只读调查证据"><h2>调查证据</h2>
    <p role="status">{report ? report.status==="partial" ? "部分完成，仍有证据缺口" : "调查报告已生成" : "正在读取和核对证据"}</p>
    {budget ? <small>已读取 {budget.tool_calls} 次 · 模型判断 {budget.model_calls} 次</small> : null}
    {observations.map(o=><details key={o.id}><summary>{TOOLS[o.tool] ?? o.tool} · {o.status==="ok" ? "已取得观察" : "未知"}</summary>
      <p>{o.origin==="snapshot" ? "历史快照" : o.origin==="live" ? "实时环境" : "业务或知识来源"} · {o.observed_at}</p>
      {o.snapshot_id ? <small>快照：{o.snapshot_id}</small> : null}{o.knowledge_revision ? <small>知识版本：{o.knowledge_revision}</small> : null}
      <pre style={{whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>{JSON.stringify(o.data,null,2)}</pre>{o.error_code ? <p>{o.error_code}</p> : null}</details>)}
    {report ? <>{report.conclusions.length ? <><h3>有证据支持的原文</h3><ul>{report.conclusions.map((c,i)=><li key={i}>{c.text}</li>)}</ul></> : null}
      {report.hypotheses.length ? <><h3>尚未验证的假设</h3><ul>{report.hypotheses.map((c,i)=><li key={i}>{c.text}</li>)}</ul></> : null}
      {report.unknowns.length ? <><h3>证据缺口</h3><ul>{report.unknowns.map((c,i)=><li key={i}>{c}</li>)}</ul></> : null}
      {report.conflicts.length ? <><h3>需核对的差异</h3><ul>{report.conflicts.map((c,i)=><li key={i}>{c}</li>)}</ul></> : null}
      <h3>建议下一步</h3><ul>{report.next_steps.map((c,i)=><li key={i}>{c}</li>)}</ul></> : null}
  </section>;
}
