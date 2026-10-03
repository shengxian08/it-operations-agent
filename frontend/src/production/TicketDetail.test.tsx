import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { TicketDetail } from "./TicketDetail";
import type { Ticket } from "./types";

const ticket: Ticket = { id: "t", ticket_number: "IT-2026-0001", user_id: "employee", title: "合成VPN问题", category: "network", priority: "medium", description: "合成描述", attempted_steps: [], status: "pending", version: 1, assignee_id: null, created_at: "2026-10-03T00:00:00Z", updated_at: "2026-10-03T00:00:00Z", comments: [], audit: [] };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
afterEach(() => vi.unstubAllGlobals());

it("support explicitly saves an internal note and sees its durable visibility label", async () => {
  let current = { ...ticket };
  const writes: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      const body = JSON.parse(String(init.body)); writes.push(body);
      current = { ...current, version: 2, comments: [{ id: "c", author_id: "support", content: body.content, visibility: body.visibility, created_at: ticket.created_at }] };
      return response(current.comments?.[0]);
    }
    return response(current);
  }));
  const user = userEvent.setup(); render(<TicketDetail number={ticket.ticket_number} support assignees={[]} onUpdated={vi.fn()} />);
  await user.selectOptions(await screen.findByLabelText("回复可见性"), "internal");
  await user.type(screen.getByLabelText("补充信息"), "合成内部排查记录");
  await user.click(screen.getByRole("button", { name: "保存补充信息" }));
  expect(await screen.findByText("内部备注（仅支持人员）", { exact: true, selector: "strong" })).toBeInTheDocument();
  expect(writes).toEqual([{ version: 1, content: "合成内部排查记录", visibility: "internal" }]);
});

it("a historical unclassified comment needs an explicit classification operation", async () => {
  let current: Ticket = { ...ticket, comments: [{ id: "old", author_id: "support", content: "旧合成备注", visibility: "unclassified" as const, created_at: ticket.created_at }] };
  const writes: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method === "PATCH") { writes.push([url, JSON.parse(String(init.body))]); current = { ...current, version: 2, comments: [{ ...current.comments![0], visibility: "public" }] }; }
    return response(current);
  }));
  const user = userEvent.setup(); render(<TicketDetail number={ticket.ticket_number} support assignees={[]} onUpdated={vi.fn()} />);
  expect(await screen.findByText("未分类历史备注（员工不可见）", { exact: true })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "核对后公开此记录" }));
  await waitFor(() => expect(writes).toEqual([["/api/v1/tickets/IT-2026-0001/comments/old", { version: 1, visibility: "public" }]]));
  expect(await screen.findByText("公开回复", { exact: true })).toBeInTheDocument();
});

it("employee writes only public and refresh failure removes previously displayed detail", async () => {
  let fail = false;
  const writes: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "POST") writes.push(JSON.parse(String(init.body)));
    return fail ? response({ detail: "ticket not found" }, 404) : response(ticket);
  }));
  const user = userEvent.setup(); render(<TicketDetail number={ticket.ticket_number} support={false} assignees={[]} onUpdated={vi.fn()} />);
  await screen.findByText("合成VPN问题"); expect(screen.queryByLabelText("回复可见性")).not.toBeInTheDocument();
  await user.type(screen.getByLabelText("补充信息"), "合成员工补充"); await user.click(screen.getByRole("button", { name: "保存补充信息" }));
  await waitFor(() => expect(writes).toEqual([{ version: 1, content: "合成员工补充", visibility: "public" }]));
  await screen.findByText("补充信息已保存。"); fail = true;
  await user.click(screen.getByRole("button", { name: "刷新工单详情" }));
  await waitFor(() => expect(screen.queryByText("合成VPN问题")).not.toBeInTheDocument());
});
