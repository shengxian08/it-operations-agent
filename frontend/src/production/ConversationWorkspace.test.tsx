import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConversationWorkspace } from "./ConversationWorkspace";
import type { TicketIntake } from "../types";

const principal = { id: "u", display_name: "员工", role: "employee" as const, csrf_token: "csrf" };
const conversations = [{ id: "c1", title: "会话一", status: "active", created_at: "2026-01-01", updated_at: "2026-01-01" }, { id: "c2", title: "会话二", status: "active", created_at: "2026-01-01", updated_at: "2026-01-01" }];
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const frame = (sequence: number, answer: string, run = "r1") => new TextEncoder().encode(`id: ${sequence}\nevent: final\ndata: ${JSON.stringify({ run_id: run, answer, message_id: `${run}-answer`, final_state: "answered", trace_id: "trace" })}\n\n`);
afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear(); });

function standard(url: string) {
  if (url === "/api/v1/conversations") return response({ conversations, next_cursor: null });
  if (url.includes("/messages")) return response({ messages: [], next_cursor: null });
  if (url.includes("/ticket-drafts")) return response({ drafts: [] });
  if (url.includes("/conversations/") && url.endsWith("/runs")) return response({ runs: [], next_cursor: null });
  throw new Error(`unexpected request ${url}`);
}

