import { useEffect, useState } from "react";

import { loadUserTickets } from "../api";
import { PRIORITY_INFO, PRIORITY_LABELS, STATUS_LABELS } from "../demoContent";
import type { TicketSummary } from "../types";

interface TicketExplorerProps {
  userId: string;
  onSelectPrompt: (prompt: string) => void;
  refreshToken?: string;
}

export function TicketExplorer({ userId, onSelectPrompt, refreshToken }: TicketExplorerProps) {
  const [tickets, setTickets] = useState<TicketSummary[]>([]);
  const [search, setSearch] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const currentUser = userId.trim();
    const controller = new AbortController();
    setTickets([]);
    setError(null);
    if (!currentUser) {
      setLoading(false);
      return () => controller.abort();
    }
    setLoading(true);
    loadUserTickets(currentUser, controller.signal)
      .then(setTickets)
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) {
          setError(caught instanceof Error ? caught.message : "工单载入失败。");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [userId, refreshToken]);

  const filtered = tickets.filter((ticket) =>
    `${ticket.ticket_number} ${ticket.title} ${ticket.category}`
      .toLocaleLowerCase()
      .includes(search.trim().toLocaleLowerCase()),
  );
  const visible = showAll || search ? filtered : filtered.slice(0, 8);

  return (
    <section className="tickets-section" id="tickets" aria-labelledby="tickets-title">
      <div className="section-heading">
        <div>
          <span className="section-kicker">YOUR DEMO TICKETS</span>
          <h2 id="tickets-title">可验证的工单</h2>
        </div>
        <p>这张表从数据库读取当前用户自己的工单。点击“填入查询”，在对话里验证查单结果。</p>
      </div>
      <div className="tickets-layout">
        <div className="ticket-browser">
          <div className="ticket-toolbar">
            <strong>{loading ? "…" : tickets.length} 张本人工单</strong>
            <label>
              <span>筛选工单</span>
              <input
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="编号或主题"
              />
            </label>
          </div>
          {loading && <p className="content-state">正在读取工单…</p>}
          {error && <p className="content-error" role="alert">{error}</p>}
          {!loading && !error && filtered.length === 0 && <p className="content-state">没有匹配的工单。</p>}
          {visible.length > 0 && (
            <div className="ticket-table-wrap">
              <table>
                <thead><tr><th>工单号</th><th>问题</th><th>状态</th><th>优先级</th><th>操作</th></tr></thead>
                <tbody>
                  {visible.map((ticket) => (
                    <tr key={ticket.ticket_number}>
                      <td><code>{ticket.ticket_number}</code></td>
                      <td>{ticket.title}</td>
                      <td>{STATUS_LABELS[ticket.status] ?? ticket.status}</td>
                      <td>{PRIORITY_LABELS[ticket.priority] ?? ticket.priority}</td>
                      <td>
                        <button
                          type="button"
                          aria-label={`查询 ${ticket.ticket_number}`}
                          onClick={() => onSelectPrompt(`查询工单 ${ticket.ticket_number} 的进度`)}
                        >填入查询 ↗</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {!showAll && !search && tickets.length > 8 && (
            <button className="show-all-button" type="button" onClick={() => setShowAll(true)}>
              展开全部 {tickets.length} 张工单
            </button>
          )}
        </div>
        <aside className="priority-guide">
          <h3>优先级是什么意思？</h3>
          <p>优先级表示影响程度，不是人物或处理人。此处是演示分类，没有配置真实 SLA 或自动派单。</p>
          <dl>
            {PRIORITY_INFO.map((priority) => (
              <div key={priority.value}>
                <dt>{priority.label}<small>{priority.value}</small></dt>
                <dd>{priority.description}</dd>
              </div>
            ))}
          </dl>
          <p className="status-help">状态代码：<code>pending</code> 待分派、<code>in_progress</code> 处理中、<code>resolved</code> 待确认、<code>closed</code> 已关闭。</p>
        </aside>
      </div>
    </section>
  );
}
