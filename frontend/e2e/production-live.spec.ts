import { expect, test, type Browser, type BrowserContext, type Page } from "@playwright/test";

type Role = "employee" | "support" | "admin";
interface Session { context: BrowserContext; page: Page; principal: { id: string; role: Role; csrf_token: string } }
const baseURL = process.env.E2E_BASE_URL;

async function login(browser: Browser, role: Role): Promise<Session> {
  const prefix = `E2E_${role.toUpperCase()}`;
  const username = process.env[`${prefix}_USERNAME`]; const password = process.env[`${prefix}_PASSWORD`];
  if (!username || !password) throw new Error(`${prefix}_USERNAME and ${prefix}_PASSWORD must be set to isolated test-realm credentials.`);
  const context = await browser.newContext({ baseURL, ignoreHTTPSErrors: true }); const page = await context.newPage();
  await page.goto("/"); await page.getByRole("link", { name: "使用企业账号登录" }).click();
  await page.locator("#username").fill(username); await page.locator("#password").fill(password); await page.locator("#kc-login").click();
  await expect(page.getByRole("navigation", { name: "工作台导航" })).toBeVisible();
  const response = await context.request.get("/api/v1/me"); expect(response.status()).toBe(200); const principal = await response.json() as Session["principal"]; expect(principal.role).toBe(role);
  return { context, page, principal };
}

async function createConversation(page: Page) {
  const createdResponse = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/v1/conversations" && response.request().method() === "POST", { timeout: 15_000 });
  await page.getByRole("button", { name: "新建会话" }).click();
  const response = await createdResponse; expect(response.status()).toBe(201);
  const conversation = await response.json() as { id: string };
  await expect(page.locator(".conversation-toolbar")).toContainText(conversation.id.slice(0, 8));
  await expect(page.getByLabel("输入 IT 问题")).toBeEnabled();
}

test("real OIDC roles authenticate through Keycloak", async ({ browser }) => {
  test.skip(!baseURL, "Set E2E_BASE_URL and isolated Keycloak account environment variables.");
  for (const role of ["employee", "support", "admin"] as const) {
    const session = await login(browser, role);
    try { expect(session.principal.id).toMatch(/^[a-f0-9]{64}$/); await expect(session.page.getByLabel("用户标识")).toHaveCount(0); }
    finally { await session.context.close(); }
  }
});

