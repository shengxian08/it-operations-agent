export type TicketPriority = "low" | "medium" | "high" | "critical";

export interface TicketDraft {
  title: string;
  category: string;
  priority: TicketPriority;
  description: string;
  attempted_steps: string[];
}

export interface Citation {
  document_id: string;
  source_title: string;
  source_path: string;
  chunk_index: number;
  excerpt: string;
}

interface EventBase {
  trace_id: string;
}

export type ChatEvent =
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
    })
  | (EventBase & {
      type: "final";
      run_id: string;
      message_id: string | null;
      answer: string;
      final_state: string;
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
