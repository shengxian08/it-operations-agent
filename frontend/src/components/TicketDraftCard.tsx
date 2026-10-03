import { useState } from "react";

import type { TicketCreateResult, TicketDraft, TicketPriority } from "../types";
import { PRIORITY_LABELS } from "../demoContent";

interface TicketDraftCardProps {
  draft: TicketDraft;
  tokenAvailable: boolean;
  submitting: boolean;
  result: TicketCreateResult | null;
  onEdit: (draft: TicketDraft) => void;
  onConfirm: () => void;
}

const PRIORITIES: TicketPriority[] = ["low", "medium", "high", "critical"];

export function TicketDraftCard({
  draft,
  tokenAvailable,
  submitting,
  result,
  onEdit,
  onConfirm,
}: TicketDraftCardProps) {
  const [dirty, setDirty] = useState(false);
  const [confirmed, setConfirmed] = useState(false);

  function update<K extends keyof TicketDraft>(key: K, value: TicketDraft[K]) {
    setDirty(true);
    setConfirmed(false);
    onEdit({ ...draft, [key]: value });
  }

  const requiresRegeneration = dirty || !tokenAvailable;

  return (
    <section className="result-card draft-card" aria-labelledby="draft-title">
      <div className="card-title-row">
        <div>
          <span className="section-kicker">TICKET DRAFT</span>
          <h2 id="draft-title">工单草稿</h2>
        </div>
        <span className="draft-status">待确认</span>
      </div>

      <div className="draft-grid">
        <label className="field-wide">
          标题
          <input
            value={draft.title}
            maxLength={300}
            onChange={(event) => update("title", event.target.value)}
          />
        </label>
        <label>
          分类
          <input
            value={draft.category}
            maxLength={100}
            onChange={(event) => update("category", event.target.value)}
          />
        </label>
        <label>
          优先级
          <select
            value={draft.priority}
            onChange={(event) => update("priority", event.target.value as TicketPriority)}
          >
            {PRIORITIES.map((priority) => (
              <option key={priority} value={priority}>{PRIORITY_LABELS[priority]}</option>
            ))}
          </select>
        </label>
        <p className="field-help field-wide">优先级表示影响程度，不代表处理人；<a href="#tickets">查看四级说明</a>。修改草稿后需要重新生成确认令牌。</p>
        <label className="field-wide">
          问题描述
          <textarea
            value={draft.description}
            rows={5}
            maxLength={10000}
            onChange={(event) => update("description", event.target.value)}
          />
        </label>
        <label className="field-wide">
          已尝试步骤（每行一项）
          <textarea
            value={draft.attempted_steps.join("\n")}
            rows={3}
            onChange={(event) =>
              update(
                "attempted_steps",
                event.target.value.split("\n").filter((step) => step.trim()).slice(0, 50),
              )
            }
          />
        </label>
      </div>

      {requiresRegeneration && !result && (
        <p className="token-warning" role="alert">
          草稿已编辑，原确认令牌已失效。请重新发送请求生成新草稿和令牌。
        </p>
      )}
      {result && (
        <p className="ticket-success" role="status">
          工单 <strong>{result.ticket_number}</strong> 已创建，状态：{result.status}
        </p>
      )}
      {!result && (
        <label className="confirmation-check">
          <input
            type="checkbox"
            checked={confirmed}
            disabled={requiresRegeneration || submitting}
            onChange={(event) => setConfirmed(event.target.checked)}
          />
          我已核对以上内容，并确认创建此工单
        </label>
      )}
      <button
        className="confirm-button"
        type="button"
        disabled={requiresRegeneration || !confirmed || submitting || Boolean(result)}
        onClick={() => {
          if (!requiresRegeneration) onConfirm();
        }}
      >
        {submitting ? "正在提交" : result ? "已创建工单" : "确认并创建工单"}
      </button>
    </section>
  );
}
