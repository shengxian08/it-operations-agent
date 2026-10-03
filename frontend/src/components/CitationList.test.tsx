import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { CitationList } from "./CitationList";

it("shows the PDF source page, table and row and opens the cited revision", async () => {
  const open = vi.fn();
  render(<CitationList onOpenSource={open} citations={[{
    document_id: "doc-1", source_title: "响应规范", source_path: "response.pdf",
    chunk_index: 1, excerpt: "紧急响应15分钟", page_number: 2,
    table_id: "p2-t1", row_index: 3, index_revision: "revision-old",
  } as any]} />);
  expect(screen.getByText(/第 2 页.*表 p2-t1.*数据行 3/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /查看原文/ }));
  expect(open).toHaveBeenCalledWith("doc-1", "revision-old");
});
