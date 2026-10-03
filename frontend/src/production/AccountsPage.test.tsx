import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import AccountsPage from "./AccountsPage";

afterEach(() => vi.unstubAllGlobals());
describe("account permission management", () => {
  it("sends the server version when changing role and enabled state", async () => {
    const account = { id: "employee-1", display_name: "张宁", role: "employee", enabled: true, version: 3 }; const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === "PATCH") return new Response(JSON.stringify({ ...account, role: "support", enabled: false, version: 4 }));
      return new Response(JSON.stringify({ items: [account], next_cursor: null }));
    }); vi.stubGlobal("fetch", fetcher); const user = userEvent.setup(); render(<AccountsPage principalId="admin-1" onIdentityChanged={vi.fn()} />);
    await user.click(await screen.findByRole("button", { name: /张宁/ })); await user.selectOptions(screen.getByRole("combobox", { name: "账号角色" }), "support"); await user.click(screen.getByRole("checkbox", { name: "允许使用此账号" })); await user.click(screen.getByRole("button", { name: "保存账号权限" }));
    expect(await screen.findByText("账号权限已保存。", { exact: true })).toBeInTheDocument();
    const patch = fetcher.mock.calls.find(([, init]) => init?.method === "PATCH"); expect(patch?.[0]).toBe("/api/v1/admin/accounts/employee-1"); expect(JSON.parse(String(patch?.[1]?.body))).toEqual({ role: "support", enabled: false, expected_version: 3 });
  });

  it("requires refreshing the server version after an optimistic conflict", async () => {
    const account = { id: "employee-1", display_name: "张宁", role: "employee", enabled: true, version: 3 }; let listCount = 0; let patches = 0;
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => { if (init?.method === "PATCH") { patches += 1; return new Response(JSON.stringify({ detail: "account_version_changed" }), { status: 409 }); } listCount += 1; return new Response(JSON.stringify({ items: [{ ...account, version: listCount > 1 ? 4 : 3 }], next_cursor: null })); }));
    const user = userEvent.setup(); render(<AccountsPage principalId="admin-1" onIdentityChanged={vi.fn()} />); await user.click(await screen.findByRole("button", { name: /张宁/ })); await user.selectOptions(screen.getByRole("combobox", { name: "账号角色" }), "support"); await user.click(screen.getByRole("button", { name: "保存账号权限" }));
    expect(await screen.findByText(/记录已被更新/)).toBeInTheDocument(); expect(screen.getByRole("button", { name: "保存账号权限" })).toBeDisabled(); await user.click(screen.getByRole("button", { name: "刷新账号版本" })); expect(await screen.findByText(/版本 4/)).toBeInTheDocument(); expect(patches).toBe(1);
  });
});
