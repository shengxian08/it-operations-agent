import { useEffect, useRef, useState } from "react";
import type { TicketCreateResult, TicketDraft, TicketPriority } from "../types";
import { api, ApiError } from "./api";
import { confirmationKey, readStored, removeStored, storeValue } from "./storage";
import { dateTime, ErrorNotice } from "./shared";
import type { DraftRecord } from "./types";

export function DraftEditor({ record, userId, onSaved, onCreated, onRefresh }: { record: DraftRecord; userId: string; onSaved: (record: DraftRecord) => void; onCreated: (result: TicketCreateResult) => void; onRefresh: () => void }) {
  const [draft, setDraft] = useState<TicketDraft>(record.draft);
  const [attemptsChecked, setAttemptsChecked] = useState(record.draft.intake_version === 1);
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const storageKey = `${userId}:unknown-confirm:${record.id}:${record.version}`;
  const [unknown, setUnknown] = useState(() => Boolean(readStored<boolean>(storageKey)));
  const [result, setResult] = useState<TicketCreateResult | null>(null);
  const [blocked, setBlocked] = useState(false);
  const lock = useRef(false);
  useEffect(() => { setDraft(record.draft); setAttemptsChecked(record.draft.intake_version === 1); setChecked(false); setError(null); setBlocked(false); setUnknown(Boolean(readStored<boolean>(`${userId}:unknown-confirm:${record.id}:${record.version}`))); }, [record.id, record.version, record.draft, userId]);
  const dirty = JSON.stringify(draft) !== JSON.stringify(record.draft);
  const expired = new Date(record.expires_at).getTime() <= Date.now();
  const valid = Boolean(draft.title.trim() && draft.category.trim() && draft.problem && draft.problem.trim().length >= 2 && draft.problem.length <= 2000 && draft.impact && draft.impact.trim().length >= 2 && draft.impact.length <= 1000 && attemptsChecked && draft.attempted_steps.length <= 10 && draft.attempted_steps.every((step) => step.trim().length >= 2 && step.length <= 300));
  const requiresDetails = Boolean(record.requires_details || record.draft.intake_version !== 1);
  const canConfirm = !requiresDetails && Boolean(record.confirmation_token);
  function update<K extends keyof TicketDraft>(key: K, value: TicketDraft[K]) {
    setDraft((current) => {
      const changed = { ...current, [key]: value };
      if (key === "problem" || key === "impact") changed.description = `${changed.problem ?? ""}\n影响范围：${changed.impact ?? ""}`;
      return changed;
    }); setChecked(false);
  }
  async function save() {
    if (lock.current || !valid) return; lock.current = true; setBusy(true); setError(null);
    const problem = draft.problem!.trim(), impact = draft.impact!.trim();
    try { onSaved(await api.updateDraft(record.id, record.version, { ...draft, problem, impact, intake_version: 1, description: `${problem}\n影响范围：${impact}` })); setChecked(false); }
    catch (caught) { setError(caught); if (caught instanceof ApiError && (caught.status === 409 || caught.status === 410)) setBlocked(true); }
    finally { lock.current = false; setBusy(false); }
  }
  async function confirm() {
    if (lock.current || !canConfirm || (!unknown && (!valid || !checked || dirty || expired || blocked))) return; lock.current = true; setBusy(true); setError(null);
    try { const created = await api.confirmDraft(record, confirmationKey(userId, record.id, record.version)); setResult(created); setUnknown(false); removeStored(storageKey); onCreated(created); }
    catch (caught) {
      setError(caught);
      if (!(caught instanceof ApiError) || caught.status >= 500 || caught.status === 0) { setUnknown(true); storeValue(storageKey, true); }
      else if (caught.status === 409 || caught.status === 410) { setBlocked(true); setUnknown(false); removeStored(storageKey); }
    } finally { lock.current = false; setBusy(false); }
  }
  return <section className="result-card draft-card" aria-label={`工单草稿 ${record.version}`}><div className="card-title-row"><div><span className="section-kicker">TICKET DRAFT / V{record.version}</span><h2>确认工单草稿</h2></div><span className="draft-status">{result ? "已创建" : "待确认"}</span></div>
    {requiresDetails ? <p className="token-warning">此旧草稿缺少必填信息，请补全后保存并重新签名。</p> : null}
    <fieldset className="draft-grid" disabled={busy || unknown || Boolean(result)}>
      <label className="field-wide">草稿标题<input value={draft.title} maxLength={300} onChange={(event) => update("title", event.target.value)} /></label>
      <label className="field-wide">故障现象或错误信息<textarea rows={3} value={draft.problem ?? ""} maxLength={2000} onChange={(event) => update("problem", event.target.value)} /></label>
      <label className="field-wide">影响范围及工作阻断情况<textarea rows={2} value={draft.impact ?? ""} maxLength={1000} onChange={(event) => update("impact", event.target.value)} /></label>
      <label>草稿分类<input value={draft.category} maxLength={100} onChange={(event) => update("category", event.target.value)} /></label>
      <label>草稿优先级<select value={draft.priority} onChange={(event) => update("priority", event.target.value as TicketPriority)}><option value="low">低</option><option value="medium">中</option><option value="high">高</option><option value="critical">紧急</option></select></label>
      <p className="field-wide field-help">初始优先级依据故障和影响范围生成：安全事件或设备丢失为紧急，明确无法办公或业务中断等影响为高，其余为中。请按当前情况核对或修改。</p>
      <label className="field-wide">草稿问题描述<textarea rows={4} value={draft.description} readOnly /></label>
      <label className="field-wide">草稿已尝试步骤<textarea rows={2} value={draft.attempted_steps.join("\n")} onChange={(event) => update("attempted_steps", event.target.value.split("\n").map((step) => step.trim()).filter(Boolean))} /></label>
      <p className="field-wide field-help">每行一项，最多 10 项，每项 2–300 字。尚未尝试时请留空，并核对下方记录。</p>
      <label className="field-wide confirmation-check"><input type="checkbox" checked={attemptsChecked} onChange={(event) => { setAttemptsChecked(event.target.checked); setChecked(false); }} />已核对尝试记录，尚未尝试可留空</label>
    </fieldset>
    <ErrorNotice error={error} />
    {unknown ? <p className="token-warning" role="status">确认结果暂时未知。请使用下方按钮恢复结果；重试保留同一个请求编号。</p> : null}
    {dirty ? <p className="field-help">请先保存修改，再核对新版本并确认创建工单。</p> : null}
    {expired && !unknown && !result ? <p className="token-warning">确认凭证已过期，请保存草稿重新签名。</p> : null}
    {!result ? <div className="draft-actions"><button type="button" className="secondary-button" disabled={busy || unknown || !valid || (!dirty && !expired && !requiresDetails)} onClick={save}>保存修改并重新签名</button>{blocked ? <button type="button" onClick={onRefresh}>刷新草稿版本</button> : null}<small>确认有效期：{dateTime(record.expires_at)}</small><label className="confirmation-check"><input type="checkbox" checked={checked} disabled={busy || !valid || !canConfirm || dirty || expired || unknown || blocked} onChange={(event) => setChecked(event.target.checked)} />我已核对以上内容，并确认创建此工单</label><button className="confirm-button" disabled={busy || !canConfirm || (!unknown && (!valid || !checked || dirty || expired || blocked))} onClick={confirm}>{busy ? "正在提交" : unknown ? "查询确认结果并重试" : "确认并创建工单"}</button></div> : <p className="ticket-success" role="status">工单 <strong>{result.ticket_number}</strong> 已创建。</p>}
  </section>;
}
