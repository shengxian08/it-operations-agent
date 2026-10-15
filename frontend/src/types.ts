export type TicketPriority = "low" | "medium" | "high" | "critical";

export interface TicketDraft {
  title: string;
  category: string;
  priority: TicketPriority;
  description: string;
  attempted_steps: string[];
  problem?: string | null;
  impact?: string | null;
  intake_version?: 1 | null;
}

export interface TicketIntake {
  schema_version: 1;
  user_id: string;
  conversation_id: string;
  outcome: "collecting" | "ready" | "cancelled";
  problem: string | null;
  impact: string | null;
  attempted_steps: string[] | null;
  next_field: "problem" | "impact" | "attempted_steps" | null;
  reason: "invalid_field" | "field_too_long" | null;
}

export interface Citation {
  document_id: string;
  source_title: string;
  source_path: string;
  chunk_index: number;
  excerpt: string;
  page_number?: number | null;
  table_id?: string | null;
  row_index?: number | null;
  index_revision?: string | null;
  section_path?: string[];
  char_start?: number | null;
  char_end?: number | null;
}

interface EventBase {
  trace_id: string;
}

export interface AgentObservation {
  id: string; tool: string; status: "ok" | "unknown"; origin: "snapshot" | "live" | "knowledge" | "business";
  observed_at: string; snapshot_id?: string | null; knowledge_revision?: string | null; resource?: string | null;
  data: Record<string, unknown>; error_code?: string | null;
}
export interface AgentBudget { tool_calls: number; model_calls: number; reserved_token_upper_bound: number; observed_tokens: number; cancelled: boolean }
export interface InvestigationReport {
  goal: string; status: "partial" | "completed"; strategy: "single" | "multi"; observations: AgentObservation[];
  conclusions: Array<{text: string; evidence_ids: string[]; verification: string}>;
  hypotheses: Array<{text: string; evidence_ids: string[]; verification: string}>;
  unknowns: string[]; conflicts: string[]; next_steps: string[]; budget: AgentBudget;
}

export interface TicketLookup {
  schema_version: 1;
  user_id: string;
  conversation_id: string;
  outcome: "found" | "not_found" | "unavailable" | "clarification" | "cancelled";
  ticket_number: string | null;
  basis: "explicit" | "conversation" | "clarification_selection" | null;
  reason: "missing_ticket_number" | "ambiguous_ticket_number" | "too_many_candidates" | null;
  candidates: string[];
}

export type ChatEvent =
  | (EventBase & { type: "agent_progress"; agent?: string; stage?: string; observation?: AgentObservation; budget?: AgentBudget; access_redacted?: boolean })
  | (EventBase & {
      type: "run_started";
      run_id: string;
      message_id: string;
    })
  | (EventBase & {
      type: "node_completed";
      node: string;
      step_count: number;
    })
  | (EventBase & {
      type: "citations";
      citations: Citation[];
    })
  | (EventBase & {
      type: "ticket_draft";
      draft: TicketDraft;
      confirmation_token: string;
    })
  | (EventBase & {
      type: "handoff";
      reason: string;
      escalation_id?: string;
    })
  | (EventBase & {
      type: "final";
      run_id: string;
      message_id: string | null;
      answer: string;
      final_state: string;
      ticket_lookup?: TicketLookup;
      ticket_intake?: TicketIntake;
      investigation_report?: InvestigationReport;
      agent_budget?: AgentBudget;
      escalation_id?: string;
    });

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
}

export interface RunStep {
  node: string;
  stepCount: number;
}

export interface TicketCreateResult {
  ticket_number: string;
  status: string;
}

export type MessageFeedback = "resolved" | "unresolved";

export interface KnowledgeDocument {
  id: string;
  title: string;
  source_path: string;
  version: string;
  chunk_count: number;
}

export interface KnowledgeArticle {
  id: string;
  title: string;
  source_path: string;
  version: string;
  sections: { heading: string; body: string }[];
}

export interface TicketSummary {
  ticket_number: string;
  title: string;
  category: string;
  priority: TicketPriority;
  status: string;
}
