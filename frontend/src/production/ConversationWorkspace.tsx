import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { CitationList } from "../components/CitationList";
import { RunTimeline } from "../components/RunTimeline";
import { api, ApiError, isAbort, streamRun } from "./api";
import { DraftEditor } from "./DraftEditor";
import { AnswerFeedback } from "./AnswerFeedback";
import { HandoffNotice } from "./HandoffNotice";
import { TicketLookupNotice } from "./TicketLookupNotice";
import { TicketIntakeNotice } from "./TicketIntakeNotice";
import { applyRunEvent, emptyRunView, isActiveRun, recoverRun } from "./runState";
import { readStored, removeStored, storeValue } from "./storage";
import { ErrorNotice, StatusBadge } from "./shared";
import type { Conversation, DraftRecord, Message, Principal, Run, RunView } from "./types";

interface PendingMessage { content: string; clientMessageId: string; key: string }
interface ConversationState { messages: Message[]; nextCursor: string | null; drafts: DraftRecord[]; run: RunView | null; input: string; loading: boolean; submitting: boolean; pending: PendingMessage | null; error: unknown }
function emptyConversation(): ConversationState { return { messages: [], nextCursor: null, drafts: [], run: null, input: "", loading: false, submitting: false, pending: null, error: null }; }
const OUTCOMES: Record<string, string> = { answered: "已根据知识资料回答", ticket_status: "已查询工单", ticket_lookup_clarification: "请选择要查询的工单", ticket_lookup_cancelled: "已取消工单查询", ticket_collection: "请补充工单信息", ticket_collection_cancelled: "已取消工单信息收集", awaiting_confirmation: "请核对并确认工单草稿", handoff: "需要人工支持" };

