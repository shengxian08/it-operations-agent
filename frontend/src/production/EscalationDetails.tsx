import { useEffect, useRef, useState } from "react";
import { api, isAbort } from "./api";
import { dateTime, ErrorNotice, STATUS_LABELS } from "./shared";
import type { AuditEntry, EscalationDetail } from "./types";

function AuditChange({ entry, field, label }: { entry: AuditEntry; field: string; label: string }) {
  const value = entry.details[field];
  if (!value || typeof value !== "object" || !("from" in value) || !("to" in value)) return null;
  const display = (item: unknown) => item === null ? "未设置" : typeof item === "number" ? String(item) : typeof item === "string" ? (field === "status" ? STATUS_LABELS[item] ?? item : item) : "未知";
  return <p>{label}：{display(value.from)} → {display(value.to)}</p>;
}

export function EscalationDetails({ id, version, onOpenSource }: { id: string; version: number; onOpenSource: (id: string, revision?: string) => void }) {
  const [detail, setDetail] = useState<EscalationDetail | null>(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState<unknown>(null), [refresh, setRefresh] = useState(0);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    const request = new AbortController(); controller.current = request;
    setLoading(true); setError(null); setDetail(null);
    api.escalation(id, undefined, request.signal).then((value) => { if (!request.signal.aborted) setDetail(value); })
      .catch((caught: unknown) => { if (!isAbort(caught)) setError(caught); })
      .finally(() => { if (!request.signal.aborted) setLoading(false); });
    return () => request.abort();
  }, [id, version, refresh]);
  async function more() {
    const request = controller.current;
    if (!detail?.next_audit_cursor || loading || !request) return;
    setLoading(true); setError(null);
    try {
      const next = await api.escalation(id, detail.next_audit_cursor, request.signal);
      if (!request.signal.aborted) setDetail((current) => ({ ...next, audit: [...(current?.audit ?? []), ...next.audit.filter((item) => !current?.audit.some((prior) => prior.id === item.id))] }));
    } catch (caught) { if (!isAbort(caught)) { setError(caught); setDetail(null); } }
    finally { if (!request.signal.aborted) setLoading(false); }
  }
  const context = detail?.context;
  return <section className="handoff-details" aria-label="交接上下文与处理记录">
    <button className="secondary-button" disabled={loading} onClick={() => setRefresh((value) => value + 1)}>刷新交接明细</button>
    <ErrorNotice error={error} onRetry={() => setRefresh((value) => value + 1)} />
    {loading ? <p role="status">正在读取交接明细…</p> : null}
    {detail ? <>
      <h4>故障上下文</h4>
      {context ? <>
        <small>记录时间：{dateTime(context.captured_at)}</small>
        <dl className="intake-facts"><dt>故障现象</dt><dd>{context.problem ?? "未提供独立故障字段，请核对员工原文。"}</dd><dt>影响范围</dt><dd>{context.impact ?? "未提供"}</dd><dt>已尝试操作</dt><dd>{context.attempted_steps === null ? "未提供" : context.attempted_steps.length ? <ul>{context.attempted_steps.map((step, index) => <li key={index}>{step}</li>)}</ul> : "员工明确尚未尝试"}</dd></dl>
        {context.source ? <small>字段来源运行：{context.source.run_id}{context.source.draft_id ? ` · 草稿 ${context.source.draft_id} / V${context.source.draft_version}` : ""}</small> : null}
        <h4>员工原文</h4>
        {context.messages.map((message) => <div className="handoff-message" key={message.id}><p>{message.content ?? "原文超过 2000 字，未纳入摘要；请向员工核对简要故障信息。"}</p><small>来源消息：{message.id}</small></div>)}
        {context.messages_omitted ? <p className="field-help">更早的员工消息未纳入本次有限摘要。</p> : null}
        <h4>可访问的资料引用</h4>
        {context.citations.map((citation) => <div className="handoff-source" key={`${citation.document_id}-${citation.index_revision}-${citation.chunk_index}`}><button className="secondary-button" onClick={() => onOpenSource(citation.document_id, citation.index_revision ?? undefined)}>{citation.source_title}{citation.page_number ? ` · 第 ${citation.page_number} 页` : ""}</button><p>{citation.excerpt ?? "内容较长，请打开原文核对完整条件。"}</p><small>引用来源消息：{citation.source_message_id}</small></div>)}
        {context.citations_unavailable || context.citations_omitted ? <p className="field-help">部分引用当前不可访问或未纳入摘要，请核对可访问的原文。</p> : null}
        {!context.citations.length ? <p>当前没有可展示的资料引用。</p> : null}
      </> : <p>历史记录未保存故障上下文。</p>}
      <h4>处理记录</h4>
      {detail.audit.map((entry) => <article className="handoff-audit" key={entry.id}><strong>{entry.event_type === "created" ? "请求已记录" : "处理信息已更新"}</strong><p>操作者：{entry.actor_id} · {dateTime(entry.created_at)}</p><AuditChange entry={entry} field="status" label="状态" /><AuditChange entry={entry} field="assignee_id" label="处理人" /><AuditChange entry={entry} field="version" label="版本" /></article>)}
      {!detail.audit.length ? <p>暂无可读取的处理记录。</p> : null}
      {detail.next_audit_cursor ? <button className="secondary-button" disabled={loading} onClick={more}>加载更早的处理记录</button> : null}
    </> : null}
  </section>;
}
