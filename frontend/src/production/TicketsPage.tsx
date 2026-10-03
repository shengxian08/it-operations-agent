import { useEffect, useState } from "react";
import { api, isAbort } from "./api";
import { ErrorNotice, PageHeading, StatusBadge } from "./shared";
import { TicketDetail } from "./TicketDetail";
import type { Assignee, Role, Ticket } from "./types";

export default function TicketsPage({ role, embedded = false, assignedUsers }: { role: Role; embedded?: boolean; assignedUsers?: Assignee[] }) {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [assignees, setAssignees] = useState<Assignee[]>(assignedUsers ?? []);
  const [cursor, setCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [refresh, setRefresh] = useState(0);
  const support = role !== "employee";
  useEffect(() => { if (assignedUsers) setAssignees(assignedUsers); }, [assignedUsers]);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError(null);
    Promise.all([api.tickets(undefined, controller.signal), support && !assignedUsers ? api.assignees(controller.signal) : Promise.resolve({ users: assignedUsers ?? [] })]).then(([result, users]) => { if (controller.signal.aborted) return; setTickets(result.tickets); setCursor(result.next_cursor); setAssignees(users.users); setSelected((current) => current && result.tickets.some((ticket) => ticket.ticket_number === current) ? current : result.tickets[0]?.ticket_number ?? null); }).catch((caught: unknown) => { if (!isAbort(caught)) setError(caught); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [refresh, support, assignedUsers]);
  async function more() {
    if (!cursor || loading) return; setLoading(true); setError(null);
    try { const result = await api.tickets(cursor); setTickets((current) => [...current, ...result.tickets.filter((item) => !current.some((existing) => item.ticket_number === existing.ticket_number))]); setCursor(result.next_cursor); }
    catch (caught) { setError(caught); } finally { setLoading(false); }
  }
  return <section className={embedded ? "embedded-page" : "production-page tickets-section"}><PageHeading kicker="SERVICE TICKETS" title={support ? "企业工单" : "我的工单"} description={support ? "查看企业工单、分派处理人，并记录处理状态和回复。" : "跟进已确认提交的工单，补充处理信息，必要时重新开启。"} /><div className="button-row"><button disabled={loading} onClick={() => setRefresh((value) => value + 1)}>刷新工单列表</button></div><ErrorNotice error={error} onRetry={() => setRefresh((value) => value + 1)} /><div className="record-detail-layout"><div className="record-list" aria-label="工单列表">{tickets.map((ticket) => <button key={ticket.ticket_number} className={ticket.ticket_number === selected ? "record-row selected" : "record-row"} onClick={() => setSelected(ticket.ticket_number)}><small>{ticket.ticket_number}</small><strong>{ticket.title}</strong><StatusBadge status={ticket.status} /></button>)}{loading ? <p role="status">正在加载工单…</p> : !tickets.length ? <p className="empty-note">暂无工单。</p> : null}{cursor ? <button disabled={loading} onClick={more}>加载更多工单</button> : null}</div>{selected ? <TicketDetail key={selected} number={selected} support={support} assignees={assignees} onUpdated={(updated) => setTickets((current) => current.map((ticket) => ticket.ticket_number === updated.ticket_number ? updated : ticket))} /> : <div className="detail-panel workspace-empty"><h3>选择工单查看处理记录</h3></div>}</div></section>;
}
