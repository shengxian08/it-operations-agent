import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DemoGuide } from "./DemoGuide";
import { KnowledgeLibrary } from "./KnowledgeLibrary";
import { TicketExplorer } from "./TicketExplorer";

afterEach(() => vi.unstubAllGlobals());

describe("project content", () => {
  it("lets a new user fill a verified example without sending it", async () => {
    const onSelectPrompt = vi.fn();
    const user = userEvent.setup();
    render(<DemoGuide onSelectPrompt={onSelectPrompt} />);

    await user.click(screen.getByRole("button", { name: "使用知识问答示例" }));
    expect(onSelectPrompt).toHaveBeenCalledWith("公司 VPN 连不上时应该先检查什么？");
    expect(screen.getByText(/不需要 JSON/)).toBeInTheDocument();
  });

  it("opens the original indexed article and fills a scoped ticket query", async () => {
    const onSelectPrompt = vi.fn();
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => ({
        ok: true,
        json: async () => {
          if (url.includes("/api/knowledge/doc-1")) {
            return {
              id: "doc-1",
              title: "VPN 连接故障处理",
              source_path: "vpn-connection.md",
              version: "1.0",
              sections: [{ heading: "操作步骤", body: "确认网络后重新打开 VPN 客户端。" }],
            };
          }
          if (url.includes("/api/knowledge")) {
            return {
              documents: [
                {
                  id: "doc-1",
                  title: "VPN 连接故障处理",
                  source_path: "vpn-connection.md",
                  version: "1.0",
                  chunk_count: 4,
                },
              ],
            };
          }
          return {
            tickets: [
              {
                ticket_number: "IT-2026-0001",
                title: "VPN 客户端无法连接",
                category: "network",
                priority: "medium",
                status: "pending",
              },
            ],
          };
        },
      })),
    );

    render(
      <>
        <KnowledgeLibrary userId="u-001" openDocument={null} />
        <TicketExplorer userId="u-001" onSelectPrompt={onSelectPrompt} />
      </>,
    );

    await user.click(await screen.findByRole("button", { name: "查看 VPN 连接故障处理" }));
    expect(await screen.findByText("确认网络后重新打开 VPN 客户端。")).toBeInTheDocument();
    expect(screen.getAllByText(/vpn-connection.md/).length).toBeGreaterThan(0);

    await user.click(await screen.findByRole("button", { name: "查询 IT-2026-0001" }));
    expect(onSelectPrompt).toHaveBeenCalledWith("查询工单 IT-2026-0001 的进度");
    expect(screen.getByText(/优先级表示影响程度/)).toBeInTheDocument();
  });
});
