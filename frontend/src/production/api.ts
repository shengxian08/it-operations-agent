import type { MessageFeedback, TicketCreateResult, TicketDraft } from "../types";
import type { Assignee, Conversation, Cursor, DraftRecord, Escalation, EscalationDetail, IndexJob, IndexJobAccepted, KnowledgeArticle, KnowledgeDocument, KnowledgeJob, KnowledgeRevision, ManagedAccount, Message, Principal, Role, Run, RunEvent, Ticket } from "./types";

const PREFIX = "/api/v1";
let principal: Principal | null = null;
export function setSession(next: Principal | null) { principal = next; }

export class ApiError extends Error {
  constructor(message: string, public readonly status: number, public readonly code: string, public readonly traceId: string | null = null) { super(message); this.name = "ApiError"; }
}
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return `${error.message}${error.traceId ? `（参考编号：${error.traceId}）` : ""}`;
  return error instanceof Error ? error.message : "请求失败，请重试。";
}
export function isAbort(error: unknown) { return error instanceof DOMException && error.name === "AbortError"; }

function record(value: unknown): Record<string, unknown> { return value !== null && typeof value === "object" ? value as Record<string, unknown> : {}; }
async function failure(response: Response): Promise<ApiError> {
  let problem: Record<string, unknown> = {};
  try { const body: unknown = await response.json(); const outer = record(body); problem = typeof outer.detail === "object" ? record(outer.detail) : outer; if (typeof outer.detail === "string") { problem.message = outer.detail; if (/^[a-z_]+$/.test(outer.detail) && !problem.code) problem.code = outer.detail; } } catch { /* A gateway may return an HTML error. */ }
  const code = typeof problem.code === "string" ? problem.code : `http_${response.status}`;
  const labels: Record<string, string> = { message_too_large_or_empty: "消息为空或超过服务器允许的长度，请缩短后重试。", session_expired: "登录已过期，请重新登录。", authentication_required: "请先使用企业账号登录。", csrf_validation_failed: "登录校验已失效，请退出并重新登录。", conversation_run_active: "此会话已有请求正在处理，请等待完成或取消。", conversation_archived: "此会话已归档，请新建会话。", user_rate_limit: "请求过于频繁，请稍后重试。", run_queue_full: "处理队列已满，请稍后重试。", model_budget_exhausted: "本期模型额度已用完，请联系管理员。" };
  const message = labels[code] ?? (typeof problem.message === "string" ? problem.message : `请求失败（${response.status}），请重试。`);
  const trace = typeof problem.trace_id === "string" ? problem.trace_id : response.headers.get("X-Trace-Id");
  if (response.status === 401) { principal = null; window.dispatchEvent(new Event("session-expired")); }
  return new ApiError(message, response.status, code, trace);
}
async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set("Accept", "application/json");
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  if (options.method && options.method !== "GET" && principal) headers.set("X-CSRF-Token", principal.csrf_token);
  let response: Response;
  try { response = await fetch(`${PREFIX}${path}`, { ...options, headers, credentials: "same-origin" }); }
  catch (caught) { if (isAbort(caught)) throw caught; throw new ApiError("网络连接中断，请重试或恢复请求结果。", 0, "network_error"); }
  if (!response.ok) throw await failure(response);
  if (response.status === 204) return undefined as T;
  try { return await response.json() as T; }
  catch { throw new ApiError("服务器返回了无法解析的结果，请刷新或联系支持。", response.status, "invalid_response", response.headers.get("X-Trace-Id")); }
}
function query(values: Record<string, string | undefined>) { const params = new URLSearchParams(); Object.entries(values).forEach(([key, value]) => { if (value) params.set(key, value); }); return params.size ? `?${params}` : ""; }
const id = encodeURIComponent;
const json = (body: unknown): string => JSON.stringify(body);

