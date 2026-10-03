import { useRef, useState } from "react";
import type { MessageFeedback } from "../types";
import { api } from "./api";
import { ErrorNotice } from "./shared";

export function AnswerFeedback({ messageId }: { messageId: string }) {
  const [feedback, setFeedback] = useState<MessageFeedback | null>(null); const [busy, setBusy] = useState(false); const [error, setError] = useState<unknown>(null); const lock = useRef(false);
  async function submit(value: MessageFeedback) { if (lock.current || feedback) return; lock.current = true; setBusy(true); setError(null); try { await api.feedback(messageId, value); setFeedback(value); } catch (caught) { setError(caught); } finally { lock.current = false; setBusy(false); } }
  return <section aria-label="回答反馈"><div className="feedback-actions"><span>{feedback ? "反馈已记录" : "这个回答解决了问题吗？"}</span><button disabled={busy || feedback !== null} onClick={() => submit("resolved")}>已解决</button><button disabled={busy || feedback !== null} onClick={() => submit("unresolved")}>仍未解决</button></div><ErrorNotice error={error} /></section>;
}
