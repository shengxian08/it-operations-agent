import type { FormEvent } from "react";

import type { ChatMessage, MessageFeedback } from "../types";

interface ChatPanelProps {
  messages: ChatMessage[];
  input: string;
  isStreaming: boolean;
  canSend: boolean;
  onInputChange: (value: string) => void;
  onSubmit: () => void;
  feedbackMessageId: string | null;
  feedback: MessageFeedback | null;
  feedbackPending: boolean;
  onFeedback: (feedback: MessageFeedback) => void;
}

export function ChatPanel({
  messages,
  input,
  isStreaming,
  canSend,
  onInputChange,
  onSubmit,
  feedbackMessageId,
  feedback,
  feedbackPending,
  onFeedback,
}: ChatPanelProps) {
  function submit(event: FormEvent) {
    event.preventDefault();
    if (canSend && !isStreaming && input.trim()) onSubmit();
  }

  return (
    <section className="chat-panel" aria-label="助手对话">
      <div className="chat-heading">
        <div>
          <span className="section-kicker">ASSISTANT</span>
          <h2>运维对话</h2>
        </div>
        <span className={isStreaming ? "live-badge is-active" : "live-badge"}>
          <i aria-hidden="true" />
          {isStreaming ? "正在处理" : "就绪"}
        </span>
      </div>

      <div className="message-list" aria-live="polite">
        {messages.length === 0 ? (
          <div className="empty-chat">
            <span aria-hidden="true">01</span>
            <h3>从一个具体问题开始</h3>
            <p>描述故障现象、影响范围和已经尝试的操作，助手会检索知识并给出可追溯的建议。</p>
          </div>
        ) : (
          messages.map((message) => (
            <article className={`message message-${message.role}`} key={message.id}>
              <span>{message.role === "user" ? "你" : "IT 助手"}</span>
              <p>{message.content}</p>
            </article>
          ))
        )}
        {isStreaming && (
          <div className="thinking" role="status">
            <i /> <i /> <i />
            正在编排处理步骤
          </div>
        )}
        {feedbackMessageId && !isStreaming && (
          <div className="feedback-actions" aria-label="回答反馈">
            <span>{feedback ? "反馈已记录" : "这个回答解决了问题吗？"}</span>
            <button
              type="button"
              disabled={feedbackPending || feedback !== null}
              onClick={() => onFeedback("resolved")}
            >
              已解决
            </button>
            <button
              type="button"
              disabled={feedbackPending || feedback !== null}
              onClick={() => onFeedback("unresolved")}
            >
              仍未解决
            </button>
          </div>
        )}
      </div>

      <form className="composer" onSubmit={submit}>
        <label htmlFor="chat-input">输入 IT 问题</label>
        <textarea
          id="chat-input"
          value={input}
          onChange={(event) => onInputChange(event.target.value)}
          placeholder="例如：VPN 连接后无法访问内部知识库……"
          rows={3}
          maxLength={10000}
        />
        <div className="composer-footer">
          <span>{input.length.toLocaleString()} / 10,000</span>
          <button type="submit" disabled={!canSend || isStreaming || !input.trim()}>
            {isStreaming ? "处理中" : "发送请求"}
          </button>
        </div>
      </form>
    </section>
  );
}