export const api = {
  me: (signal?: AbortSignal) => request<Principal>("/me", { signal }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  conversations: (cursor?: string, signal?: AbortSignal) => request<{ conversations: Conversation[] } & Cursor>(`/conversations${query({ cursor })}`, { signal }),
  createConversation: (title?: string) => request<Conversation>("/conversations", { method: "POST", body: json({ title }) }),
  archiveConversation: (conversationId: string) => request<Conversation>(`/conversations/${id(conversationId)}/archive`, { method: "POST" }),
  messages: (conversationId: string, cursor?: string, signal?: AbortSignal) => request<{ messages: Message[] } & Cursor>(`/conversations/${id(conversationId)}/messages${query({ cursor })}`, { signal }),
  runs: (conversationId: string, signal?: AbortSignal) => request<{ runs: Run[] } & Cursor>(`/conversations/${id(conversationId)}/runs`, { signal }),
  startRun: (conversationId: string, content: string, clientMessageId: string, key: string) => request<Run>(`/conversations/${id(conversationId)}/runs`, { method: "POST", headers: { "Idempotency-Key": key }, body: json({ content, client_message_id: clientMessageId }) }),
  run: (runId: string, signal?: AbortSignal) => request<Run>(`/runs/${id(runId)}`, { signal }),
  cancelRun: (runId: string) => request<Run>(`/runs/${id(runId)}/cancel`, { method: "POST" }),
  feedback: (messageId: string, feedback: MessageFeedback) => request<void>(`/messages/${id(messageId)}/feedback`, { method: "POST", body: json({ feedback }) }),
  drafts: (conversationId: string, signal?: AbortSignal) => request<{ drafts: DraftRecord[] }>(`/ticket-drafts${query({ conversation_id: conversationId })}`, { signal }),
  updateDraft: (draftId: string, version: number, draft: TicketDraft) => request<DraftRecord>(`/ticket-drafts/${id(draftId)}`, { method: "PATCH", body: json({ version, draft }) }),
  confirmDraft: (draft: DraftRecord, key: string) => request<TicketCreateResult>(`/ticket-drafts/${id(draft.id)}/confirm`, { method: "POST", headers: { "Idempotency-Key": key }, body: json({ version: draft.version, confirmation_token: draft.confirmation_token }) }),
  tickets: (cursor?: string, signal?: AbortSignal) => request<{ tickets: Ticket[] } & Cursor>(`/tickets${query({ cursor })}`, { signal }),
  ticket: (number: string, signal?: AbortSignal) => request<Ticket>(`/tickets/${id(number)}`, { signal }),
  updateTicket: (number: string, version: number, changes: { status?: string; assignee_id?: string | null }) => request<Ticket>(`/tickets/${id(number)}`, { method: "PATCH", body: json({ version, ...changes }) }),
  comment: (number: string, version: number, content: string, visibility: "public" | "internal" = "public") => request<Ticket>(`/tickets/${id(number)}/comments`, { method: "POST", body: json({ version, content, visibility }) }),
  classifyComment: (number: string, commentId: string, version: number, visibility: "public" | "internal") => request(`/tickets/${id(number)}/comments/${id(commentId)}`, { method: "PATCH", body: json({ version, visibility }) }),
  escalations: (cursor?: string, signal?: AbortSignal) => request<{ escalations: Escalation[] } & Cursor>(`/escalations${query({ cursor })}`, { signal }),
  escalation: (identifier: string, auditCursor?: string, signal?: AbortSignal) => request<EscalationDetail>(`/escalations/${id(identifier)}${query({ audit_cursor: auditCursor })}`, { signal }),
  updateEscalation: (escalation: Escalation, status: string, assignee_id: string | null) => request<Escalation>(`/escalations/${id(escalation.id)}`, { method: "PATCH", body: json({ version: escalation.version, status, assignee_id }) }),
  assignees: (signal?: AbortSignal) => request<{ users: Assignee[] }>("/support/assignees", { signal }),
  accounts: (cursor?: string, signal?: AbortSignal) => request<{ items: ManagedAccount[] } & Cursor>(`/admin/accounts${query({ cursor })}`, { signal }),
  updateAccount: (accountId: string, version: number, role: Role, enabled: boolean) => request<ManagedAccount>(`/admin/accounts/${id(accountId)}`, { method: "PATCH", body: json({ role, enabled, expected_version: version }) }),
  documents: (cursor?: string, signal?: AbortSignal) => request<{ documents: KnowledgeDocument[] } & Cursor>(`/knowledge/documents${query({ cursor })}`, { signal }),
  article: (documentId: string, signal?: AbortSignal, indexRevision?: string) => request<KnowledgeArticle>(`/knowledge/documents/${id(documentId)}${query({ index_revision: indexRevision })}`, { signal }),
  upload: (file: File, title: string, access: string) => { const form = new FormData(); form.append("file", file); form.append("title", title); form.append("access_level", access); return request<KnowledgeJob>("/knowledge/uploads", { method: "POST", body: form }); },
  jobs: (cursor?: string, signal?: AbortSignal) => request<{ jobs: KnowledgeJob[] } & Cursor>(`/knowledge/jobs${query({ cursor })}`, { signal }),
  job: (jobId: string, signal?: AbortSignal) => request<KnowledgeJob>(`/knowledge/jobs/${id(jobId)}`, { signal }),
  publish: (jobId: string) => request<IndexJobAccepted>(`/knowledge/jobs/${id(jobId)}/publish`, { method: "POST" }),
  deactivate: (documentId: string) => request<IndexJobAccepted>(`/knowledge/documents/${id(documentId)}/deactivate`, { method: "POST" }),
  indexJob: (jobId: string, signal?: AbortSignal) => request<IndexJob>(`/admin/knowledge/index-jobs/${id(jobId)}`, { signal }),
  revisions: (signal?: AbortSignal) => request<{ revisions: KnowledgeRevision[] }>("/knowledge/revisions", { signal }),
  activate: (revisionId: string) => request<{ revision_id: string; status: string }>(`/knowledge/revisions/${id(revisionId)}/activate`, { method: "POST" }),
};

const EVENT_NAMES = new Set(["run_started", "node_completed", "citations", "ticket_draft", "handoff", "final"]);
function parseEvent(block: string): RunEvent | null {
  let name = "", sequence = 0; const lines: string[] = [];
  for (const line of block.split("\n")) { if (line.startsWith("event:")) name = line.slice(6).trim(); if (line.startsWith("id:")) sequence = Number(line.slice(3).trim()); if (line.startsWith("data:")) lines.push(line.slice(5).trimStart()); }
  if (!EVENT_NAMES.has(name) || !lines.length) return null;
  if (!Number.isSafeInteger(sequence) || sequence <= 0) throw new ApiError("消息流缺少有效序号，请恢复运行。", 0, "invalid_event_sequence");
  try { return { ...record(JSON.parse(lines.join("\n"))), type: name, sequence } as RunEvent; }
  catch { throw new ApiError("消息流格式异常，请恢复运行。", 0, "invalid_event_payload"); }
}
export async function* parseRunEvents(stream: ReadableStream<Uint8Array>): AsyncGenerator<RunEvent> {
  const reader = stream.getReader(); const decoder = new TextDecoder(); let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read(); buffer += decoder.decode(value, { stream: !done });
      // Normalize only completed CRLF pairs: a CR may be split across network chunks.
      buffer = buffer.replace(/\r\n/g, "\n");
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) { const event = parseEvent(buffer.slice(0, boundary)); buffer = buffer.slice(boundary + 2); if (event) yield event; boundary = buffer.indexOf("\n\n"); }
      if (done) break;
    }
    if (buffer.trim()) { const event = parseEvent(buffer); if (event) yield event; }
  } finally { await reader.cancel().catch(() => undefined); reader.releaseLock(); }
}
export async function* streamRun(runId: string, after: number, signal: AbortSignal): AsyncGenerator<RunEvent> {
  let response: Response;
  try { response = await fetch(`${PREFIX}/runs/${id(runId)}/events?after=${after}`, { credentials: "same-origin", headers: { Accept: "text/event-stream" }, signal }); }
  catch (caught) { if (isAbort(caught)) throw caught; throw new ApiError("消息连接中断，请恢复运行。", 0, "network_error"); }
  if (!response.ok) throw await failure(response);
  if (!response.body) throw new ApiError("服务器没有返回运行消息流。", 0, "empty_stream");
  yield* parseRunEvents(response.body);
}