export function ConversationWorkspace({ principal, onOpenSource }: { principal: Principal; onOpenSource: (id: string, indexRevision?: string) => void }) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationCursor, setConversationCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [states, setStates] = useState<Record<string, ConversationState>>({});
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<unknown>(null);
  const [listRefresh, setListRefresh] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [creating, setCreating] = useState(false);
  const [archiving, setArchiving] = useState(false);
  const streams = useRef(new Map<string, AbortController>());
  const sendLocks = useRef(new Set<string>());
  const alive = useRef(true);
  const stateRef = useRef(states); stateRef.current = states;
  const patch = useCallback((conversationId: string, update: (state: ConversationState) => ConversationState) => { if (alive.current) setStates((current) => ({ ...current, [conversationId]: update(current[conversationId] ?? emptyConversation()) })); }, []);
  const pendingKey = useCallback((conversationId: string) => `${principal.id}:pending-run:${conversationId}`, [principal.id]);

  useEffect(() => { alive.current = true; return () => { alive.current = false; for (const controller of streams.current.values()) controller.abort(); streams.current.clear(); }; }, []);
  useEffect(() => {
    const controller = new AbortController(); setListLoading(true); setListError(null);
    api.conversations(undefined, controller.signal).then((result) => {
      if (controller.signal.aborted) return; setConversations(result.conversations); setConversationCursor(result.next_cursor);
      setSelected((current) => { if (current && result.conversations.some((item) => item.id === current)) return current; const stored = readStored<string>(`${principal.id}:selected`); return result.conversations.find((item) => item.id === stored)?.id ?? result.conversations.find((item) => item.status === "active")?.id ?? result.conversations[0]?.id ?? null; });
    }).catch((caught: unknown) => { if (!isAbort(caught)) setListError(caught); }).finally(() => { if (!controller.signal.aborted) setListLoading(false); });
    return () => controller.abort();
  }, [principal.id, listRefresh]);

  const refreshDrafts = useCallback(async (conversationId: string) => {
    try { const result = await api.drafts(conversationId); patch(conversationId, (state) => ({ ...state, drafts: result.drafts })); }
    catch (caught) { if (!isAbort(caught)) patch(conversationId, (state) => ({ ...state, error: caught })); }
  }, [patch]);
  const projectRun = useCallback((conversationId: string, view: RunView) => {
    patch(conversationId, (state) => {
      // Every callback carries its original conversation id, including late stream frames.
      const final = view.final; const messageId = final?.message_id ?? `${view.id}-final`;
      const messages = final?.answer && !state.messages.some((message) => message.id === messageId) ? [...state.messages, { id: messageId, role: "assistant" as const, content: final.answer, citations: view.citations }] : state.messages;
      return { ...state, run: !state.run || state.run.id === view.id ? view : state.run, messages };
    });
  }, [patch]);
  const watchRun = useCallback(async (conversationId: string, run: Run) => {
    if (streams.current.has(run.id) || !alive.current) return;
    const controller = new AbortController(); streams.current.set(run.id, controller);
    let view = recoverRun(emptyRunView(run.id), run); projectRun(conversationId, view);
    try {
      if (isActiveRun(view.status)) {
        for await (const event of streamRun(run.id, view.sequence, controller.signal)) {
          if (controller.signal.aborted || !alive.current) return;
          view = applyRunEvent(view, event); projectRun(conversationId, view);
          if (event.type === "ticket_draft") void refreshDrafts(conversationId);
        }
      }
      // Stream closure alone never means success. Reconcile with the durable result.
      const recovered = await api.run(run.id, controller.signal); view = recoverRun(view, recovered);
      if (isActiveRun(view.status)) view = { ...view, problem: "连接已中断，后台仍在处理。点击恢复运行继续接收结果。" };
      projectRun(conversationId, view); await refreshDrafts(conversationId);
    } catch (caught) {
      if (isAbort(caught) || !alive.current) return;
      try {
        view = recoverRun(view, await api.run(run.id, controller.signal));
        if (isActiveRun(view.status)) view = { ...view, problem: "连接已中断，后台仍在处理。点击恢复运行继续接收结果。" };
        projectRun(conversationId, view); await refreshDrafts(conversationId);
      } catch (recoveryError) { if (!isAbort(recoveryError)) patch(conversationId, (state) => state.run?.id && state.run.id !== view.id ? state : ({ ...state, error: recoveryError, run: { ...view, problem: "暂时无法恢复结果，请重试恢复运行。" } })); }
    } finally { if (streams.current.get(run.id) === controller) streams.current.delete(run.id); }
  }, [patch, projectRun, refreshDrafts]);

  useEffect(() => {
    if (!selected) return; const conversationId = selected; const controller = new AbortController(); storeValue(`${principal.id}:selected`, conversationId);
    patch(conversationId, (state) => ({ ...state, loading: true, error: null, pending: state.pending ?? readStored<PendingMessage>(pendingKey(conversationId)) }));
    Promise.all([api.messages(conversationId, undefined, controller.signal), api.runs(conversationId, controller.signal), api.drafts(conversationId, controller.signal)]).then(([history, runs, drafts]) => {
      if (controller.signal.aborted) return;
      patch(conversationId, (state) => ({ ...state, messages: history.messages, nextCursor: history.next_cursor, drafts: drafts.drafts, loading: false }));
      const ordered = [...runs.runs].sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? "")); const latest = ordered[0];
      if (latest) {
        patch(conversationId, (state) => ({ ...state, run: recoverRun(emptyRunView(latest.id), latest) }));
        api.run(latest.id, controller.signal).then((run) => { if (controller.signal.aborted) return; if (!streams.current.has(run.id)) { projectRun(conversationId, recoverRun(emptyRunView(run.id), run)); if (isActiveRun(run.status)) void watchRun(conversationId, run); } }).catch((caught: unknown) => { if (!isAbort(caught)) patch(conversationId, (state) => ({ ...state, error: caught })); });
      }
    }).catch((caught: unknown) => { if (!isAbort(caught)) patch(conversationId, (state) => ({ ...state, error: caught, loading: false })); });
    return () => controller.abort();
  }, [selected, refresh, principal.id, patch, pendingKey, projectRun, watchRun]);

  async function createConversation() {
    if (creating) return; setCreating(true); setListError(null);
    try { const conversation = await api.createConversation(); if (!alive.current) return; setConversations((current) => [conversation, ...current]); setSelected(conversation.id); }
    catch (caught) { setListError(caught); } finally { setCreating(false); }
  }
  async function archive() {
    if (!selected || archiving) return; const conversationId = selected; setArchiving(true); setListError(null);
    try { await api.archiveConversation(conversationId); setConversations((current) => current.map((item) => item.id === conversationId ? { ...item, status: "archived" } : item)); }
    catch (caught) { setListError(caught); } finally { setArchiving(false); }
  }
  async function moreConversations() {
    if (!conversationCursor || listLoading) return; setListLoading(true); setListError(null);
    try { const result = await api.conversations(conversationCursor); setConversations((current) => [...current, ...result.conversations.filter((item) => !current.some((existing) => existing.id === item.id))]); setConversationCursor(result.next_cursor); }
    catch (caught) { setListError(caught); } finally { setListLoading(false); }
  }
  async function send(conversationId: string, retry = false, contentOverride?: string) {
    const state = stateRef.current[conversationId] ?? emptyConversation(); if (sendLocks.current.has(conversationId)) return;
    const pending = retry ? state.pending : { content: (contentOverride ?? state.input).trim(), clientMessageId: crypto.randomUUID(), key: crypto.randomUUID() };
    if (!pending?.content || (!retry && (state.pending || isActiveRun(state.run?.status ?? "")))) return;
    sendLocks.current.add(conversationId); storeValue(pendingKey(conversationId), pending);
    patch(conversationId, (current) => ({ ...current, pending, submitting: true, error: null, input: retry || contentOverride !== undefined ? current.input : "", messages: current.messages.some((message) => message.id === pending.clientMessageId) ? current.messages : [...current.messages, { id: pending.clientMessageId, role: "user", content: pending.content }] }));
    try {
      const run = await api.startRun(conversationId, pending.content, pending.clientMessageId, pending.key); removeStored(pendingKey(conversationId));
      if (alive.current) setConversations((current) => current.map((item) => item.id === conversationId && item.title === "新会话" ? { ...item, title: pending.content.slice(0, 60) } : item));
      patch(conversationId, (current) => ({ ...current, pending: null, run: recoverRun(emptyRunView(run.id), run) })); void watchRun(conversationId, run);
    } catch (caught) {
      const knownRejection = caught instanceof ApiError && caught.status >= 400 && caught.status < 500;
      if (knownRejection) removeStored(pendingKey(conversationId));
      patch(conversationId, (current) => ({ ...current, error: caught, pending: knownRejection ? null : pending, input: knownRejection && contentOverride === undefined ? pending.content : current.input, messages: knownRejection ? current.messages.filter((message) => message.id !== pending.clientMessageId) : current.messages }));
    } finally { sendLocks.current.delete(conversationId); patch(conversationId, (current) => ({ ...current, submitting: false })); }
  }
  async function restore(conversationId: string, runId: string) {
    patch(conversationId, (state) => ({ ...state, error: null }));
    try { const run = await api.run(runId); projectRun(conversationId, recoverRun(emptyRunView(run.id), run)); if (isActiveRun(run.status)) void watchRun(conversationId, run); else await refreshDrafts(conversationId); }
    catch (caught) { patch(conversationId, (state) => ({ ...state, error: caught })); }
  }
  async function cancel(conversationId: string, runId: string) {
    patch(conversationId, (state) => ({ ...state, submitting: true, error: null }));
    try { const run = await api.cancelRun(runId); streams.current.get(runId)?.abort(); projectRun(conversationId, recoverRun(stateRef.current[conversationId]?.run ?? emptyRunView(runId), run)); }
    catch (caught) { patch(conversationId, (state) => ({ ...state, error: caught })); }
    finally { patch(conversationId, (state) => ({ ...state, submitting: false })); }
  }
  async function moreHistory(conversationId: string) {
    const cursor = stateRef.current[conversationId]?.nextCursor; if (!cursor) return;
    patch(conversationId, (state) => ({ ...state, loading: true, error: null }));
    try { const result = await api.messages(conversationId, cursor); patch(conversationId, (state) => ({ ...state, nextCursor: result.next_cursor, messages: [...result.messages.filter((message) => !state.messages.some((existing) => existing.id === message.id)), ...state.messages] })); }
    catch (caught) { patch(conversationId, (state) => ({ ...state, error: caught })); }
    finally { patch(conversationId, (state) => ({ ...state, loading: false })); }
  }
  const current = selected ? states[selected] ?? emptyConversation() : null; const conversation = conversations.find((item) => item.id === selected); const active = isActiveRun(current?.run?.status ?? "");
  function submit(event: FormEvent) { event.preventDefault(); if (selected) void send(selected); }
  return <div className="conversation-layout"><aside className="conversation-sidebar" aria-label="会话列表"><span className="section-kicker">YOUR WORKSPACE</span><h1>运维对话</h1><button className="primary-button" disabled={creating || listLoading} onClick={createConversation}>{creating ? "正在新建" : "新建会话"}</button><ErrorNotice error={listError} onRetry={() => setListRefresh((value) => value + 1)} />{listLoading ? <p role="status">正在加载会话…</p> : null}<div className="conversation-list">{conversations.map((item) => <button className={selected === item.id ? "conversation-item selected" : "conversation-item"} key={item.id} onClick={() => setSelected(item.id)} aria-pressed={selected === item.id}><strong>{item.title || "新会话"}</strong><small>{item.status === "archived" ? "已归档" : "进行中的会话"}</small></button>)}</div>{conversationCursor ? <button disabled={listLoading} onClick={moreConversations}>加载更多会话</button> : null}</aside>
    {!selected || !current ? <section className="workspace-empty"><span className="section-kicker">START HERE</span><h2>从一个具体问题开始</h2><p>新建会话，描述故障现象、影响范围和已尝试的操作。你可以随时返回历史会话继续处理。</p></section> : <section className="conversation-main" aria-label="当前会话"><div className="conversation-toolbar"><div><h2>{conversation?.title || "新会话"}</h2><small>会话 {selected.slice(0, 8)} · 当前身份：{principal.display_name}</small></div><div className="button-row"><button disabled={current.loading} onClick={() => setRefresh((value) => value + 1)}>刷新会话</button><button disabled={archiving || conversation?.status === "archived" || active || Boolean(current.pending)} onClick={archive}>{conversation?.status === "archived" ? "已归档" : "归档会话"}</button></div></div><ErrorNotice error={current.error} />
    <div className="work-grid"><section className="chat-panel" aria-label="助手对话"><div className="chat-heading"><div><span className="section-kicker">ASSISTANT</span><h2>问题与处理记录</h2></div>{current.run ? <StatusBadge status={current.run.status} /> : null}</div><div className="message-list" aria-live="polite">{current.nextCursor ? <button className="history-button" disabled={current.loading} onClick={() => moreHistory(selected)}>加载更早的消息</button> : null}{current.loading ? <p role="status">正在恢复历史记录…</p> : null}{!current.messages.length && !current.loading ? <div className="empty-chat"><h3>需要什么 IT 帮助？</h3><p>可以查询知识、跟进工单，或描述需要人工处理的故障。</p></div> : null}{current.messages.map((message) => <article key={message.id} className={`message message-${message.role}`}><span>{message.role === "user" ? "你" : "IT 助手"}</span><p>{message.content}</p>{message.citations?.length ? <div className="message-sources">{message.citations.map((citation) => <button key={`${citation.document_id}-${citation.chunk_index}`} onClick={() => onOpenSource(citation.document_id, citation.index_revision ?? undefined)}>{citation.source_title}</button>)}</div> : null}</article>)}{active ? <p className="thinking" role="status">后台正在处理，切换会话或刷新后可继续查看。</p> : null}</div>
    {current.pending ? <div className="unknown-request"><p role="status">消息受理结果暂时未知，请恢复原请求，避免重复发送。</p><button className="primary-button" disabled={current.submitting} onClick={() => send(selected, true)}>恢复发送结果并重试</button></div> : null}
    <form className="composer" onSubmit={submit}><label htmlFor="chat-input">输入 IT 问题</label><textarea id="chat-input" rows={3} maxLength={10000} disabled={conversation?.status === "archived"} value={current.input} onChange={(event) => { const value = event.target.value; patch(selected, (state) => ({ ...state, input: value })); }} placeholder="描述故障现象、影响范围和已尝试的操作…" /><div className="composer-footer"><span>{conversation?.status === "archived" ? "此会话已归档" : `${current.input.length.toLocaleString()} / 10,000`}</span><button disabled={current.loading || current.submitting || active || Boolean(current.pending) || conversation?.status === "archived" || !current.input.trim()}>{current.submitting ? "正在发送" : "发送请求"}</button></div></form></section>
    <aside className="results" aria-label="运行结果">{current.run ? <><section className="result-card outcome-card"><span className="section-kicker">本次结果</span><h2>{current.run.final ? OUTCOMES[current.run.final.final_state] ?? (current.run.status === "failed" ? "处理未完成" : "处理已结束") : active ? "后台正在处理" : current.run.status === "cancelled" ? "请求已取消" : "等待恢复结果"}</h2>{current.run.final?.error ? <ErrorNotice error={current.run.final.error} /> : null}<ErrorNotice error={current.run.problem} /><div className="button-row">{active ? <button disabled={current.submitting} onClick={() => cancel(selected, current.run!.id)}>取消运行</button> : null}{current.run.problem ? <button onClick={() => restore(selected, current.run!.id)}>恢复运行</button> : null}</div></section>{current.run.final?.final_state === "ticket_lookup_clarification" && current.run.final.ticket_lookup?.user_id === principal.id && current.run.final.ticket_lookup.conversation_id === selected ? <TicketLookupNotice lookup={current.run.final.ticket_lookup} disabled={current.loading || current.submitting || active || Boolean(current.pending) || conversation?.status === "archived"} onSelect={(number) => { void send(selected, false, number); }} /> : null}<CitationList citations={current.run.citations} onOpenSource={onOpenSource} />{current.run.handoff || current.run.final?.final_state === "handoff" ? <HandoffNotice escalationId={current.run.escalationId} /> : null}<RunTimeline runId={current.run.id} steps={current.run.steps} active={active} finalState={current.run.final?.final_state ?? null} /></> : <div className="result-placeholder"><span>RESULT</span><p>发送请求后，可在这里查看处理结果、知识引用与待确认草稿。</p></div>}
    {current.run?.final?.final_state === "ticket_collection" && current.run.final.ticket_intake?.user_id === principal.id && current.run.final.ticket_intake.conversation_id === selected ? <TicketIntakeNotice intake={current.run.final.ticket_intake} /> : null}
    {current.drafts.filter((draft) => draft.status === "pending").map((draft) => <DraftEditor key={`${draft.id}-${draft.version}`} record={draft} userId={principal.id} onSaved={(saved) => patch(selected, (state) => ({ ...state, drafts: state.drafts.map((item) => item.id === saved.id ? saved : item) }))} onCreated={(result) => patch(selected, (state) => ({ ...state, drafts: state.drafts.map((item) => item.id === draft.id ? { ...item, status: "confirmed" } : item), messages: [...state.messages, { id: `ticket-${result.ticket_number}`, role: "assistant", content: `工单 ${result.ticket_number} 已创建，可在“我的工单”查看处理进展。` }] }))} onRefresh={() => { void refreshDrafts(selected); }} />)}
    {current.drafts.filter((draft) => draft.status === "confirmed" && draft.ticket_number).map((draft) => <section className="result-card" key={`confirmed-${draft.id}`}><span className="section-kicker">TICKET CREATED</span><h2>工单已创建</h2><p>工单 <strong>{draft.ticket_number}</strong> 已提交，可在工单记录中跟进。</p></section>)}
    {current.run?.final?.message_id && current.run.status === "completed" ? <AnswerFeedback key={current.run.final.message_id} messageId={current.run.final.message_id} /> : null}
    </aside></div></section>}
  </div>;
}
