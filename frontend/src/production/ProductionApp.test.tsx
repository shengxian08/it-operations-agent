import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProductionApp } from "./ProductionApp";

afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear(); });

describe("production identity", () => {
  it("starts with an OIDC login for an unauthenticated browser", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "请登录" }), { status: 401 })));
    render(<ProductionApp />);
    const login = await screen.findByRole("link", { name: "使用企业账号登录" });
    expect(login).toHaveAttribute("href", "/api/v1/auth/login?return_to=%2F");
    expect(screen.queryByLabelText("用户标识")).not.toBeInTheDocument();
  });

  it("shows server identity and resets private data after session expiry", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("/me")) return new Response(JSON.stringify({ id: "u-42", display_name: "张宁", role: "employee", csrf_token: "csrf" }));
      return new Response(JSON.stringify({ conversations: [], next_cursor: null }));
    }));
    render(<ProductionApp />);
    expect(await screen.findByText("张宁")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "新建会话" })).toBeEnabled());
    window.dispatchEvent(new Event("session-expired"));
    expect(await screen.findByRole("link", { name: "使用企业账号登录" })).toBeInTheDocument();
    expect(screen.queryByText("张宁")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "新建会话" })).not.toBeInTheDocument();
  });

  it("logs out through the authenticated cookie and csrf endpoint", async () => {
    const fetcher = vi.fn(async (url: string) => {
      if (url.endsWith("/me")) return new Response(JSON.stringify({ id: "u-42", display_name: "张宁", role: "employee", csrf_token: "csrf" }));
      if (url.endsWith("/logout")) return new Response(null, { status: 204 });
      return new Response(JSON.stringify({ conversations: [], next_cursor: null }));
    });
    vi.stubGlobal("fetch", fetcher);
    const user = userEvent.setup(); render(<ProductionApp />);
    await user.click(await screen.findByRole("button", { name: "退出登录" }));
    expect(await screen.findByRole("link", { name: "使用企业账号登录" })).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledWith("/api/v1/auth/logout", expect.objectContaining({ method: "POST", credentials: "same-origin" }));
  });
});