describe("conversation durable requests", () => {
  it("restores owned intake facts and distinguishes unanswered from explicitly no attempts", async () => {
    let result = { run_id: "r1", answer: "请说明影响范围", message_id: "m", final_state: "ticket_collection", trace_id: "t",
      ticket_intake: { schema_version: 1, user_id: "u", conversation_id: "c1", outcome: "collecting",
        problem: "VPN错误E42", impact: null, attempted_steps: null, next_field: "impact", reason: null } as TicketIntake };
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/v1/conversations/c1/runs") return response({ runs: [{ id: "r1", status: "completed", trace_id: "t", result }], next_cursor: null });
      if (url === "/api/v1/runs/r1") return response({ id: "r1", status: "completed", trace_id: "t", result });
      return standard(url);
    }));
    const user = userEvent.setup(); render(<ConversationWorkspace principal={principal} onOpenSource={vi.fn()} />);
    expect(await screen.findByRole("heading", { name: "请补充工单信息" })).toBeInTheDocument();
    expect(screen.getByText("VPN错误E42", { exact: true })).toBeInTheDocument();
    expect(screen.getAllByText("尚未回答", { exact: true })).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "确认并创建工单" })).not.toBeInTheDocument();
    expect(screen.queryByText("人工交接未确认")).not.toBeInTheDocument();
    result = { ...result, ticket_intake: { ...result.ticket_intake, attempted_steps: [] } };
    await user.click(screen.getByRole("button", { name: "刷新会话" }));
    expect(await screen.findByText("员工明确尚未尝试", { exact: true })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /会话二/ }));
    await waitFor(() => expect(screen.queryByText("VPN错误E42", { exact: true })).not.toBeInTheDocument());
  });

  it("does not display intake facts belonging to another user", async () => {
    const result = { run_id: "r1", answer: "请补充信息", message_id: "m", final_state: "ticket_collection", trace_id: "t",
      ticket_intake: { schema_version: 1, user_id: "other", conversation_id: "c1", outcome: "collecting",
        problem: "另一员工的故障", impact: null, attempted_steps: null, next_field: "impact", reason: null } };
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/v1/conversations/c1/runs") return response({ runs: [{ id: "r1", status: "completed", trace_id: "t", result }], next_cursor: null });
      if (url === "/api/v1/runs/r1") return response({ id: "r1", status: "completed", trace_id: "t", result });
      return standard(url);
    }));
    render(<ConversationWorkspace principal={principal} onOpenSource={vi.fn()} />);
    await screen.findByRole("heading", { name: "请补充工单信息" });
    expect(screen.queryByText("另一员工的故障", { exact: true })).not.toBeInTheDocument();
  });

  it("restores clarification, selects an exact number and sends one read-only lookup", async () => {
    const lookup = { schema_version: 1, user_id: "u", conversation_id: "c1", outcome: "clarification",
      ticket_number: null, basis: null, reason: "ambiguous_ticket_number", candidates: ["IT-2026-0001", "IT-2026-0002"] };
    let result = { run_id: "r1", answer: "请选择工单", message_id: "r1-answer", final_state: "ticket_lookup_clarification", trace_id: "trace", ticket_lookup: lookup };
    const requests: Array<{ path: string; body: unknown }> = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        requests.push({ path: url, body: JSON.parse(init.body as string) });
        result = { ...result, run_id: "r2", final_state: "ticket_status", answer: "当前工单已解决", message_id: "r2-answer" };
        return response({ id: "r2", status: "completed", trace_id: "trace", result }, 202);
      }
      if (url === "/api/v1/conversations/c1/runs") return response({ runs: [{ id: "r1", status: "completed", trace_id: "trace", result }], next_cursor: null });
      if (url.startsWith("/api/v1/runs/")) return response({ id: result.run_id, status: "completed", trace_id: "trace", result });
      return standard(url);
    }));
    const user = userEvent.setup(); render(<ConversationWorkspace principal={principal} onOpenSource={vi.fn()} />);
    expect(await screen.findByRole("heading", { name: "请选择要查询的工单" })).toBeInTheDocument();
    expect(screen.queryByText("人工交接未确认")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("输入 IT 问题"), "稍后要问的另一个问题");
    await user.click(screen.getByRole("button", { name: "查询工单 IT-2026-0002" }));
    expect(await screen.findByText("当前工单已解决")).toBeInTheDocument();
    expect(requests).toHaveLength(1);
    expect(requests[0].path).toBe("/api/v1/conversations/c1/runs");
    expect(requests[0].body).toMatchObject({ content: "IT-2026-0002" });
    expect(screen.getByLabelText("输入 IT 问题")).toHaveValue("稍后要问的另一个问题");
  });

  it("shows a missing-number prompt without handoff or ticket creation", async () => {
    const result = { run_id: "r1", answer: "请提供完整工单号", message_id: "m", final_state: "ticket_lookup_clarification", trace_id: "t",
      ticket_lookup: { schema_version: 1, user_id: "u", conversation_id: "c1", outcome: "clarification", ticket_number: null,
        basis: null, reason: "missing_ticket_number", candidates: [] } };
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url === "/api/v1/conversations/c1/runs") return response({ runs: [{ id: "r1", status: "completed", trace_id: "t", result }], next_cursor: null });
      if (url === "/api/v1/runs/r1") return response({ id: "r1", status: "completed", trace_id: "t", result });
      return standard(url);
    }));
    render(<ConversationWorkspace principal={principal} onOpenSource={vi.fn()} />);
    expect(await screen.findByRole("heading", { name: "请选择要查询的工单" })).toBeInTheDocument();
    expect(screen.getByText("在输入框中填写完整工单号（如 IT-2026-0001），然后发送请求。", { exact: true })).toBeInTheDocument();
    expect(screen.queryByText("人工交接未确认")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /查询工单 IT/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "确认并创建工单" })).not.toBeInTheDocument();
  });

  it("does not claim a successful handoff when no durable escalation was created", async () => {
    const final = { run_id: "r1", answer: "运行中断，请联系 IT 支持。", message_id: "r1-answer",
      final_state: "handoff", error: "processing_failed", trace_id: "trace" };
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") return response({ id: "r1", status: "running", trace_id: "trace", result: null }, 202);
      if (url.includes("/events?")) return new Response(new ReadableStream<Uint8Array>({ start(controller) {
        controller.enqueue(new TextEncoder().encode(`id: 1\nevent: final\ndata: ${JSON.stringify(final)}\n\n`)); controller.close();
      } }));
      if (url === "/api/v1/runs/r1") return response({ id: "r1", status: "failed", trace_id: "trace", events: [], result: final });
      return standard(url);
    }));
    const user = userEvent.setup(); render(<ConversationWorkspace principal={principal} onOpenSource={vi.fn()} />);
    await waitFor(() => expect(screen.getByLabelText("输入 IT 问题")).toBeEnabled());
    await user.type(screen.getByLabelText("输入 IT 问题"), "请转人工"); await user.click(screen.getByRole("button", { name: "发送请求" }));
    expect(await screen.findByText("人工交接未确认")).toBeInTheDocument();
    expect(screen.queryByText("已转交人工支持")).not.toBeInTheDocument();
  });
  it("keeps a late answer in its original conversation after switching", async () => {
    let controller: ReadableStreamDefaultController<Uint8Array> | undefined; let completed = false;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") return response({ id: "r1", status: "running", trace_id: "trace", result: null }, 202);
      if (url.includes("/events?")) return new Response(new ReadableStream<Uint8Array>({ start(value) { controller = value; } }), { headers: { "Content-Type": "text/event-stream" } });
      if (url === "/api/v1/runs/r1") return response({ id: "r1", status: completed ? "completed" : "running", trace_id: "trace", events: [], result: completed ? { run_id: "r1", answer: "仅会话一的答案", message_id: "r1-answer", final_state: "answered", trace_id: "trace" } : null });
      if (url === "/api/v1/conversations/c1/runs") return response({ runs: completed ? [{ id: "r1", status: "completed", trace_id: "trace", result: null }] : [], next_cursor: null });
      return standard(url);
    }));
    const user = userEvent.setup(); render(<ConversationWorkspace principal={principal} onOpenSource={vi.fn()} />);
    await waitFor(() => expect(screen.getByLabelText("输入 IT 问题")).toBeEnabled());
    await user.type(screen.getByLabelText("输入 IT 问题"), "问题一"); await user.click(screen.getByRole("button", { name: "发送请求" }));
    await waitFor(() => expect(controller).toBeDefined());
    await user.click(screen.getByRole("button", { name: /会话二/ }));
    await act(async () => { completed = true; controller!.enqueue(frame(1, "仅会话一的答案")); controller!.close(); });
    expect(screen.queryByText("仅会话一的答案")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /会话一/ }));
    expect(await screen.findByText("仅会话一的答案")).toBeInTheDocument();
  });

  it("recovers the durable answer when the stream closes without final", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") return response({ id: "r1", status: "running", trace_id: "trace", result: null }, 202);
      if (url.includes("/events?")) return new Response(new ReadableStream<Uint8Array>({ start(controller) { controller.close(); } }));
      if (url === "/api/v1/runs/r1") return response({ id: "r1", status: "completed", trace_id: "trace", events: [], result: { run_id: "r1", answer: "持久结果恢复成功", message_id: "r1-answer", final_state: "answered", trace_id: "trace" } });
      return standard(url);
    }));
    const user = userEvent.setup(); render(<ConversationWorkspace principal={principal} onOpenSource={vi.fn()} />);
    await waitFor(() => expect(screen.getByLabelText("输入 IT 问题")).toBeEnabled()); await user.type(screen.getByLabelText("输入 IT 问题"), "问题"); await user.click(screen.getByRole("button", { name: "发送请求" }));
    expect(await screen.findByText("持久结果恢复成功")).toBeInTheDocument();
  });

  it("does not replace a newer run with an older delayed reconciliation", async () => {
    let release: ((response: Response) => void) | undefined; let runNumber = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "POST") { runNumber += 1; return response({ id: `r${runNumber}`, status: "running", trace_id: "trace", result: null }, 202); }
      if (url.includes("/runs/r1/events?")) return new Response(new ReadableStream<Uint8Array>({ start(controller) { controller.enqueue(frame(1, "第一轮答案")); controller.close(); } }));
      if (url.includes("/runs/r2/events?")) return new Response(new ReadableStream<Uint8Array>({ start() { /* The newer run remains in progress. */ } }));
      if (url === "/api/v1/runs/r1") return new Promise<Response>((resolve) => { release = resolve; });
      return standard(url);
    }));
    const user = userEvent.setup(); render(<ConversationWorkspace principal={principal} onOpenSource={vi.fn()} />);
    await waitFor(() => expect(screen.getByLabelText("输入 IT 问题")).toBeEnabled()); await user.type(screen.getByLabelText("输入 IT 问题"), "第一轮"); await user.click(screen.getByRole("button", { name: "发送请求" }));
    await screen.findByText("第一轮答案"); await user.type(screen.getByLabelText("输入 IT 问题"), "第二轮"); await user.click(screen.getByRole("button", { name: "发送请求" }));
    await screen.findByRole("button", { name: "取消运行" });
    await act(async () => { release!(response({ id: "r1", status: "completed", trace_id: "trace", events: [], result: { run_id: "r1", answer: "第一轮答案", message_id: "r1-answer", final_state: "answered", trace_id: "trace" } })); });
    expect(screen.getByRole("button", { name: "取消运行" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "发送请求" })).toBeDisabled();
  });
});
