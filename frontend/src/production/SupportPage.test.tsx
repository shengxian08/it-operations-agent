import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import SupportPage from "./SupportPage";
import type { EscalationContext, EscalationDetail } from "./types";

const record = { id: "handoff-1", user_id: "employee", conversation_id: "conversation", run_id: "run", reason: "explicit_manual_request", status: "pending", version: 1, assignee_id: null, created_at: "2026-10-03T00:00:00Z" };
const context: EscalationContext = { schema_version: 1, captured_at: "2026-10-03T00:00:00Z", problem: "VPN错误E42", impact: "仅本人无法办公", attempted_steps: ["已重启客户端，仍失败"], source: { run_id: "prior-run", draft_id: null, draft_version: null },
  messages: [{ id: "message-1", content: "故障：VPN无法连接", omission: null }, { id: "message-large", content: null, omission: "too_long" }], messages_omitted: true, citations_omitted: false, citations_unavailable: 0,
  citations: [{ document_id: "source-1", index_revision: "revision-1", chunk_index: 0, source_title: "VPN历史原文", source_path: "vpn.md", excerpt: "仅在证书过期时重新签发，等待5分钟。", excerpt_omitted: false, source_message_id: "answer-1" }] };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
afterEach(() => vi.unstubAllGlobals());

describe("human support context and audit", () => {
  it("reads bounded facts and versioned references, then reloads processing audit after saving", async () => {
    let detail: EscalationDetail = { ...record, context, audit: [{ id: "created", actor_id: "employee", event_type: "created", details: { version: { from: null, to: 1 } }, created_at: record.created_at }], next_audit_cursor: null };
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/v1/escalations") return response({ escalations: [detail], next_cursor: null });
      if (url === "/api/v1/support/assignees") return response({ users: [{ id: "support", display_name: "李工", role: "support" }] });
      if (url === "/api/v1/escalations/handoff-1" && init?.method === "PATCH") {
        detail = { ...detail, version: 2, status: "in_progress", audit: [{ id: "updated", actor_id: "support", event_type: "updated", details: { version: { from: 1, to: 2 } }, created_at: record.created_at }, ...detail.audit] };
        return response(detail);
      }
      if (url.startsWith("/api/v1/escalations/handoff-1")) return response(detail);
      throw new Error(`unexpected ${url}`);
    }));
    const user = userEvent.setup(), openSource = vi.fn(); render(<SupportPage onOpenSource={openSource} />);
    await user.click(await screen.findByRole("button", { name: "查看交接上下文与处理记录" }));
    const panel = await screen.findByRole("region", { name: "交接上下文与处理记录" });
    expect(await within(panel).findByText("VPN错误E42", { exact: true })).toBeInTheDocument();
    expect(within(panel).getByText("已重启客户端，仍失败", { exact: true })).toBeInTheDocument();
    expect(within(panel).getByText(/原文超过 2000 字/)).toBeInTheDocument();
    expect(within(panel).getByText(/更早的员工消息未纳入/)).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: /VPN历史原文/ }));
    expect(openSource).toHaveBeenCalledWith("source-1", "revision-1");
    await user.selectOptions(screen.getByLabelText("升级状态"), "in_progress");
    await user.click(screen.getByRole("button", { name: "保存升级处理" }));
    expect(await screen.findByText("版本：1 → 2", { exact: true })).toBeInTheDocument();
  });

  it("states explicitly when a historical record has no captured context", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/v1/escalations") return response({ escalations: [record], next_cursor: null });
      if (url === "/api/v1/support/assignees") return response({ users: [] });
      return response({ ...record, context: null, audit: [], next_audit_cursor: null });
    }));
    const user = userEvent.setup(); render(<SupportPage onOpenSource={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "查看交接上下文与处理记录" }));
    expect(await screen.findByText("历史记录未保存故障上下文。", { exact: true })).toBeInTheDocument();
    expect(screen.queryByText("VPN错误E42", { exact: true })).not.toBeInTheDocument();
  });

  it("clears previously displayed context when a refreshed read loses access", async () => {
    let denied = false;
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/v1/escalations") return response({ escalations: [record], next_cursor: null });
      if (url === "/api/v1/support/assignees") return response({ users: [] });
      return denied ? response({ detail: "escalation not found" }, 404) : response({ ...record, context, audit: [], next_audit_cursor: null });
    }));
    const user = userEvent.setup(); render(<SupportPage onOpenSource={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: "查看交接上下文与处理记录" }));
    await screen.findByText("VPN错误E42", { exact: true }); denied = true;
    await user.click(screen.getByRole("button", { name: "刷新交接明细" }));
    await screen.findByRole("alert");
    await waitFor(() => expect(screen.queryByText("VPN错误E42", { exact: true })).not.toBeInTheDocument());
  });
});
