import type { ChatEvent, ChatMessage, Citation, RunStep, TicketDraft, TicketPriority } from "../types";

export type Role = "employee" | "support" | "admin";
export interface ManagedAccount { id: string; display_name: string; role: Role; enabled: boolean; version: number }
export interface Principal { id: string; display_name: string; role: Role; csrf_token: string }
export interface Conversation { id: string; title: string; status: "active" | "archived"; created_at: string; updated_at: string }
export interface Message extends ChatMessage { citations?: Citation[]; created_at?: string }
export type RunStatus = "queued" | "running" | "completed" | "failed" | "cancelled";
export type FinalResult = Extract<ChatEvent, { type: "final" }> & { status?: RunStatus; error?: string };
export type RunEvent = (ChatEvent | (Extract<ChatEvent, { type: "ticket_draft" }> & { draft_id?: string; version?: number })) & { sequence: number };
export interface StoredEvent { sequence: number; type: ChatEvent["type"]; data: Record<string, unknown>; created_at?: string }
export interface Run { id: string; conversation_id?: string; status: RunStatus; trace_id: string; result: Omit<FinalResult, "type"> | null; events?: StoredEvent[]; created_at?: string; finished_at?: string }
export interface RunView { id: string; sequence: number; status: RunStatus; steps: RunStep[]; citations: Citation[]; final: FinalResult | null; handoff: string | null; problem: string | null; escalationId?: string | null }
export interface DraftRecord { id: string; draft_id?: string; conversation_id: string; run_id?: string | null; version: number; draft: TicketDraft; requires_details?: boolean; confirmation_token: string | null; ticket_number?: string | null; expires_at: string; status: string }
export interface Ticket { id: string; ticket_number: string; user_id: string; title: string; category: string; priority: TicketPriority; description: string; attempted_steps: string[]; status: string; version: number; assignee_id: string | null; created_at: string; updated_at: string; comments?: TicketComment[]; audit?: AuditEntry[] }
export interface TicketComment { id: string; author_id: string; content: string; visibility?: "public" | "internal" | "unclassified"; author_role?: string | null; created_at: string }
export interface AuditEntry { id: string; actor_id: string; event_type: string; details: Record<string, unknown>; created_at: string }
export interface Escalation { id: string; user_id: string; conversation_id: string; run_id: string; reason: string; status: string; version: number; assignee_id: string | null; created_at: string }
export interface EscalationContext {
  schema_version: 1; captured_at: string; problem: string | null; impact: string | null; attempted_steps: string[] | null;
  source: { run_id: string; draft_id: string | null; draft_version: number | null } | null;
  messages: Array<{ id: string; content: string | null; omission: "too_long" | null }>;
  messages_omitted: boolean; citations_omitted: boolean; citations_unavailable: number;
  citations: Array<Omit<Citation, "excerpt"> & { excerpt: string | null; excerpt_omitted: boolean; source_message_id: string }>;
}
export interface EscalationDetail extends Escalation { context: EscalationContext | null; audit: AuditEntry[]; next_audit_cursor: string | null }
export interface Assignee { id: string; display_name: string; role: Role }
export interface KnowledgeDocument { id: string; title: string; access_level: string; status: string; version: string | number; created_at: string; requires_reparse?: boolean; reparse_reason?: string | null }
export interface PdfTable { id: string; page_number: number; headers: string[]; rows: string[][] }
export interface MarkdownBlock { block_type: string; content: string; char_start: number; char_end: number; contains_code?: boolean }
export interface KnowledgeSection { heading: string; content: string; context?: string; tables?: PdfTable[]; blocks?: MarkdownBlock[]; section_path?: string[]; parser_version?: string; char_start?: number; char_end?: number }
export interface KnowledgeArticle extends KnowledgeDocument { content?: string; index_revision?: string; parser_version?: string | null; sections: KnowledgeSection[] }
export interface KnowledgeJob { id: string; status: string; title: string; access_level: string; document_id: string | null; created_at: string; content?: string; sections?: KnowledgeSection[]; error?: string | null; requires_reparse?: boolean }
export interface KnowledgeRevision { id: string; created_at: string; active: boolean; document_count: number }
export interface IndexJobAccepted { index_job_id: string; status: string }
export interface IndexJob { id: string; index_job_id?: string; kind?: string; status: "queued" | "running" | "completed" | "failed"; result: { revision_id: string; status: string; excluded_documents?: string[] } | null; error: string | null }
export interface Cursor { next_cursor: string | null }
