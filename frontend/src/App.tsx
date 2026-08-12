import { useRef, useState } from "react";

import { confirmTicket, recordFeedback, streamMessage } from "./api";
import { ChatPanel } from "./components/ChatPanel";
import { CitationList } from "./components/CitationList";
import { HandoffCard } from "./components/HandoffCard";
import { RunTimeline } from "./components/RunTimeline";
import { TicketDraftCard } from "./components/TicketDraftCard";
import type {
  ChatMessage,
  Citation,
  MessageFeedback,
  RunStep,
  TicketCreateResult,
  TicketDraft,
} from "./types";

const DEFAULT_USER_ID = "u-001";
const DEFAULT_CONVERSATION_ID = "c-001";

export function App() {
  const [userId, setUserId] = useState(DEFAULT_USER_ID);
  const [conversationId, setConversationId] = useState(DEFAULT_CONVERSATION_ID);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [steps, setSteps] = useState<RunStep[]>([]);
  const [finalState, setFinalState] = useState<string | null>(null);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [draft, setDraft] = useState<TicketDraft | null>(null);
  const [confirmationToken, setConfirmationToken] = useState<string | null>(null);
  const [confirmationTraceId, setConfirmationTraceId] = useState<string | null>(null);
  const [idempotencyKey, setIdempotencyKey] = useState<string | null>(null);
  const [ticketResult, setTicketResult] = useState<TicketCreateResult | null>(null);
  const [submittingTicket, setSubmittingTicket] = useState(false);
  const [handoff, setHandoff] = useState<{ reason: string; userInput: string } | null>(null);
  const [feedbackMessageId, setFeedbackMessageId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<MessageFeedback | null>(null);
  const [feedbackPending, setFeedbackPending] = useState(false);
  const latestRequest = useRef("");

  async function sendMessage() {
    const content = input.trim();
    const currentUserId = userId.trim();
    const currentConversationId = conversationId.trim();
    if (!content || !currentUserId || !currentConversationId || isStreaming) return;

    latestRequest.current = content;
    setInput("");
    setError(null);
    setRunId(null);
    setSteps([]);
    setFinalState(null);
    setCitations([]);
    setDraft(null);
    setConfirmationToken(null);
    setConfirmationTraceId(null);
    setIdempotencyKey(null);
    setTicketResult(null);
    setHandoff(null);
    setFeedbackMessageId(null);
    setFeedback(null);
    setIsStreaming(true);
    setMessages((current) => [
      ...current,
      { id: crypto.randomUUID(), role: "user", content },
    ]);

    try {
      for await (const event of streamMessage(currentConversationId, currentUserId, content)) {
        switch (event.type) {
          case "run_started":
            setRunId(event.run_id);
            break;
          case "node_completed":
            setSteps((current) => [
              ...current,
              { node: event.node, stepCount: event.step_count },
            ]);
            break;
          case "citations":
            setCitations(event.citations);
            break;
          case "ticket_draft":
            setDraft(event.draft);
            setConfirmationToken(event.confirmation_token);
            setConfirmationTraceId(event.trace_id);
            setIdempotencyKey(crypto.randomUUID());
            break;
          case "handoff":
            setHandoff({ reason: event.reason, userInput: content });
            break;
          case "final":
            setRunId(event.run_id);
            setFinalState(event.final_state);
            setMessages((current) => [
              ...current,
              {
                id: event.message_id ?? `${event.run_id}-final`,
                role: "assistant",
                content: event.answer,
              },
            ]);
            setFeedbackMessageId(event.message_id);
            break;
        }
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "消息处理失败，请稍后重试。");
    } finally {
      setIsStreaming(false);
    }
  }

  function editDraft(nextDraft: TicketDraft) {
    setDraft(nextDraft);
    setConfirmationToken(null);
    setConfirmationTraceId(null);
    setIdempotencyKey(null);
  }

  async function submitTicket() {
    if (
      !draft ||
      !confirmationToken ||
      !confirmationTraceId ||
      !idempotencyKey ||
      submittingTicket
    ) return;
    setSubmittingTicket(true);
    setError(null);
    try {
      const result = await confirmTicket({
        conversationId: conversationId.trim(),
        userId: userId.trim(),
        confirmationToken,
        draft,
        idempotencyKey,
        traceId: confirmationTraceId,
      });
      setTicketResult(result);
      setConfirmationToken(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "工单提交失败，请稍后重试。");
    } finally {
      setSubmittingTicket(false);
    }
  }

  async function submitFeedback(nextFeedback: MessageFeedback) {
    if (!feedbackMessageId || feedbackPending || feedback) return;
    setFeedbackPending(true);
    setError(null);
    try {
      await recordFeedback(feedbackMessageId, userId.trim(), nextFeedback);
      setFeedback(nextFeedback);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "反馈提交失败，请稍后重试。");
    } finally {
      setFeedbackPending(false);
    }
  }

  const identityReady = Boolean(userId.trim() && conversationId.trim());

  return (
    <main className="workspace">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="IT 运维知识助手首页">
          <span>IT</span>
          <div><strong>运维知识助手</strong><small>Operations Intelligence</small></div>
        </a>
        <div className="environment"><i aria-hidden="true" /> 本地演示环境</div>
      </header>

      <section className="intro" id="top">
        <div>
          <span className="section-kicker">SERVICE DESK / 2026</span>
          <h1>让每次故障处理<br />都有迹可循。</h1>
        </div>
        <p>连接企业知识、自动诊断与工单流程。所有建议附带引用，所有创建操作均由你最终确认。</p>
      </section>

      <section className="identity-bar" aria-label="连接信息">
        <label>用户标识<input value={userId} onChange={(event) => setUserId(event.target.value)} /></label>
        <label>会话标识<input value={conversationId} onChange={(event) => setConversationId(event.target.value)} /></label>
        <span>{identityReady ? "连接信息已就绪" : "请填写连接信息"}</span>
      </section>

      {error && <div className="error-banner" role="alert">{error}</div>}

      <div className="work-grid">
        <ChatPanel
          messages={messages}
          input={input}
          isStreaming={isStreaming}
          canSend={identityReady}
          onInputChange={setInput}
          onSubmit={sendMessage}
          feedbackMessageId={feedbackMessageId}
          feedback={feedback}
          feedbackPending={feedbackPending}
          onFeedback={submitFeedback}
        />
        <aside className="results" aria-label="运行结果">
          {!runId && !draft && !handoff && !citations.length ? (
            <div className="result-placeholder">
              <span>OUTPUT</span>
              <p>运行轨迹、知识引用与工单草稿将在这里实时出现。</p>
            </div>
          ) : null}
          <RunTimeline runId={runId} steps={steps} active={isStreaming} finalState={finalState} />
          <CitationList citations={citations} />
          {draft && (
            <TicketDraftCard
              key={`${runId ?? "draft"}-${confirmationToken ? "fresh" : "edited"}`}
              draft={draft}
              tokenAvailable={Boolean(confirmationToken)}
              submitting={submittingTicket}
              result={ticketResult}
              onEdit={editDraft}
              onConfirm={submitTicket}
            />
          )}
          {handoff && <HandoffCard reason={handoff.reason} userInput={handoff.userInput} />}
        </aside>
      </div>

      <footer><span>IT OPERATIONS AGENT</span><span>知识可溯源 · 操作需确认</span></footer>
    </main>
  );
}
