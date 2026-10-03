import { expect, test, type Page, type Route } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

const draftContent = { title: "VPN无法连接", category: "network", priority: "high", description: "连接超时\n影响范围：仅本人无法办公", attempted_steps: ["重启客户端"], problem: "连接超时", impact: "仅本人无法办公", intake_version: 1 };
const principal = { id: "employee-1", display_name: "张宁", role: "employee", csrf_token: "csrf-token" };
const conversation = { id: "conversation-1", title: "VPN故障会话", status: "active", created_at: "2026-01-01", updated_at: "2026-01-01" };
const article = { id: "doc-1", title: "VPN排障手册", access_level: "employee", status: "active", version: 1, created_at: "2026-01-01", sections: [{ heading: "检查步骤", content: "请检查客户端证书有效期。" }] };
const ticket = { id: "ticket-1", ticket_number: "IT-2026-0001", user_id: principal.id, title: "VPN连接故障", category: "network", priority: "high", description: "VPN连接超时", attempted_steps: [], status: "resolved", version: 1, assignee_id: null, created_at: "2026-01-01", updated_at: "2026-01-01", comments: [] as Array<Record<string, unknown>>, audit: [] as Array<Record<string, unknown>> };
const sse = (events: Array<[string, Record<string, unknown>]>) => events.map(([name, data], index) => `id: ${index + 1}\nevent: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join("");

for (const width of [1440, 390]) {
  test(`ticket progress visibility persists and employee reads only public detail at ${width}px (Mock HTTP)`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => { if (["warning", "error"].includes(message.type())) errors.push(message.text()); });
    const state = await setup(page, "support");
    state.tickets = [{ ...ticket, status: "in_progress", comments: [{ id: "old", author_id: "support-1", content: "已核对的历史合成处理记录", visibility: "unclassified", created_at: "2026-10-03T00:00:00Z" }] }];
    await page.setViewportSize({ width, height: 900 }); await page.goto("/");
    await page.getByRole("button", { name: "支持工作台", exact: true }).click();
    await page.getByRole("tab", { name: "企业工单", exact: true }).click();
    await expect(page.getByText("未分类历史备注（员工不可见）", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "核对后公开此记录" }).click();
    await expect(page.getByText("记录已设为公开回复。", { exact: true })).toBeVisible();
    await page.getByLabel("回复可见性").selectOption("internal");
    await page.getByLabel("补充信息").fill("合成内部排查记录，仅供支持人员核对");
    await page.getByRole("button", { name: "保存补充信息" }).click();
    await expect(page.locator(".comment-entry strong", { hasText: "内部备注（仅支持人员）" })).toBeVisible();
    expect(state.received.filter((row) => row.method === "POST" && row.path.endsWith("/comments")).at(-1)?.body).toEqual({ version: 2, content: "合成内部排查记录，仅供支持人员核对", visibility: "internal" });
    await page.reload(); await page.getByRole("button", { name: "支持工作台", exact: true }).click(); await page.getByRole("tab", { name: "企业工单", exact: true }).click();
    await expect(page.getByText("合成内部排查记录，仅供支持人员核对", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
    await auditScreenshot(page, `ticket-progress-support-${width}.png`);
    // The real permission policy is tested against PG/API. These controlled replies exercise UI refresh after a role change.
    state.tickets = state.tickets.map((row) => ({ ...row, comments: row.comments.filter((entry) => entry.visibility === "public") }));
    await page.route("**/api/v1/me", (route) => json(route, principal));
    await page.reload(); await page.getByRole("button", { name: "我的工单", exact: true }).click();
    await expect(page.getByText("已核对的历史合成处理记录", { exact: true })).toBeVisible();
    await expect(page.getByText("合成内部排查记录，仅供支持人员核对", { exact: true })).toHaveCount(0);
    await expect(page.getByLabel("回复可见性")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
    expect(errors).toEqual([]);
    await auditScreenshot(page, `ticket-progress-employee-${width}.png`);
  });

  test(`support reads employee context, updates audit and opens the cited version at ${width}px (Mock HTTP)`, async ({ page, baseURL }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => { if (message.type() === "error" || message.type() === "warning") errors.push(message.text()); });
    const state = await setup(page, "support");
    const record = { id: "handoff-context", user_id: "employee-1", conversation_id: conversation.id, run_id: "handoff-run", reason: "explicit_manual_request", status: "pending", version: 1, assignee_id: null, created_at: "2026-10-03T00:00:00Z" };
    state.escalations = [record];
    const context = { schema_version: 1, captured_at: record.created_at, problem: "VPN错误E42", impact: "仅本人无法办公", attempted_steps: ["重启客户端后仍然失败"], source: { run_id: "intake-run", draft_id: null, draft_version: null },
      messages: [{ id: "employee-message", content: "VPN连接失败，请转人工", omission: null }], messages_omitted: false, citations_omitted: false, citations_unavailable: 0,
      citations: [{ document_id: article.id, index_revision: "handoff-revision", chunk_index: 0, source_title: "VPN资料交接版", source_path: "vpn.md", excerpt: "仅在证书过期时重新签发，等待5分钟。", excerpt_omitted: false, source_message_id: "knowledge-answer" }] };
    let audit = [{ id: "created", actor_id: "employee-1", event_type: "created", details: { version: { from: null as number | null, to: 1 } }, created_at: record.created_at }];
    await page.route("**/api/v1/escalations/handoff-context", async (route) => {
      if (route.request().method() === "PATCH") {
        const body = route.request().postDataJSON();
        expect(body.version).toBe(1); expect(body.status).toBe("in_progress");
        state.escalations = [{ ...state.escalations[0], ...body, version: 2 }];
        audit = [{ id: "updated", actor_id: "support-1", event_type: "updated", details: { version: { from: 1, to: 2 } }, created_at: record.created_at }, ...audit];
      }
      return json(route, { ...state.escalations[0], context, audit, next_audit_cursor: null });
    });
    let revisionOpened = false;
    await page.route("**/api/v1/knowledge/documents/doc-1**", async (route) => {
      expect(new URL(route.request().url()).searchParams.get("index_revision")).toBe("handoff-revision"); revisionOpened = true;
      return json(route, { ...article, title: "VPN资料交接版", sections: [{ heading: "证书条件", content: context.citations[0].excerpt }] });
    });
    await page.setViewportSize({ width, height: 900 }); await page.goto("/");
    expect(new URL(page.url()).origin).toBe(new URL(baseURL!).origin); await expect(page).toHaveTitle(/IT/);
    await page.getByRole("button", { name: "支持工作台", exact: true }).click();
    await page.getByRole("button", { name: "查看交接上下文与处理记录" }).click();
    const details = page.getByRole("region", { name: "交接上下文与处理记录" });
    await expect(details.getByText("VPN错误E42", { exact: true })).toBeVisible();
    await expect(details.getByText("重启客户端后仍然失败", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
    await auditScreenshot(page, `handoff-context-${width}.png`);
    await page.getByLabel("升级处理人").selectOption("support-1");
    await page.getByLabel("升级状态").selectOption("in_progress");
    await page.getByRole("button", { name: "保存升级处理" }).click();
    await expect(details.getByText("版本：1 → 2", { exact: true })).toBeVisible();
    await page.reload(); await page.getByRole("button", { name: "支持工作台", exact: true }).click(); await page.getByRole("button", { name: "查看交接上下文与处理记录" }).click();
    await expect(details.getByText("版本：1 → 2", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
    await auditScreenshot(page, `handoff-processed-${width}.png`);
    await details.getByRole("button", { name: "VPN资料交接版", exact: true }).click();
    await expect(page.getByRole("article", { name: "知识原文" })).toContainText(context.citations[0].excerpt);
    expect(revisionOpened).toBe(true); expect(await page.locator("vite-error-overlay").count()).toBe(0); expect(errors).toEqual([]);
  });
}

for (const width of [1440, 390]) {
  test(`ticket intake collects, restores and waits for explicit attempts at ${width}px (Mock HTTP)`, async ({ page, baseURL }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => { if (message.type() === "error" || message.type() === "warning") errors.push(message.text()); });
    const state = await setup(page);
    const replies = ["创建工单", "VPN错误E42", "仅本人无法办公", "尚未尝试"];
    let turn = 0;
    await page.route("**/api/v1/conversations**", async (route) => {
      const request = route.request(), path = new URL(request.url()).pathname.slice("/api/v1".length);
      if (path === "/conversations") return json(route, { conversations: [conversation, { ...conversation, id: "conversation-2", title: "另一个会话" }], next_cursor: null });
      if (path.startsWith("/conversations/conversation-2/")) return json(route, path.endsWith("/runs") ? { runs: [], next_cursor: null } : { messages: [], next_cursor: null });
      if (path !== `/conversations/${conversation.id}/runs` || request.method() !== "POST") return route.fallback();
      const body = request.postDataJSON();
      state.received.push({ path, method: "POST", body, csrf: request.headers()["x-csrf-token"] });
      expect(body.content).toBe(replies[turn]); turn += 1;
      const intake = { schema_version: 1, user_id: principal.id, conversation_id: conversation.id, outcome: turn === 4 ? "ready" : "collecting",
        problem: turn >= 2 ? "VPN错误E42" : null, impact: turn >= 3 ? "仅本人无法办公" : null, attempted_steps: turn === 4 ? [] : null,
        next_field: turn === 1 ? "problem" : turn === 2 ? "impact" : turn === 3 ? "attempted_steps" : null, reason: null };
      const result = { run_id: `intake-${turn}`, message_id: `intake-answer-${turn}`, answer: turn === 4 ? "请核对草稿并确认。" : "请继续补充工单信息。",
        final_state: turn === 4 ? "awaiting_confirmation" : "ticket_collection", trace_id: `intake-trace-${turn}`, ticket_intake: intake };
      state.runs = [{ id: result.run_id, conversation_id: conversation.id, status: "completed", trace_id: result.trace_id, result, events: [] }];
      state.messages.push({ id: body.client_message_id, role: "user", content: body.content }, { id: result.message_id, role: "assistant", content: result.answer });
      if (turn === 4) state.drafts = [{ id: "intake-draft", conversation_id: conversation.id, version: 1, status: "pending", requires_details: false,
        draft: { ...draftContent, title: "VPN错误E42", problem: "VPN错误E42", description: "VPN错误E42\n影响范围：仅本人无法办公", attempted_steps: [] }, confirmation_token: "intake-token", expires_at: "2099-01-01T00:00:00Z" }];
      return json(route, { id: result.run_id, status: "queued", trace_id: result.trace_id, result: null }, 202);
    });
    await page.route("**/api/v1/runs/intake-*/events**", (route) => route.fulfill({ status: 200, contentType: "text/event-stream", body: sse([["final", state.runs[0].result as Record<string, unknown>]]) }));
    await page.setViewportSize({ width, height: 900 }); await page.goto("/");
    expect(new URL(page.url()).origin).toBe(new URL(baseURL!).origin); await expect(page).toHaveTitle(/IT/);
    for (let index = 0; index < replies.length; index += 1) {
      await page.getByLabel("输入 IT 问题").fill(replies[index]); await page.getByRole("button", { name: "发送请求" }).click();
      if (index < 3) {
        await expect(page.getByRole("heading", { name: "请补充工单信息" })).toBeVisible();
        await expect(page.getByRole("button", { name: "确认并创建工单" })).toHaveCount(0);
        await expect(page.getByText("人工交接未确认", { exact: true })).toHaveCount(0);
        await page.reload(); await expect(page.getByRole("region", { name: "正在收集工单信息" })).toBeVisible();
        if (index === 1) {
          await expect(page.getByRole("region", { name: "正在收集工单信息" })).toContainText("VPN错误E42");
          await page.getByRole("button", { name: /另一个会话/ }).click();
          await expect(page.getByRole("region", { name: "正在收集工单信息" })).toHaveCount(0);
          await page.getByRole("button", { name: /VPN故障会话/ }).click();
          await expect(page.getByRole("region", { name: "正在收集工单信息" })).toContainText("VPN错误E42");
        }
        expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
        await auditScreenshot(page, `intake-${index + 1}-${width}.png`);
      } else {
        await expect(page.getByLabel("故障现象或错误信息")).toHaveValue("VPN错误E42");
        await expect(page.getByLabel("影响范围及工作阻断情况")).toHaveValue("仅本人无法办公");
        await expect(page.getByLabel("草稿已尝试步骤")).toHaveValue("");
        await expect(page.getByRole("button", { name: "确认并创建工单" })).toBeDisabled();
        await page.reload(); await expect(page.getByLabel("故障现象或错误信息")).toHaveValue("VPN错误E42");
        expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
        await auditScreenshot(page, `intake-ready-${width}.png`);
      }
    }
    const requests = state.received.filter((request) => request.path === `/conversations/${conversation.id}/runs` && request.method === "POST");
    expect(requests).toHaveLength(4); expect(requests.every((request) => request.csrf === principal.csrf_token)).toBe(true);
    expect(requests.every((request) => !Object.hasOwn(request.body as object, "ticket_intake_context") && !Object.hasOwn(request.body as object, "history"))).toBe(true);
    expect(state.received.filter((request) => request.path.endsWith("/confirm"))).toHaveLength(0);
    expect(await page.locator("vite-error-overlay").count()).toBe(0); expect(errors).toEqual([]);
  });
}

for (const width of [1440, 390]) {
  test(`ticket lookup clarifies, restores and selects the exact object at ${width}px (Mock HTTP)`, async ({ page, baseURL }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => { if (message.type() === "error" || message.type() === "warning") errors.push(message.text()); });
    const state = await setup(page);
    const one = "IT-2026-0001", two = "IT-2026-0002";
    const lookup = { schema_version: 1, user_id: principal.id, conversation_id: conversation.id,
      outcome: "clarification", ticket_number: null, basis: null, reason: "ambiguous_ticket_number", candidates: [one, two] };
    const final = { run_id: "clarify-1", message_id: "clarify-answer", answer: "有多张工单，请选择要查询的一张。", final_state: "ticket_lookup_clarification", trace_id: "lookup-trace", ticket_lookup: lookup };
    state.messages = [{ id: final.message_id, role: "assistant", content: final.answer }];
    state.runs = [{ id: final.run_id, conversation_id: conversation.id, status: "completed", trace_id: final.trace_id, result: final, events: [], created_at: "2026-10-03" }];
    await page.route("**/api/v1/runs/run-1/events**", (route) => route.fulfill({ status: 200, contentType: "text/event-stream",
      body: sse([["run_started", { run_id: "run-1", trace_id: final.trace_id }], ["citations", { citations: [], trace_id: final.trace_id }], ["final", state.runs[0].result as Record<string, unknown>]]) }));
    await page.route("**/api/v1/conversations**", async (route) => {
      const path = new URL(route.request().url()).pathname.slice("/api/v1".length);
      if (path === "/conversations") return json(route, { conversations: [conversation, { ...conversation, id: "conversation-2", title: "另一个会话" }], next_cursor: null });
      if (path.startsWith("/conversations/conversation-2/")) return json(route, path.endsWith("/runs") ? { runs: [], next_cursor: null } : { messages: [], next_cursor: null });
      if (path === `/conversations/${conversation.id}/runs` && route.request().method() === "POST") {
        const body = route.request().postDataJSON();
        state.received.push({ path, method: "POST", body, csrf: route.request().headers()["x-csrf-token"] });
        const result = { ...final, run_id: "run-1", message_id: "lookup-answer", answer: `工单 ${two} 当前状态为 resolved。`, final_state: "ticket_status",
          ticket_lookup: { ...lookup, outcome: "found", ticket_number: two, basis: "explicit", reason: null, candidates: [] } };
        state.runs = [{ id: "run-1", status: "completed", conversation_id: conversation.id, result, events: [], trace_id: final.trace_id, created_at: "2026-10-03T12:00:00" }];
        state.messages.push({ id: body.client_message_id, role: "user", content: body.content }, { id: result.message_id, role: "assistant", content: result.answer });
        return json(route, { id: "run-1", status: "queued", trace_id: final.trace_id, result: null }, 202);
      }
      return route.fallback();
    });
    await page.setViewportSize({ width, height: 900 }); await page.goto("/");
    expect(new URL(page.url()).origin).toBe(new URL(baseURL!).origin); await expect(page).toHaveTitle(/IT/);
    await expect(page.getByRole("heading", { name: "请选择要查询的工单" })).toBeVisible();
    await expect(page.getByRole("button", { name: `查询工单 ${two}` })).toBeEnabled();
    await page.reload(); await expect(page.getByRole("button", { name: `查询工单 ${two}` })).toBeEnabled();
    await expect(page.getByText("人工交接未确认", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "确认并创建工单" })).toHaveCount(0);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    if (overflow) {
      await auditScreenshot(page, `lookup-overflow-${width}.png`);
      console.log("Lookup layout overflow", await page.evaluate(() => [".conversation-layout", ".conversation-sidebar", ".conversation-list", ".conversation-main", ".work-grid", ".results", ".ticket-lookup-choices"].map((selector) => {
        const element = document.querySelector(selector) as HTMLElement; const style = getComputedStyle(element);
        return { selector, width: element.getBoundingClientRect().width, scrollWidth: element.scrollWidth, minWidth: style.minWidth, columns: style.gridTemplateColumns };
      })));
    }
    expect(overflow).toBe(false);
    await auditScreenshot(page, `lookup-clarification-${width}.png`);
    await page.getByRole("button", { name: /另一个会话/ }).click();
    await expect(page.getByRole("button", { name: `查询工单 ${two}` })).toHaveCount(0);
    await expect(page.getByText("本次检索证据", { exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: /VPN故障会话/ }).click();
    await page.getByRole("button", { name: `查询工单 ${two}` }).click();
    await expect(page.getByText(`工单 ${two} 当前状态为 resolved。`, { exact: true })).toBeVisible();
    const sent = state.received.filter((request) => request.path === `/conversations/${conversation.id}/runs` && request.method === "POST");
    expect(sent).toHaveLength(1); expect(sent[0].body).toMatchObject({ content: two }); expect(sent[0].csrf).toBe("csrf-token");
    await expect(page.getByRole("button", { name: `查询工单 ${two}` })).toHaveCount(0);
    expect(await page.locator("vite-error-overlay").count()).toBe(0); expect(errors).toEqual([]);
    await auditScreenshot(page, `lookup-selected-${width}.png`);
  });

  test(`missing ticket number stays a normal clarification at ${width}px (Mock HTTP)`, async ({ page }) => {
    const state = await setup(page);
    const result = { run_id: "missing-1", message_id: "missing-answer", answer: "请提供要查询的完整工单号。", final_state: "ticket_lookup_clarification", trace_id: "missing-trace",
      ticket_lookup: { schema_version: 1, user_id: principal.id, conversation_id: conversation.id, outcome: "clarification", ticket_number: null, basis: null, reason: "missing_ticket_number", candidates: [] } };
    state.runs = [{ id: result.run_id, status: "completed", trace_id: result.trace_id, result, events: [] }];
    await page.setViewportSize({ width, height: 900 }); await page.goto("/");
    await expect(page.getByRole("heading", { name: "请选择要查询的工单" })).toBeVisible();
    await expect(page.getByText("在输入框中填写完整工单号（如 IT-2026-0001），然后发送请求。", { exact: true })).toBeVisible();
    await expect(page.getByText("人工交接未确认", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /查询工单 IT/ })).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
    await auditScreenshot(page, `lookup-missing-${width}.png`);
  });
}
async function json(route: Route, value: unknown, status = 200) { await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) }); }
async function auditScreenshot(page: Page, filename: string) {
  const directory = process.env.AUDIT_EVIDENCE_DIRECTORY;
  if (!directory) return;
  await mkdir(directory, { recursive: true });
  await page.screenshot({ path: join(directory, filename), fullPage: true });
}

async function setup(page: Page, role = "employee") {
  const state = { accounts: [{ id: "managed-employee", display_name: "王琪", role: "employee", enabled: true, version: 1 }], messages: [] as Array<Record<string, unknown>>, runs: [] as Array<Record<string, unknown>>, drafts: [] as Array<Record<string, unknown>>, tickets: [] as Array<typeof ticket>, escalations: [] as Array<Record<string, unknown>>, jobs: [] as Array<Record<string, unknown>>, documents: [article] as Array<Record<string, unknown>>, revisions: [{ id: "revision-original", active: true, document_count: 1, created_at: "2026-01-01" }] as Array<Record<string, unknown>>, received: [] as Array<{ method: string; path: string; body: unknown; csrf: string | undefined }> };
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request(); const path = new URL(request.url()).pathname.slice("/api/v1".length); const method = request.method(); let body: unknown = null;
    if (request.headers()["content-type"]?.includes("application/json")) body = request.postDataJSON();
    state.received.push({ method, path, body, csrf: request.headers()["x-csrf-token"] });
    if (path === "/me") return json(route, { ...principal, role });
    if (path === "/auth/logout") return route.fulfill({ status: 204 });
    if (path === "/conversations" && method === "GET") return json(route, { conversations: [conversation], next_cursor: null });
    if (path === "/conversations" && method === "POST") return json(route, { ...conversation, id: "conversation-2", title: "新会话" }, 201);
    if (path.endsWith("/archive")) return route.fulfill({ status: 204 });
    if (path.endsWith("/messages")) return json(route, { messages: state.messages, next_cursor: null });
    if (path.endsWith("/runs") && method === "GET") return json(route, { runs: state.runs, next_cursor: null });
    if (path.endsWith("/runs") && method === "POST") {
      const requestBody = body as { content: string }; const final = { run_id: "run-1", message_id: "answer-1", answer: "请检查VPN证书是否过期。", final_state: "answered", trace_id: "trace-1", status: "completed" };
      state.messages = [{ id: "question-1", role: "user", content: requestBody.content }, { id: final.message_id, role: "assistant", content: final.answer }];
      state.runs = [{ id: "run-1", conversation_id: conversation.id, status: "completed", trace_id: "trace-1", result: final, events: [], created_at: "2026-01-01" }];
      return json(route, { id: "run-1", status: "queued", trace_id: "trace-1", result: null }, 202);
    }
    if (path.endsWith("/events")) return route.fulfill({ status: 200, contentType: "text/event-stream", body: sse([["run_started", { run_id: "run-1", trace_id: "trace-1" }], ["citations", { citations: [{ document_id: article.id, source_title: article.title, source_path: "vpn.md", chunk_index: 0, excerpt: "检查客户端证书。" }], trace_id: "trace-1" }], ["final", state.runs[0]?.result as Record<string, unknown>]]) });
    if (path.startsWith("/runs/")) return json(route, state.runs[0]);
    if (path === "/ticket-drafts") return json(route, { drafts: state.drafts });
    if (path.startsWith("/ticket-drafts/") && method === "PATCH") {
      const input = body as { version: number; draft: unknown }; const updated = { ...state.drafts[0], version: input.version + 1, confirmation_token: "token-v2", draft: input.draft }; state.drafts = [updated]; return json(route, updated);
    }
    if (path.endsWith("/confirm")) { state.drafts = state.drafts.map((item) => ({ ...item, status: "confirmed", ticket_number: ticket.ticket_number, confirmation_token: null })); return json(route, { ticket_number: ticket.ticket_number, status: "pending" }, 201); }
    if (path === "/tickets") return json(route, { tickets: state.tickets, next_cursor: null });
    if (path.startsWith("/tickets/") && method === "GET") return json(route, state.tickets[0]);
    if (path.endsWith("/comments")) {
      const input = body as { content: string; visibility: string }; const updated = { ...state.tickets[0], version: state.tickets[0].version + 1, comments: [...state.tickets[0].comments, { id: `comment-${state.tickets[0].version}`, author_id: principal.id, content: input.content, visibility: input.visibility, created_at: "2026-10-03T00:02:00Z" }] }; state.tickets = [updated]; return json(route, updated.comments.at(-1), 201);
    }
    if (path.includes("/comments/") && method === "PATCH") {
      const input = body as { version: number; visibility: string }; const commentId = path.split("/").at(-1);
      state.tickets = [{ ...state.tickets[0], version: input.version + 1, comments: state.tickets[0].comments.map((entry) => entry.id === commentId ? { ...entry, visibility: input.visibility } : entry) }];
      return json(route, { id: commentId, visibility: input.visibility, version: input.version + 1 });
    }
    if (path.startsWith("/tickets/") && method === "PATCH") { state.tickets = [{ ...state.tickets[0], ...body as object, version: state.tickets[0].version + 1, audit: [{ id: "audit-1", actor_id: principal.id, event_type: "updated", details: body, created_at: "2026-01-02" }] }]; return json(route, state.tickets[0]); }
    if (path === "/admin/accounts") return json(route, { items: state.accounts, next_cursor: null });
    if (path.startsWith("/admin/accounts/") && method === "PATCH") { state.accounts = state.accounts.map((item) => ({ ...item, ...body as object, version: item.version + 1 })); return json(route, state.accounts[0]); }
    if (path === "/support/assignees") return json(route, { users: [{ id: "support-1", display_name: "李工", role: "support" }] });
    if (path === "/escalations") return json(route, { escalations: state.escalations, next_cursor: null });
    if (path.startsWith("/escalations/") && method === "PATCH") { state.escalations = [{ ...state.escalations[0], ...body as object, version: Number(state.escalations[0].version) + 1 }]; return json(route, state.escalations[0]); }
    if (path === "/knowledge/documents") return json(route, { documents: state.documents, next_cursor: null });
    if (path.startsWith("/knowledge/documents/") && method === "GET") return json(route, article);
    if (path === "/knowledge/jobs") return json(route, { jobs: state.jobs, next_cursor: null });
    if (path === "/knowledge/uploads") { const job = { id: "job-1", title: "新VPN手册", access_level: "employee", status: "ready", document_id: null, created_at: "2026-01-02", sections: [{ heading: "新步骤", content: "重新签发VPN证书。" }] }; state.jobs = [job]; return json(route, job, 202); }
    if (path.startsWith("/knowledge/jobs/") && method === "GET") return json(route, state.jobs[0]);
    if (path.endsWith("/publish")) return json(route, { index_job_id: "index-publish", status: "queued" }, 202);
    if (path.endsWith("/deactivate")) return json(route, { index_job_id: "index-deactivate", status: "queued" }, 202);
    if (path === "/admin/knowledge/index-jobs/index-publish") { state.jobs = state.jobs.map((item) => ({ ...item, status: "published" })); if (!state.documents.some((item) => item.id === "doc-2")) state.documents.push({ ...article, id: "doc-2", title: "新VPN手册" }); state.revisions = [{ id: "revision-new", active: true, document_count: 2, created_at: "2026-01-02" }, { id: "revision-original", active: false, document_count: 1, created_at: "2026-01-01" }]; return json(route, { id: "index-publish", status: "completed", result: { revision_id: "revision-new", status: "active" }, error: null }); }
    if (path === "/admin/knowledge/index-jobs/index-deactivate") { state.documents = state.documents.map((item) => item.id === "doc-2" ? { ...item, status: "inactive" } : item); return json(route, { id: "index-deactivate", status: "completed", result: { revision_id: "revision-deactivated", status: "active" }, error: null }); }
    if (path === "/knowledge/revisions") return json(route, { revisions: state.revisions });
    if (path.endsWith("/activate")) { const revisionId = path.split("/").at(-2); state.revisions = state.revisions.map((item) => ({ ...item, active: item.id === revisionId })); return json(route, { revision_id: revisionId, status: "active" }); }
    if (path.endsWith("/feedback")) return route.fulfill({ status: 204 });
    return json(route, { detail: `Unexpected fixture route: ${method} ${path}` }, 404);
  });
  return state;
}

test("production requires enterprise login and exposes no editable identity", async ({ page }) => {
  await setup(page); await page.route("**/api/v1/me", (route) => json(route, { detail: "authentication_required" }, 401)); await page.goto("/");
  await expect(page.getByRole("link", { name: "使用企业账号登录" })).toBeVisible(); await expect(page.getByLabel("用户标识")).toHaveCount(0);
});

test("recovers a missing final frame, reloads history, and opens citation source", async ({ page }) => {
  await setup(page); await page.route("**/api/v1/runs/*/events?*", (route) => route.fulfill({ status: 200, contentType: "text/event-stream", body: "" })); await page.goto("/");
  await page.getByLabel("输入 IT 问题").fill("VPN证书故障"); await page.getByRole("button", { name: "发送请求" }).click(); await expect(page.getByText("请检查VPN证书是否过期。", { exact: true })).toBeVisible();
  await page.reload(); await expect(page.getByText("请检查VPN证书是否过期。", { exact: true })).toHaveCount(1); await page.getByRole("button", { name: "知识资料", exact: true }).click(); await page.getByRole("button", { name: /VPN排障手册/ }).click(); await expect(page.getByText("请检查客户端证书有效期。")).toBeVisible();
});

test("ticket lookup renders public progress in the assistant answer and restores it (Mock HTTP)", async ({ page }) => {
  const state = await setup(page);
  const answer = "工单 IT-2026-0001 当前状态为 resolved。最近可见记录（2026-10-03 00:02:00 UTC，支持回复）：合成支持公开进度";
  const result = { run_id: "run-progress", message_id: "progress-answer", trace_id: "progress-trace", status: "completed", final_state: "ticket_status", answer };
  state.messages = [{ id: "progress-query", role: "user", content: "查询工单 IT-2026-0001 的进度" }, { id: result.message_id, role: "assistant", content: answer }];
  state.runs = [{ id: result.run_id, conversation_id: conversation.id, status: "completed", trace_id: result.trace_id, result, events: [], created_at: "2026-10-03T00:03:00Z" }];
  await page.goto("/");
  await expect(page.locator(".message-assistant")).toContainText("合成支持公开进度");
  await expect(page.locator(".outcome-card")).toContainText("已查询工单");
  await expect(page.locator(".message-assistant")).toContainText("2026-10-03 00:02:00 UTC");
  await page.reload();
  await expect(page.locator(".message-assistant")).toHaveCount(1);
  await expect(page.locator(".message-assistant")).toContainText(answer);
});

test("unknown run submission retries with the same body and key", async ({ page }) => {
  await setup(page); const requests: Array<{ key: string | undefined; body: unknown }> = []; let attempt = 0;
  await page.route("**/api/v1/conversations/*/runs", async (route) => { if (route.request().method() !== "POST") return route.fallback(); requests.push({ key: route.request().headers()["idempotency-key"], body: route.request().postDataJSON() }); attempt += 1; if (attempt === 1) return route.abort(); return route.fallback(); });
  await page.goto("/"); await page.getByLabel("输入 IT 问题").fill("VPN故障"); await page.getByRole("button", { name: "发送请求" }).click(); await page.getByRole("button", { name: "恢复发送结果并重试" }).click(); await expect(page.getByText("请检查VPN证书是否过期。", { exact: true })).toBeVisible(); expect(requests).toHaveLength(2); expect(requests[1]).toEqual(requests[0]);
});

test("draft edits rotate credentials and unknown confirmations retain their key", async ({ page }) => {
  const state = await setup(page); state.drafts = [{ id: "draft-1", conversation_id: conversation.id, version: 1, draft: draftContent, confirmation_token: "token-v1", expires_at: "2099-01-01", status: "pending" }]; const confirmations: Array<{ key: string | undefined; body: unknown }> = [];
  await page.route("**/api/v1/ticket-drafts/*/confirm", async (route) => { confirmations.push({ key: route.request().headers()["idempotency-key"], body: route.request().postDataJSON() }); if (confirmations.length === 1) return route.abort(); return route.fallback(); });
  await page.goto("/"); await page.getByLabel("草稿标题").fill("修订的VPN故障"); await expect(page.getByRole("button", { name: "确认并创建工单" })).toBeDisabled(); await page.getByRole("button", { name: "保存修改并重新签名" }).click(); await page.getByRole("checkbox", { name: /我已核对/ }).check(); await page.getByRole("button", { name: "确认并创建工单" }).click(); await page.getByRole("button", { name: "查询确认结果并重试" }).click();
  await expect(page.getByText(/工单 IT-2026-0001 已创建/)).toBeVisible(); expect(confirmations[1]).toEqual(confirmations[0]); expect(confirmations[0].body).toEqual({ version: 2, confirmation_token: "token-v2" }); expect(state.received.filter((item) => item.method === "PATCH")[0].csrf).toBe("csrf-token");
});

test("employee supplements and reopens a resolved ticket", async ({ page }) => {
  const state = await setup(page); state.tickets = [{ ...ticket }]; await page.goto("/"); await page.getByRole("button", { name: "我的工单", exact: true }).click(); await page.getByLabel("补充信息").fill("故障仍然存在，补充客户端日志。"); await page.getByRole("button", { name: "保存补充信息" }).click(); await expect(page.getByText("故障仍然存在，补充客户端日志。", { exact: true })).toBeVisible(); await page.getByRole("button", { name: "重新开启工单" }).click(); await expect(page.getByText("工单已重新开启。", { exact: true })).toBeVisible(); expect(state.tickets[0].status).toBe("in_progress"); expect(state.tickets[0].version).toBe(3);
});

test("support assigns escalation and updates ticket with audit", async ({ page }) => {
  const state = await setup(page, "support"); state.tickets = [{ ...ticket, status: "pending" }]; state.escalations = [{ id: "escalation-1", user_id: principal.id, conversation_id: conversation.id, run_id: "run-1", reason: "需人工检查网络设备", status: "pending", version: 1, assignee_id: null, created_at: "2026-01-01" }]; await page.goto("/"); await page.getByRole("button", { name: "支持工作台", exact: true }).click(); await page.getByLabel("升级处理人").selectOption("support-1"); await page.getByLabel("升级状态").selectOption("in_progress"); await page.getByRole("button", { name: "保存升级处理" }).click(); await expect.poll(() => state.escalations[0].assignee_id).toBe("support-1"); await page.getByRole("tab", { name: "企业工单" }).click(); await page.getByRole("combobox", { name: "处理人", exact: true }).selectOption("support-1"); await page.getByLabel("工单状态").selectOption("in_progress"); await page.getByRole("button", { name: "保存工单处理" }).click(); await expect(page.getByText("工单处理状态已保存。")).toBeVisible(); await page.getByText("处理审计记录（1）").click(); await expect(page.getByText("updated", { exact: true })).toBeVisible();
});

test("administrator previews upload, publishes, deactivates and rolls back", async ({ page }) => {
  const state = await setup(page, "admin"); await page.goto("/"); await page.getByRole("button", { name: "知识管理", exact: true }).click(); await page.getByLabel("文档标题").fill("新VPN手册"); await page.getByLabel("知识文件").setInputFiles({ name: "vpn.md", mimeType: "text/markdown", buffer: Buffer.from("# 新步骤\n重新签发VPN证书。") }); await page.getByRole("button", { name: "上传并准备预览" }).click(); await expect(page.getByText("重新签发VPN证书。", { exact: true })).toBeVisible(); await expect(page.getByRole("button", { name: "发布知识资料" })).toBeDisabled(); await page.getByRole("checkbox", { name: /已核对正文/ }).check(); await page.getByRole("button", { name: "发布知识资料" }).click(); await expect(page.getByText(/知识资料已发布/)).toBeVisible(); const row = page.locator(".document-admin-row").filter({ hasText: "新VPN手册" }); await row.getByRole("button", { name: "停用文档" }).click(); await row.getByRole("button", { name: "确认停用" }).click(); await expect.poll(() => state.documents.find((item) => item.id === "doc-2")?.status).toBe("inactive"); const revision = page.locator(".revision-row").filter({ hasText: "revision-original" }); await revision.getByRole("button", { name: "激活此版本" }).click(); await revision.getByRole("button", { name: "确认激活版本" }).click(); await expect(page.getByText(/历史知识版本已激活/)).toBeVisible(); expect(state.revisions.find((item) => item.id === "revision-original")?.active).toBe(true);
});

test("employee workspace remains usable on a narrow viewport", async ({ page }) => {
  await setup(page); await page.setViewportSize({ width: 390, height: 844 }); await page.goto("/"); await expect(page.getByLabel("输入 IT 问题")).toBeVisible(); const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth); expect(overflow).toBe(false);
});

test("administrator changes member role, disables and enables access", async ({ page }) => {
  const state = await setup(page, "admin"); await page.goto("/"); await page.getByRole("button", { name: "账号管理", exact: true }).click();
  await page.getByRole("combobox", { name: "账号角色", exact: true }).selectOption("support"); await page.getByRole("checkbox", { name: "允许使用此账号" }).uncheck(); await page.getByRole("button", { name: "保存账号权限", exact: true }).click();
  await expect(page.getByText("账号权限已保存。", { exact: true })).toBeVisible(); expect(state.accounts[0]).toMatchObject({ role: "support", enabled: false, version: 2 });
  await page.getByRole("checkbox", { name: "允许使用此账号" }).check(); await page.getByRole("button", { name: "保存账号权限", exact: true }).click(); await expect.poll(() => state.accounts[0].enabled).toBe(true); expect(state.accounts[0].version).toBe(3);
  const patches = state.received.filter((item) => item.path.startsWith("/admin/accounts/") && item.method === "PATCH"); expect(patches[0].body).toMatchObject({ expected_version: 1 }); expect(patches[1].body).toMatchObject({ expected_version: 2 }); expect(patches.every((item) => item.csrf === "csrf-token")).toBe(true);
});

for (const width of [1440, 390]) {
  test(`PDF cell preview and legacy warning remain usable at ${width}px`, async ({ page }) => {
    const state = await setup(page, "admin");
    state.jobs = [{ id: "pdf-job", title: "服务响应规范", access_level: "employee", status: "ready", document_id: null,
      created_at: "2026-01-01", sections: [{ heading: "PDF 第 1 页", content: "结构化行", context: "适用条件：工作日。响应时间不等于解决时间。",
        tables: [{ id: "p1-t1", page_number: 1, headers: ["服务等级", "响应时间（分钟）", "适用范围"],
          rows: [["普通", "240", "一般问题"], ["紧急", "15", "业务中断"]] }] }] }];
    state.documents.push({ ...article, id: "legacy-pdf", title: "旧版PDF规范", requires_reparse: true });
    await page.setViewportSize({ width, height: 960 });
    const errors: string[] = []; page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/"); await page.getByRole("button", { name: "知识管理", exact: true }).click();
    await page.getByRole("button", { name: /服务响应规范/ }).click();
    const table = page.getByRole("table", { name: /第 1 页.*p1-t1/ });
    await expect(table.getByRole("row").filter({ hasText: "紧急" })).toHaveText("紧急15业务中断");
    await expect(table.getByRole("row").filter({ hasText: "普通" })).toHaveText("普通240一般问题");
    await expect(page.getByText("旧版 PDF 待重新上传并核对，暂不提供检索。")).toBeVisible();
    await expect(page.getByRole("button", { name: "发布知识资料" })).toBeDisabled();
    await page.getByRole("checkbox", { name: /已核对正文/ }).check();
    await expect(page.getByRole("button", { name: "发布知识资料" })).toBeEnabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(errors).toEqual([]);
    await auditScreenshot(page, `pdf-preview-${width}.png`);
  });
}

test("PDF citation opens its cited revision instead of the updated source", async ({ page }) => {
  await setup(page); let requestedRevision: string | null = null;
  await page.route("**/api/v1/runs/*/events?*", (route) => route.fulfill({ status: 200, contentType: "text/event-stream",
    body: sse([["run_started", { run_id: "run-1", trace_id: "trace-1" }], ["citations", { citations: [{ document_id: "doc-1",
      source_title: "服务响应规范", source_path: "response.pdf", chunk_index: 1, excerpt: "服务等级：紧急；响应时间（分钟）：15；工作日适用。",
      page_number: 1, table_id: "p1-t1", row_index: 2, index_revision: "revision-cited" }] }],
      ["final", { run_id: "run-1", answer: "紧急响应15分钟。", message_id: "answer-1", final_state: "answered", trace_id: "trace-1" }]]) }));
  await page.route("**/api/v1/knowledge/documents/doc-1?*", (route) => {
    requestedRevision = new URL(route.request().url()).searchParams.get("index_revision");
    return json(route, { ...article, title: "服务响应规范", index_revision: "revision-cited", version: "hash-cited",
      sections: [{ heading: "PDF 第 1 页", content: "紧急响应15分钟。" }] });
  });
  await page.goto("/"); await page.getByLabel("输入 IT 问题").fill("紧急响应需要多久");
  await page.getByRole("button", { name: "发送请求" }).click();
  await expect(page.getByText(/第 1 页.*表 p1-t1.*数据行 2/)).toBeVisible();
  await page.getByRole("button", { name: "查看原文 ↗" }).click();
  await expect(page.getByText("引用时的知识版本：revision-cited")).toBeVisible();
  await expect(page.getByText("紧急响应15分钟。", { exact: true })).toBeVisible();
  expect(requestedRevision).toBe("revision-cited");
  await auditScreenshot(page, "pdf-cited-source.png");
});

for (const recorded of [false, true]) {
  test(`human support outcome reports durable record presence: ${recorded}`, async ({ page }) => {
    const state = await setup(page);
    await page.route("**/api/v1/runs/*/events?*", (route) => {
      const final = { run_id: "run-1", message_id: "answer-1", answer: "需要人工协助。", final_state: "handoff", trace_id: "trace-1",
        status: recorded ? "completed" : "failed", ...(recorded ? { escalation_id: "esc-42" } : { error: "processing_failed" }) };
      state.runs = [{ id: "run-1", conversation_id: conversation.id, status: final.status, result: final, events: [], trace_id: "trace-1", created_at: "2026-01-01" }];
      return route.fulfill({ status: 200, contentType: "text/event-stream", body: sse([
        ["run_started", { run_id: "run-1", trace_id: "trace-1" }], ["final", final]]) });
    });
    await page.goto("/"); await page.getByLabel("输入 IT 问题").fill("请转人工");
    await page.getByRole("button", { name: "发送请求" }).click();
    await expect(page.getByRole("heading", { name: recorded ? "人工支持请求已记录" : "人工交接未确认" })).toBeVisible();
    await expect(page.getByText("已转交人工支持", { exact: true })).toHaveCount(0);
    if (recorded) {
      await expect(page.getByText("请求编号：esc-42")).toBeVisible();
      await expect(page.getByText("提交时状态：待处理。尚未确认支持人员接单。")).toBeVisible();
    }
    await auditScreenshot(page, recorded ? "handoff-recorded.png" : "handoff-unconfirmed.png");
  });
}

for (const width of [1440, 390]) {
  test(`Markdown source code preview and migration warning at ${width}px (controlled HTTP)`, async ({ page }) => {
    const state = await setup(page, "admin");
    const command = '```powershell\n# 命令内注释不是章节\nGet-Service -Name NetworkService\n\n    retry = 2\n    endpoint = "https://example.invalid/very/long/configuration/value/that/must/wrap/on/a/narrow/viewport"\n```';
    state.jobs = [{ id: "md-job", title: "会议室命令指南", access_level: "employee", status: "ready", document_id: null,
      created_at: "2026-01-01", requires_reparse: false, sections: [{ heading: "E-42", content: command,
        section_path: ["会议室指南", "B200", "E-42"], blocks: [{ block_type: "code", content: command,
          char_start: 30, char_end: 30 + command.length, contains_code: true }] }] }];
    state.documents.push({ ...article, id: "legacy-md", title: "旧版命令资料", requires_reparse: true, reparse_reason: "markdown_structure" });
    await page.setViewportSize({ width, height: 960 });
    const errors: string[] = []; page.on("pageerror", (error) => errors.push(error.message));
    await page.goto("/"); await page.getByRole("button", { name: "知识管理", exact: true }).click();
    await page.getByRole("button", { name: /会议室命令指南/ }).click();
    await expect(page.locator(".source-code code")).toHaveCount(1);
    expect(await page.locator(".source-code code").textContent()).toBe(command);
    await expect(page.getByText("会议室指南 › B200 › E-42")).toBeVisible();
    await expect(page.getByRole("heading", { name: "命令内注释不是章节" })).toHaveCount(0);
    await expect(page.getByText("旧版 Markdown 待重新上传并核对，暂不进入新版本检索。")).toBeVisible();
    await expect(page.getByRole("button", { name: "发布知识资料" })).toBeDisabled();
    await page.getByRole("checkbox", { name: /已核对正文/ }).check();
    await expect(page.getByRole("button", { name: "发布知识资料" })).toBeEnabled();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(await page.title()).not.toBe("");
    await expect(page.locator("vite-error-overlay")).toHaveCount(0);
    expect(errors).toEqual([]);
    await auditScreenshot(page, `markdown-preview-${width}.png`);
  });
}
