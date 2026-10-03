import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { KnowledgeSection } from "./KnowledgeSection";

describe("source based Markdown preview", () => {
  it("keeps code, blank lines and indentation literal inside pre/code", () => {
    const raw = '```powershell\n# 这是命令注释\nGet-Service\n\n    <script>alert("literal")</script>\n' + '    参数保持原样\n'.repeat(35) + '```';
    const section = { heading: "E-42", content: raw, section_path: ["指南", "会议室", "E-42"],
      blocks: [{ block_type: "code", content: raw, char_start: 20, char_end: 20 + raw.length, contains_code: true }] };
    const { container } = render(<KnowledgeSection section={section} />);
    expect(container.querySelector("pre code")?.textContent).toBe(raw);
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText("指南 › 会议室 › E-42")).toBeInTheDocument();
    expect(screen.getAllByRole("heading")).toHaveLength(1);
  });

  it("renders paragraph and code as separate complete source blocks", () => {
    const raw = "说明中的  两个空格。";
    const code = "    timeout = 15\n\n    retry = 2";
    const { container } = render(<KnowledgeSection section={{ heading: "配置", content: raw + "\n\n" + code,
      blocks: [{ block_type: "paragraph", content: raw, char_start: 0, char_end: raw.length },
        { block_type: "code", content: code, char_start: 30, char_end: 30 + code.length, contains_code: true }] }} />);
    expect(container.querySelector("p")?.textContent).toBe(raw);
    expect(container.querySelector("pre code")?.textContent).toBe(code);
  });

  it("retains the full historical source without assigning structure", () => {
    const raw = "原版\n\n    indented\n结尾";
    const { container } = render(<KnowledgeSection section={{ heading: "旧资料", content: raw }} />);
    expect(container.querySelector("p")?.textContent).toBe(raw);
  });

  it("keeps PDF table cells and page identity", () => {
    render(<KnowledgeSection section={{ heading: "PDF 第 2 页", content: "原行", context: "工作日适用。",
      tables: [{ id: "p2-t1", page_number: 2, headers: ["等级", "分钟"], rows: [["紧急", "15"]] }] }} />);
    expect(screen.getByRole("table", { name: "第 2 页 · 表 p2-t1" })).toHaveTextContent("等级分钟紧急15");
    expect(screen.getByText("工作日适用。")).toBeInTheDocument();
  });
});