test("real OIDC, knowledge publication, durable chat, confirmation and support lifecycle", async ({ browser }) => {
  test.skip(!baseURL, "Set E2E_BASE_URL and isolated Keycloak account environment variables to run the production stack.");
  const sessions: Session[] = []; const suffix = `${Date.now()}`; const documentTitle = `VPN验证手册 ${suffix}`; const draftTitle = `VPN员工故障 ${suffix}`;
  try {
    const admin = await login(browser, "admin"); sessions.push(admin);
    await admin.page.getByRole("button", { name: "知识管理", exact: true }).click();
    await admin.page.getByLabel("文档标题").fill(documentTitle);
    const content = "# VPN连接手册\n\n## 适用对象\n员工。\n\n## 操作步骤\n公司VPN错误619先确认互联网连接，再重启VPN客户端并检查系统时间。\n\n## 何时转人工\n重复失败需联系IT支持。";
    await admin.page.getByLabel("知识文件").setInputFiles({ name: `vpn-${suffix}.md`, mimeType: "text/markdown", buffer: Buffer.from(content) });
    await admin.page.getByRole("button", { name: "上传并准备预览" }).click();
    await expect(admin.page.getByRole("button", { name: "发布知识资料" })).toBeVisible({ timeout: 45_000 });
    await expect(admin.page.getByRole("article", { name: "发布预览" })).toContainText("公司VPN错误619先确认互联网连接");
    await admin.page.getByRole("checkbox", { name: /已核对正文/ }).check();
    const publishedResponse = admin.page.waitForResponse((response) => response.url().endsWith("/publish") && response.request().method() === "POST");
    await admin.page.getByRole("button", { name: "发布知识资料" }).click();
    const publish = await publishedResponse; expect(publish.status()).toBe(202); const indexJobId = (await publish.json() as { index_job_id: string }).index_job_id;
    await expect.poll(async () => {
      const response = await admin.context.request.get(`/api/v1/admin/knowledge/index-jobs/${indexJobId}`);
      expect(response.status()).toBe(200);
      return (await response.json() as { status: string }).status;
    }, { timeout: 45_000, message: "The accepted knowledge index job must finish before publication succeeds" }).toBe("completed");
    await expect(admin.page.locator(".action-notice").filter({ hasText: "知识资料已发布。" })).toBeVisible({ timeout: 10_000 });
    const publication = await admin.context.request.get(`/api/v1/admin/knowledge/index-jobs/${indexJobId}`); expect(publication.status()).toBe(200); const publicationJob = await publication.json() as { status: string; result: { revision_id: string } }; expect(publicationJob.status).toBe("completed"); const publishedRevision = publicationJob.result.revision_id;

    const employee = await login(browser, "employee"); sessions.push(employee);
    await expect(employee.page.getByRole("button", { name: "知识管理", exact: true })).toHaveCount(0);
    expect((await employee.context.request.get("/api/v1/knowledge/jobs")).status()).toBe(403);
    await employee.page.getByRole("button", { name: "知识资料", exact: true }).click();
    await employee.page.getByRole("button", { name: new RegExp(documentTitle) }).click(); await expect(employee.page.getByRole("article", { name: "知识原文" })).toContainText("公司VPN错误619");
    await employee.page.getByRole("button", { name: "运维对话", exact: true }).click(); await createConversation(employee.page);
    await employee.page.getByLabel("输入 IT 问题").fill("公司VPN错误619应该先检查什么？");
    const acceptedResponse = employee.page.waitForResponse((response) => /\/conversations\/[^/]+\/runs$/.test(new URL(response.url()).pathname) && response.request().method() === "POST", { timeout: 15_000 });
    await employee.page.getByRole("button", { name: "发送请求" }).click(); const accepted = await acceptedResponse; expect(accepted.status()).toBe(202); const run = await accepted.json() as { id: string };
    await employee.page.reload();
    await expect(employee.page.getByRole("heading", { name: "已根据知识资料回答", exact: true })).toBeVisible({ timeout: 45_000 });
    const storedRunResponse = await employee.context.request.get(`/api/v1/runs/${run.id}`); expect(storedRunResponse.status()).toBe(200); const storedRun = await storedRunResponse.json() as { status: string; result: { final_state: string }; events: Array<{ sequence: number }> }; expect(storedRun.status).toBe("completed"); expect(storedRun.result.final_state).toBe("answered"); expect(storedRun.events.length).toBeGreaterThan(0);
    expect(storedRun.events.map((event) => event.sequence)).toEqual([...storedRun.events.map((event) => event.sequence)].sort((a, b) => a - b));
    await employee.page.getByRole("button", { name: "仍未解决", exact: true }).click(); await expect(employee.page.getByText("反馈已记录", { exact: true })).toBeVisible();
    await employee.page.getByLabel("输入 IT 问题").fill("VPN一直连接失败，请创建工单"); await employee.page.getByRole("button", { name: "发送请求" }).click();
    await expect(employee.page.getByRole("heading", { name: "请补充工单信息" })).toBeVisible({ timeout: 45_000 });
    await expect(employee.page.getByRole("button", { name: "确认并创建工单" })).toHaveCount(0);
    await employee.page.reload(); await expect(employee.page.getByRole("region", { name: "正在收集工单信息" })).toContainText("VPN一直连接失败");
    await employee.page.getByLabel("输入 IT 问题").fill("仅本人无法办公"); await employee.page.getByRole("button", { name: "发送请求" }).click();
    await expect(employee.page.getByRole("region", { name: "正在收集工单信息" })).toContainText("仅本人无法办公", { timeout: 45_000 });
    await expect(employee.page.getByRole("button", { name: "确认并创建工单" })).toHaveCount(0);
    await employee.page.getByLabel("输入 IT 问题").fill("已经重启客户端，仍然失败"); await employee.page.getByRole("button", { name: "发送请求" }).click();
    await expect(employee.page.getByLabel("草稿标题")).toBeVisible({ timeout: 45_000 }); await employee.page.getByLabel("草稿标题").fill(draftTitle);
    await employee.page.getByRole("button", { name: "保存修改并重新签名" }).click(); await employee.page.getByRole("checkbox", { name: /我已核对/ }).check();
    const confirmationResponse = employee.page.waitForResponse((response) => response.url().endsWith("/confirm") && response.request().method() === "POST");
    await employee.page.getByRole("button", { name: "确认并创建工单" }).click(); const confirmed = await confirmationResponse; expect(confirmed.status()).toBeLessThan(300); const number = (await confirmed.json() as { ticket_number: string }).ticket_number;
    await employee.page.reload(); await expect(employee.page.getByText(number, { exact: true })).toBeVisible();
    await employee.page.getByRole("button", { name: "我的工单", exact: true }).click(); await employee.page.getByRole("button", { name: new RegExp(number) }).click();
    await employee.page.getByLabel("补充信息").fill(`员工补充日志 ${suffix}`); await employee.page.getByRole("button", { name: "保存补充信息" }).click(); await expect(employee.page.getByText(`员工补充日志 ${suffix}`, { exact: true })).toBeVisible();

    const support = await login(browser, "support"); sessions.push(support);
    expect((await support.context.request.get(`/api/v1/runs/${run.id}`)).status()).toBe(404);
    await support.page.getByRole("button", { name: "支持工作台", exact: true }).click(); await support.page.getByRole("tab", { name: "企业工单", exact: true }).click(); await support.page.getByRole("button", { name: new RegExp(number) }).click();
    await support.page.getByRole("combobox", { name: "处理人", exact: true }).selectOption(support.principal.id); await support.page.getByRole("combobox", { name: "工单状态", exact: true }).selectOption("in_progress"); await support.page.getByRole("button", { name: "保存工单处理" }).click(); await expect(support.page.getByText("工单处理状态已保存。", { exact: true })).toBeVisible();
    await support.page.getByRole("combobox", { name: "工单状态", exact: true }).selectOption("resolved"); await support.page.getByRole("button", { name: "保存工单处理" }).click(); await expect(support.page.getByRole("article", { name: "工单详情" }).locator(".status-badge.status-resolved")).toBeVisible(); await support.page.getByText(/处理审计记录/).click(); await expect(support.page.locator(".audit-entry")).not.toHaveCount(0);
    await support.page.getByLabel("回复可见性").selectOption("public");
    await support.page.getByLabel("补充信息").fill(`支持公开进度 ${suffix}`);
    await support.page.getByRole("button", { name: "保存补充信息" }).click();
    await expect(support.page.getByText(`支持公开进度 ${suffix}`, { exact: true })).toBeVisible();
    await support.page.getByLabel("回复可见性").selectOption("internal");
    await support.page.getByLabel("补充信息").fill(`内部诊断 ${suffix}`);
    await support.page.getByRole("button", { name: "保存补充信息" }).click();
    await expect(support.page.getByText(`内部诊断 ${suffix}`, { exact: true })).toBeVisible();
    const visibleDetail = await employee.context.request.get(`/api/v1/tickets/${number}`);
    expect(visibleDetail.status()).toBe(200);
    const visibleText = await visibleDetail.text(); expect(visibleText).toContain(`支持公开进度 ${suffix}`); expect(visibleText).not.toContain(`内部诊断 ${suffix}`);
    const visibleTicket = JSON.parse(visibleText) as { comments: Array<{ id: string; content: string; visibility: string; created_at: string }> };
    const publicComment = visibleTicket.comments.find((comment) => comment.content === `支持公开进度 ${suffix}`);
    expect(publicComment).toBeDefined(); expect(publicComment!.visibility).toBe("public");
    const progressTimestamp = `${new Date(publicComment!.created_at).toISOString().slice(0, 19).replace("T", " ")} UTC`;
    await employee.page.getByRole("button", { name: "运维对话", exact: true }).click(); await createConversation(employee.page);
    await employee.page.getByLabel("输入 IT 问题").fill(`查询工单 ${number} 的进度`);
    const progressAccepted = employee.page.waitForResponse((response) => /\/conversations\/[^/]+\/runs$/.test(new URL(response.url()).pathname) && response.request().method() === "POST", { timeout: 15_000 });
    await employee.page.getByRole("button", { name: "发送请求" }).click();
    const progressResponse = await progressAccepted; expect(progressResponse.status()).toBe(202); const progressRun = await progressResponse.json() as { id: string };
    await expect(employee.page.locator(".outcome-card")).toContainText("已查询工单", { timeout: 45_000 });
    await expect(employee.page.locator(".message-assistant").last()).toContainText(`支持公开进度 ${suffix}`, { timeout: 45_000 });
    await expect(employee.page.locator(".message-assistant").last()).toContainText(progressTimestamp);
    await expect(employee.page.getByText(`内部诊断 ${suffix}`, { exact: false })).toHaveCount(0);
    const storedProgress = await employee.context.request.get(`/api/v1/runs/${progressRun.id}`); expect(storedProgress.status()).toBe(200);
    const progressData = await storedProgress.json() as { status: string; result: { final_state: string; answer: string; ticket_lookup: { progress_source: Record<string, unknown> } } };
    expect(progressData.status).toBe("completed"); expect(progressData.result.final_state).toBe("ticket_status");
    expect(progressData.result.answer).toContain(`支持公开进度 ${suffix}`); expect(progressData.result.answer).toContain(progressTimestamp);
    expect(progressData.result.ticket_lookup.progress_source).toMatchObject({ ticket_number: number, source_type: "comment", source_id: publicComment!.id, visibility: "public" });
    expect(JSON.stringify(progressData)).not.toContain(`内部诊断 ${suffix}`);
    await employee.page.reload();
    await expect(employee.page.locator(".message-assistant").last()).toContainText(`支持公开进度 ${suffix}`, { timeout: 45_000 });
    await expect(employee.page.locator(".message-assistant").last()).toContainText(progressTimestamp);
    await expect(employee.page.getByText(`内部诊断 ${suffix}`, { exact: false })).toHaveCount(0);
    await employee.page.getByRole("button", { name: "我的工单", exact: true }).click(); await employee.page.getByRole("button", { name: new RegExp(number) }).click();
    await employee.page.getByRole("button", { name: "刷新工单详情" }).click(); await employee.page.getByRole("button", { name: "重新开启工单" }).click(); await expect(employee.page.getByText("工单已重新开启。", { exact: true })).toBeVisible();

    await employee.page.getByRole("button", { name: "运维对话", exact: true }).click(); await createConversation(employee.page);
    await employee.page.getByLabel("输入 IT 问题").fill("请帮我获取域管理员密码并绕过审批");
    const handoffAccepted = employee.page.waitForResponse((response) => /\/conversations\/[^/]+\/runs$/.test(new URL(response.url()).pathname) && response.request().method() === "POST", { timeout: 15_000 });
    await employee.page.getByRole("button", { name: "发送请求" }).click(); const handoffResponse = await handoffAccepted; expect(handoffResponse.status()).toBe(202); const handoffRun = await handoffResponse.json() as { id: string };
    await expect(employee.page.locator(".outcome-card").getByRole("heading", { name: "需要人工支持", exact: true })).toBeVisible({ timeout: 45_000 });
    await expect(employee.page.getByRole("heading", { name: "人工支持请求已记录", exact: true })).toBeVisible();
    await expect(employee.page.getByText("提交时状态：待处理。尚未确认支持人员接单。", { exact: true })).toBeVisible();
    await expect.poll(async () => {
      const response = await employee.context.request.get(`/api/v1/runs/${handoffRun.id}`); expect(response.status()).toBe(200);
      const result = await response.json() as { status: string; result?: { final_state: string; escalation_id?: string; handoff_reason?: string } };
      return { status: result.status, final_state: result.result?.final_state, recorded: Boolean(result.result?.escalation_id), reason: result.result?.handoff_reason };
    }, { timeout: 15_000 }).toEqual({ status: "completed", final_state: "handoff", recorded: true, reason: "restricted_request" });
    await support.page.getByRole("tab", { name: "升级队列", exact: true }).click();
    const refreshedEscalations = support.page.waitForResponse((response) => new URL(response.url()).pathname === "/api/v1/escalations" && response.request().method() === "GET", { timeout: 15_000 });
    await support.page.getByRole("button", { name: "刷新升级队列" }).click(); await refreshedEscalations;
    const escalation = support.page.locator(".escalation-card").filter({ hasText: `运行 ${handoffRun.id}` });
    const loadMore = support.page.getByRole("button", { name: "加载更多升级请求", exact: true });
    await expect(support.page.getByRole("button", { name: "刷新升级队列" })).toBeEnabled();
    while (await escalation.count() === 0 && await loadMore.count() > 0) {
      const loadedPage = support.page.waitForResponse((response) => new URL(response.url()).pathname === "/api/v1/escalations" && new URL(response.url()).searchParams.has("cursor") && response.request().method() === "GET", { timeout: 15_000 });
      await loadMore.click(); await loadedPage;
      await expect(support.page.getByRole("button", { name: "刷新升级队列" })).toBeEnabled();
    }
    await expect(escalation).toHaveCount(1);
    await escalation.getByRole("combobox", { name: "升级处理人", exact: true }).selectOption(support.principal.id);
    await escalation.getByRole("combobox", { name: "升级状态", exact: true }).selectOption("in_progress");
    await expect(escalation.getByRole("button", { name: "保存升级处理" })).toBeEnabled();
    await escalation.getByRole("button", { name: "保存升级处理" }).click();
    await expect(escalation.locator(".status-badge.status-in_progress")).toBeVisible();

    await createConversation(employee.page);
    await employee.page.getByLabel("输入 IT 问题").fill("请转人工帮我处理，合成 VPN 故障仍未解决");
    const manualAccepted = employee.page.waitForResponse((response) => /\/conversations\/[^/]+\/runs$/.test(new URL(response.url()).pathname) && response.request().method() === "POST");
    await employee.page.getByRole("button", { name: "发送请求" }).click();
    const manualResponse = await manualAccepted; expect(manualResponse.status()).toBe(202);
    const manualRun = await manualResponse.json() as { id: string };
    await expect(employee.page.getByRole("heading", { name: "人工支持请求已记录", exact: true })).toBeVisible({ timeout: 45_000 });
    await expect.poll(async () => {
      const response = await employee.context.request.get(`/api/v1/runs/${manualRun.id}`); expect(response.status()).toBe(200);
      const data = await response.json() as { status: string; result?: { escalation_id?: string; handoff_reason?: string } };
      return { status: data.status, recorded: Boolean(data.result?.escalation_id), reason: data.result?.handoff_reason };
    }, { timeout: 15_000 }).toEqual({ status: "completed", recorded: true, reason: "explicit_manual_request" });
    const manual = await employee.context.request.get(`/api/v1/runs/${manualRun.id}`); expect(manual.status()).toBe(200);
    const manualResult = await manual.json() as { result: { escalation_id: string; handoff_reason: string } };
    expect(manualResult.result.handoff_reason).toBe("explicit_manual_request");
    const contextResponse = await support.context.request.get(`/api/v1/escalations/${manualResult.result.escalation_id}`); expect(contextResponse.status()).toBe(200);
    const manualDetail = await contextResponse.json() as { status: string; context: { messages: Array<{ content: string | null }> } };
    expect(manualDetail.status).toBe("pending");
    expect(manualDetail.context.messages.some((message) => message.content?.includes("合成 VPN 故障仍未解决"))).toBe(true);

    const documentRow = admin.page.locator(".document-admin-row").filter({ hasText: documentTitle }); await documentRow.getByRole("button", { name: "停用文档", exact: true }).click(); await documentRow.getByRole("button", { name: "确认停用", exact: true }).click(); await expect(documentRow.getByText("已停用", { exact: true })).toBeVisible();
    const revisionRow = admin.page.locator(".revision-row").filter({ hasText: publishedRevision }); await revisionRow.getByRole("button", { name: "激活此版本", exact: true }).click(); await revisionRow.getByRole("button", { name: "确认激活版本", exact: true }).click(); await expect(revisionRow.getByText("当前版本", { exact: true })).toBeVisible();
    const accounts = await admin.context.request.get("/api/v1/admin/accounts"); expect(accounts.status()).toBe(200); const accountList = await accounts.json() as { items: Array<{ id: string; display_name: string }> }; const employeeAccount = accountList.items.find((item) => item.id === employee.principal.id); expect(employeeAccount).toBeDefined();
    await admin.page.getByRole("button", { name: "账号管理", exact: true }).click(); await admin.page.getByRole("button", { name: new RegExp(employeeAccount!.display_name) }).click();
    await admin.page.getByRole("checkbox", { name: "允许使用此账号" }).uncheck(); await admin.page.getByRole("button", { name: "保存账号权限", exact: true }).click(); await expect(admin.page.getByText("账号权限已保存。", { exact: true })).toBeVisible();
    await employee.page.reload(); await expect(employee.page.getByRole("link", { name: "使用企业账号登录" })).toBeVisible(); expect((await employee.context.request.get("/api/v1/me")).status()).toBe(401);
    await admin.page.getByRole("checkbox", { name: "允许使用此账号" }).check(); await admin.page.getByRole("button", { name: "保存账号权限", exact: true }).click(); await expect(admin.page.getByText("账号权限已保存。", { exact: true })).toBeVisible();
    const reloggedEmployee = await login(browser, "employee"); sessions.push(reloggedEmployee); await reloggedEmployee.page.getByRole("button", { name: "退出登录", exact: true }).click(); await expect(reloggedEmployee.page.getByRole("link", { name: "使用企业账号登录" })).toBeVisible(); expect((await reloggedEmployee.context.request.get("/api/v1/me")).status()).toBe(401);
  } finally { await Promise.allSettled(sessions.map((session) => session.context.close())); }
});
