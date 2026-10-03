import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DraftEditor } from "./DraftEditor";
import type { DraftRecord } from "./types";

const draft: DraftRecord = { id: "draft-1", conversation_id: "c", version: 1, draft: { title: "VPN故障", category: "network", priority: "high", description: "连接超时\n影响范围：仅本人", attempted_steps: [], problem: "连接超时", impact: "仅本人", intake_version: 1 }, confirmation_token: "token-v1", expires_at: "2099-01-01T00:00:00Z", status: "pending" };
afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear(); });

describe("versioned ticket confirmation", () => {
  it("requires explicit facts before a legacy draft can be resigned", async () => {
    const legacy = { ...draft, draft: { ...draft.draft, problem: null, impact: null, intake_version: null }, requires_details: true, confirmation_token: null };
    const fetcher = vi.fn(async (_url: string, _init: RequestInit) => new Response(JSON.stringify({ ...legacy, version: 2 })));
    vi.stubGlobal("fetch", fetcher); const user = userEvent.setup();
    render(<DraftEditor record={legacy} userId="u" onSaved={vi.fn()} onCreated={vi.fn()} onRefresh={vi.fn()} />);
    expect(screen.getByText("此旧草稿缺少必填信息，请补全后保存并重新签名。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认并创建工单" })).toBeDisabled();
    await user.type(screen.getByLabelText("故障现象或错误信息"), "VPN错误E42");
    await user.type(screen.getByLabelText("影响范围及工作阻断情况"), "仅本人无法办公");
    expect(screen.getByRole("button", { name: "保存修改并重新签名" })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: "已核对尝试记录，尚未尝试可留空" }));
    await user.click(screen.getByRole("button", { name: "保存修改并重新签名" }));
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(JSON.parse(fetcher.mock.calls[0][1]!.body as string).draft).toMatchObject({
      problem: "VPN错误E42", impact: "仅本人无法办公", description: "VPN错误E42\n影响范围：仅本人无法办公", attempted_steps: [], intake_version: 1,
    });
  });

  it("saves edits and requires checking the newly signed version", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ ...draft, version: 2, confirmation_token: "token-v2", draft: { ...draft.draft, title: "修改标题" } })));
    vi.stubGlobal("fetch", fetcher); const onSaved = vi.fn(); const user = userEvent.setup();
    const view = render(<DraftEditor record={draft} userId="u" onSaved={onSaved} onCreated={vi.fn()} onRefresh={vi.fn()} />);
    await user.type(screen.getByLabelText("草稿标题"), "修改");
    expect(screen.getByRole("button", { name: "确认并创建工单" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "保存修改并重新签名" }));
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ version: 2, confirmation_token: "token-v2" }));
    view.rerender(<DraftEditor record={{ ...draft, version: 2, confirmation_token: "token-v2" }} userId="u" onSaved={onSaved} onCreated={vi.fn()} onRefresh={vi.fn()} />);
    expect(screen.getByRole("checkbox", { name: /我已核对以上内容/ })).not.toBeChecked();
  });

  it("reuses the same confirmation key after an unknown result", async () => {
    const keys: string[] = []; let attempt = 0;
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
      keys.push(new Headers(init.headers).get("Idempotency-Key")!); attempt += 1;
      if (attempt === 1) throw new TypeError("network lost");
      return new Response(JSON.stringify({ ticket_number: "IT-001", status: "pending" }));
    }));
    const user = userEvent.setup(); const created = vi.fn();
    render(<DraftEditor record={draft} userId="u" onSaved={vi.fn()} onCreated={created} onRefresh={vi.fn()} />);
    await user.click(screen.getByRole("checkbox", { name: /我已核对以上内容/ })); await user.click(screen.getByRole("button", { name: "确认并创建工单" }));
    await user.click(await screen.findByRole("button", { name: "查询确认结果并重试" }));
    expect(keys).toHaveLength(2); expect(keys[1]).toBe(keys[0]);
    expect(created).toHaveBeenCalledWith({ ticket_number: "IT-001", status: "pending" });
  });
});
