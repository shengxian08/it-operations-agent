import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import AdminPage from "./AdminPage";
import { storeValue } from "./storage";

afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear(); });
describe("asynchronous knowledge publication", () => {
  it("labels unknown old Markdown and prevents publishing an old ready preview", async () => {
    const job = { id: "old-md", title: "旧命令手册", access_level: "employee", status: "ready", document_id: null,
      created_at: "2026-01-01", content: "历史原文", requires_reparse: true };
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("/jobs")) return new Response(JSON.stringify({ jobs: [job], next_cursor: null }));
      if (url.endsWith("/jobs/old-md")) return new Response(JSON.stringify(job));
      if (url.endsWith("/revisions")) return new Response(JSON.stringify({ revisions: [] }));
      if (url.endsWith("/documents")) return new Response(JSON.stringify({ documents: [{ id: "old-doc", title: "旧版Markdown资料", status: "active", version: "old", access_level: "employee", created_at: "2026-01-01", requires_reparse: true, reparse_reason: "markdown_structure" }], next_cursor: null }));
      throw new Error(`unexpected ${url}`);
    }));
    const user = userEvent.setup(); render(<AdminPage />);
    expect(await screen.findByText("旧版 Markdown 待重新上传并核对，暂不进入新版本检索。")).toBeVisible();
    await user.click(await screen.findByRole("button", { name: /旧命令手册/ }));
    expect(await screen.findByText(/此预览需要重新解析/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "发布知识资料" })).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /已核对正文/ })).not.toBeInTheDocument();
  });
  it("explains a durable token budget failure without claiming publication", async () => {
    storeValue("admin:knowledge-index-task", { id: "over-budget", message: "知识资料已发布。" });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("/index-jobs/over-budget")) return new Response(JSON.stringify({ id: "over-budget", status: "failed", result: null, error: "embedding_input_too_long" }));
      if (url.endsWith("/jobs")) return new Response(JSON.stringify({ jobs: [], next_cursor: null }));
      if (url.endsWith("/revisions")) return new Response(JSON.stringify({ revisions: [] }));
      if (url.endsWith("/documents")) return new Response(JSON.stringify({ documents: [], next_cursor: null }));
      throw new Error(`unexpected ${url}`);
    }));
    render(<AdminPage />);
    expect(await screen.findByText(/标题、章节与正文的完整输入超过模型预算/)).toBeVisible();
    expect(screen.getByText(/请人工整理.*重新上传并核对/)).toBeVisible();
    expect(screen.queryByText(/知识资料已发布/)).not.toBeInTheDocument();
  });
  it("shows original table cells and the PDF page before the administrator can publish", async () => {
    const job = { id: "pdf-1", title: "响应规范", access_level: "employee", status: "ready", document_id: null,
      created_at: "2026-01-01", sections: [{ heading: "PDF 第 1 页", content: "工作日适用；响应不等于解决。",
      tables: [{ id: "p1-t1", page_number: 1, headers: ["服务等级", "响应时间（分钟）", "适用范围"],
        rows: [["紧急", "15", "业务中断"]] }] }] };
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.endsWith("/jobs")) return new Response(JSON.stringify({ jobs: [job], next_cursor: null }));
      if (url.endsWith("/jobs/pdf-1")) return new Response(JSON.stringify(job));
      if (url.endsWith("/revisions")) return new Response(JSON.stringify({ revisions: [] }));
      if (url.endsWith("/documents")) return new Response(JSON.stringify({ documents: [], next_cursor: null }));
      throw new Error(`unexpected ${url}`);
    }));
    const user = userEvent.setup(); render(<AdminPage />);
    await user.click(await screen.findByRole("button", { name: /响应规范/ }));
    const table = await screen.findByRole("table", { name: /第 1 页.*p1-t1/ });
    expect(table).toHaveTextContent("服务等级响应时间（分钟）适用范围紧急15业务中断");
    expect(screen.getByRole("button", { name: "发布知识资料" })).toBeDisabled();
  });
  it("keeps publication in progress until the durable index job completes", async () => {
    let release: ((response: Response) => void) | undefined;
    const job = { id: "upload-1", title: "VPN手册", access_level: "employee", status: "ready", document_id: null, created_at: "2026-01-01", sections: [{ heading: "步骤", content: "重启客户端" }] };
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/publish")) return new Response(JSON.stringify({ index_job_id: "index-1", status: "queued" }), { status: 202 });
      if (url.endsWith("/index-jobs/index-1")) return new Promise<Response>((resolve) => { release = resolve; });
      if (url.endsWith("/jobs")) return new Response(JSON.stringify({ jobs: [job], next_cursor: null }));
      if (url.endsWith("/jobs/upload-1")) return new Response(JSON.stringify(job));
      if (url.endsWith("/revisions")) return new Response(JSON.stringify({ revisions: [] }));
      if (url.endsWith("/documents")) return new Response(JSON.stringify({ documents: [], next_cursor: null }));
      throw new Error(`unexpected ${url} ${init?.method}`);
    }));
    const user = userEvent.setup(); render(<AdminPage />);
    await user.click(await screen.findByRole("button", { name: /VPN手册/ }));
    await user.click(await screen.findByRole("checkbox", { name: /已核对正文/ }));
    await user.click(screen.getByRole("button", { name: "发布知识资料" }));
    expect(await screen.findByText(/正在准备知识版本/)).toBeInTheDocument();
    expect(screen.queryByText(/知识资料已发布/)).not.toBeInTheDocument();
    await act(async () => { release!(new Response(JSON.stringify({ id: "index-1", status: "completed", result: { revision_id: "revision-new", status: "active" }, error: null }))); });
    expect(await screen.findByText(/知识资料已发布.*revision-new/)).toBeInTheDocument();
  });
});
