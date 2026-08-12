import { expect, test, type Page } from "@playwright/test";

const draft = {
  title: "VPN 无法连接",
  category: "network",
  priority: "high",
  description: "连接客户端持续超时",
  attempted_steps: ["重启客户端"],
};

function sse(...events: Array<[string, Record<string, unknown>]>) {
  return events.map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join("");
}

async function mockStream(page: Page, body: string) {
  await page.route("**/api/conversations/*/messages:stream", async (route) => {
    await route.fulfill({ status: 200, contentType: "text/event-stream", body });
  });
}

async function send(page: Page, message = "VPN 连不上怎么办？") {
  await page.goto("/");
  await page.getByLabel("输入 IT 问题").fill(message);
  await page.getByRole("button", { name: "发送请求" }).click();
}

test("renders a complete API stream and ignores unknown events", async ({ page }) => {
  await mockStream(
    page,
    sse(
      ["run_started", { run_id: "run-12345678", message_id: "user-msg", trace_id: "trace-1" }],
      ["node_completed", { node: "retrieve_evidence", step_count: 2, trace_id: "trace-1" }],
      ["unrecognized", { value: "ignored" }],
      ["citations", { citations: [{ document_id: "vpn", source_title: "VPN 排障手册", source_path: "vpn-connection.md", chunk_index: 0, excerpt: "检查客户端证书。" }], trace_id: "trace-1" }],
      ["final", { run_id: "run-12345678", message_id: "assistant-msg", answer: "请先检查客户端证书是否过期。", final_state: "answered", trace_id: "trace-1" }],
    ),
  );

  await send(page);
  await expect(page.getByText("请先检查客户端证书是否过期。")).toBeVisible();
  await expect(page.getByText("正在检索知识")).toBeVisible();
  await expect(page.getByText("VPN 排障手册")).toBeVisible();
});

test("shows the status returned for a ticket lookup", async ({ page }) => {
  await mockStream(
    page,
    sse(
      ["run_started", { run_id: "run-lookup", message_id: "user-msg", trace_id: "trace-lookup" }],
      ["node_completed", { node: "lookup_ticket", step_count: 2, trace_id: "trace-lookup" }],
      ["citations", { citations: [], trace_id: "trace-lookup" }],
      ["final", {
        run_id: "run-lookup",
        message_id: "assistant-lookup",
        answer: "工单 IT-2026-0001 当前状态为 pending。",
        final_state: "ticket_status",
        trace_id: "trace-lookup",
      }],
    ),
  );

  await send(page, "查询工单 IT-2026-0001");
  await expect(page.getByText("工单 IT-2026-0001 当前状态为 pending。")).toBeVisible();
  await expect(page.getByText("正在查询工单")).toBeVisible();
});

test("confirms an untouched draft using its API token and trace", async ({ page }) => {
  let confirmation: Record<string, unknown> | undefined;
  let confirmationTrace: string | undefined;
  await mockStream(
    page,
    sse(
      ["run_started", { run_id: "run-ticket", message_id: "user-msg", trace_id: "trace-2" }],
      ["ticket_draft", { draft, confirmation_token: "bound-token", trace_id: "trace-2" }],
      ["final", { run_id: "run-ticket", message_id: "assistant-msg", answer: "请确认工单草稿。", final_state: "ticket_draft", trace_id: "trace-2" }],
    ),
  );
  await page.route("**/api/conversations/*/ticket-confirmations", async (route) => {
    confirmation = route.request().postDataJSON() as Record<string, unknown>;
    confirmationTrace = route.request().headers()["x-trace-id"];
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ ticket_number: "INC-1001", status: "open" }),
    });
  });

  await send(page);
  await page.getByRole("checkbox", { name: /我已核对/ }).check();
  await page.getByRole("button", { name: "确认并创建工单" }).click();
  await expect(page.getByText(/INC-1001/)).toBeVisible();
  expect(confirmation).toMatchObject({
    user_id: "u-001",
    confirmation_token: "bound-token",
    draft,
  });
  expect(confirmation?.idempotency_key).toEqual(expect.any(String));
  expect(confirmationTrace).toBe("trace-2");
});

test("editing a draft disables confirmation and never submits the old token", async ({ page }) => {
  let confirmationRequests = 0;
  await mockStream(
    page,
    sse(
      ["run_started", { run_id: "run-edited", message_id: "user-msg", trace_id: "trace-3" }],
      ["ticket_draft", { draft, confirmation_token: "must-not-be-used", trace_id: "trace-3" }],
      ["final", { run_id: "run-edited", message_id: "assistant-msg", answer: "请确认工单草稿。", final_state: "ticket_draft", trace_id: "trace-3" }],
    ),
  );
  await page.route("**/api/conversations/*/ticket-confirmations", async (route) => {
    confirmationRequests += 1;
    await route.fulfill({ status: 500, body: "unexpected request" });
  });

  await send(page);
  await page.getByLabel("标题").fill("用户修改后的标题");
  await expect(page.getByText(/原确认令牌已失效/)).toBeVisible();
  await expect(page.getByRole("button", { name: "确认并创建工单" })).toBeDisabled();
  expect(confirmationRequests).toBe(0);
});
