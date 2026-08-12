import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TicketDraftCard } from "./TicketDraftCard";
import type { TicketDraft } from "../types";

const draft: TicketDraft = {
  title: "VPN 无法连接",
  category: "network",
  priority: "high",
  description: "客户端持续超时",
  attempted_steps: ["重启客户端"],
};

describe("TicketDraftCard", () => {
  it("requires explicit confirmation and invalidates it after an edit", async () => {
    const user = userEvent.setup();
    const onEdit = vi.fn();
    const onConfirm = vi.fn();
    render(
      <TicketDraftCard
        draft={draft}
        tokenAvailable
        submitting={false}
        result={null}
        onEdit={onEdit}
        onConfirm={onConfirm}
      />,
    );

    const confirmButton = screen.getByRole("button", { name: "确认并创建工单" });
    expect(confirmButton).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: /我已核对/ }));
    expect(confirmButton).toBeEnabled();
    await user.click(confirmButton);
    expect(onConfirm).toHaveBeenCalledTimes(1);
    await user.type(screen.getByLabelText("标题"), " - 补充");

    expect(onEdit).toHaveBeenCalled();
    expect(confirmButton).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("原确认令牌已失效");
    await user.click(confirmButton);
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });
});
