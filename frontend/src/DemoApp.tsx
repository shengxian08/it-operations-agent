import { useRef, useState } from "react";

import { confirmTicket, recordFeedback, streamMessage } from "./api";
import { ChatPanel } from "./components/ChatPanel";
import { CitationList } from "./components/CitationList";
import { DemoGuide } from "./components/DemoGuide";
import { HandoffCard } from "./components/HandoffCard";
import { KnowledgeLibrary } from "./components/KnowledgeLibrary";
import { RunTimeline } from "./components/RunTimeline";
import { TicketDraftCard } from "./components/TicketDraftCard";
import { TicketExplorer } from "./components/TicketExplorer";
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

const RESULT_LABELS: Record<string, { title: string; explanation: string }> = {
  answered: { title: "已根据资料回答", explanation: "查看下方检索片段，并可打开知识原文核对。" },
  ticket_status: { title: "已查询本人工单", explanation: "查单结果来自工单数据，不使用知识文档作为依据。" },
  awaiting_confirmation: { title: "工单草稿待确认", explanation: "只有你核对草稿并点击确认，系统才会创建工单。" },
  handoff: { title: "需要人工支持", explanation: "本次请求没有可可靠执行的自动结果，请查看原因与下一步。" },
};

export function DemoApp() {
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
  const [openDocument, setOpenDocument] = useState<{ id: string; request: number } | null>(null);
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

  function fillPrompt(prompt: string) {
    setInput(prompt);
    document.getElementById("workspace")?.scrollIntoView({ behavior: "smooth", block: "start" });
    requestAnimationFrame(() => document.getElementById("chat-input")?.focus());
  }

  function openSource(documentId: string) {
    setOpenDocument((current) => ({ id: documentId, request: (current?.request ?? 0) + 1 }));
    document.getElementById("knowledge")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <main className="workspace">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="IT 运维知识助手首页">
          <span>IT</span>
          <div><strong>运维知识助手</strong><small>Operations Intelligence</small></div>
        </a>
        <nav className="top-nav" aria-label="页面导航">
          <a href="#try">怎么检验</a>
          <a href="#workspace">开始对话</a>
          <a href="#knowledge">知识资料</a>
          <a href="#tickets">演示工单</a>
        </nav>
        <div className="environment"><i aria-hidden="true" /> 本地演示环境</div>
      </header>

      <section className="intro" id="top">
        <div>
          <span className="section-kicker">SERVICE DESK / 2026</span>
          <h1>看得见资料的<br />IT 支持工作台。</h1>
        </div>
        <p>先看系统有哪些资料和工单，再用真实案例提问。每个回答都能核对来源，建单要由你亲自确认。</p>
      </section>

      <section className="identity-bar" aria-label="连接信息">
        <label>用户标识<input value={userId} onChange={(event) => setUserId(event.target.value)} /></label>
        <label>会话标识<input value={conversationId} onChange={(event) => setConversationId(event.target.value)} /></label>
        <span>{identityReady ? "演示身份已就绪" : "请填写演示身份"}<small>这是本地模拟身份，不是正式登录认证。</small></span>
      </section>

      {error && <div className="error-banner" role="alert">{error}</div>}

      <DemoGuide onSelectPrompt={fillPrompt} />

      <div className="work-grid" id="workspace">
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
              <span>RESULT</span>
              <p>发送一个上方的示例问题，这里会显示结果、资料引用或工单草稿。</p>
            </div>
          ) : null}
          {finalState && (
            <div className="result-card outcome-card" role="status">
              <span className="section-kicker">本次结果</span>
              <h2>{ticketResult ? "工单已创建" : RESULT_LABELS[finalState]?.title ?? "处理已结束"}</h2>
              <p>{ticketResult ? `工单 ${ticketResult.ticket_number} 已提交，可在下方工单列表中查看。` : RESULT_LABELS[finalState]?.explanation ?? "请查看下方详情。"}</p>
            </div>
          )}
          <CitationList citations={citations} onOpenSource={openSource} />
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
          <RunTimeline runId={runId} steps={steps} active={isStreaming} finalState={finalState} />
        </aside>
      </div>

      <KnowledgeLibrary userId={userId} openDocument={openDocument} />
      <TicketExplorer userId={userId} onSelectPrompt={fillPrompt} refreshToken={ticketResult?.ticket_number} />

      <footer><span>IT OPERATIONS AGENT</span><span>知识可溯源 · 操作需确认</span></footer>
    </main>
  );
}
