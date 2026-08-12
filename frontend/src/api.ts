import type {
  ChatEvent,
  MessageFeedback,
  TicketCreateResult,
  TicketDraft,
} from "./types";

const CHAT_EVENT_NAMES = new Set([
  "run_started",
  "node_completed",
  "citations",
  "ticket_draft",
  "handoff",
  "final",
]);

interface ApiProblem {
  message?: string;
  detail?: string | { message?: string };
}

function eventFromBlock(block: string): ChatEvent | null {
  let eventName = "";
  const dataLines: string[] = [];

  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) eventName = line.slice(6).trim();
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }

  if (!eventName || !dataLines.length) return null;
  if (!CHAT_EVENT_NAMES.has(eventName)) {
    console.warn(`Ignored unknown SSE event: ${eventName}`);
    return null;
  }

  const data = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
  return { type: eventName, ...data } as ChatEvent;
}

export async function* parseSseStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<ChatEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer = (buffer + decoder.decode(value, { stream: !done })).replace(/\r\n/g, "\n");

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const event = eventFromBlock(buffer.slice(0, boundary));
        buffer = buffer.slice(boundary + 2);
        if (event) yield event;
        boundary = buffer.indexOf("\n\n");
      }

      if (done) break;
    }

    const event = eventFromBlock(buffer.trim());
    if (event) yield event;
  } finally {
    reader.releaseLock();
  }
}

async function responseError(response: Response): Promise<Error> {
  let message = `请求失败（${response.status}）`;
  try {
    const problem = (await response.json()) as ApiProblem;
    if (typeof problem.detail === "string") message = problem.detail;
    else if (problem.detail?.message) message = problem.detail.message;
    else if (problem.message) message = problem.message;
  } catch {
    // Preserve the status-based fallback for non-JSON failures.
  }
  return new Error(message);
}

export async function* streamMessage(
  conversationId: string,
  userId: string,
  content: string,
  signal?: AbortSignal,
): AsyncGenerator<ChatEvent> {
  const response = await fetch(
    `/api/conversations/${encodeURIComponent(conversationId)}/messages:stream`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ user_id: userId, content }),
      signal,
    },
  );

  if (!response.ok) throw await responseError(response);
  if (!response.body) throw new Error("服务器未返回消息流。");
  yield* parseSseStream(response.body);
}

export async function confirmTicket(input: {
  conversationId: string;
  userId: string;
  confirmationToken: string;
  draft: TicketDraft;
  idempotencyKey: string;
  traceId: string;
}): Promise<TicketCreateResult> {
  const response = await fetch(
    `/api/conversations/${encodeURIComponent(input.conversationId)}/ticket-confirmations`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Trace-Id": input.traceId,
      },
      body: JSON.stringify({
        user_id: input.userId,
        confirmation_token: input.confirmationToken,
        draft: input.draft,
        idempotency_key: input.idempotencyKey,
      }),
    },
  );

  if (!response.ok) throw await responseError(response);
  return (await response.json()) as TicketCreateResult;
}

export async function recordFeedback(
  messageId: string,
  userId: string,
  feedback: MessageFeedback,
): Promise<void> {
  const response = await fetch(
    `/api/messages/${encodeURIComponent(messageId)}/feedback?user_id=${encodeURIComponent(userId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ feedback }),
    },
  );
  if (!response.ok) throw await responseError(response);
}
