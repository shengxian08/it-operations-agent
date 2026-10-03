import type { KnowledgeSection as Section } from "./types";

export function KnowledgeSection({ section }: { section: Section }) {
  return <section className="article-section">
    <h4>{section.heading}</h4>
    {section.section_path?.length ? <p className="section-path">{section.section_path.join(" › ")}</p> : null}
    {section.blocks?.length ? section.blocks.map((block) => block.contains_code || block.block_type === "code"
      ? <pre className="source-code" key={block.char_start}><code>{block.content}</code></pre>
      : <p key={block.char_start}>{block.content}</p>) : <p>{section.context ?? section.content}</p>}
    {section.tables?.map((table) => <div className="knowledge-table-scroll" key={table.id}>
      <table>
        <caption>第 {table.page_number} 页 · 表 {table.id}</caption>
        <thead><tr>{table.headers.map((header, column) => <th scope="col" key={column}>{header}</th>)}</tr></thead>
        <tbody>{table.rows.map((row, index) => <tr key={index}>{row.map((cell, column) => <td key={column}>{cell || "（原单元格为空）"}</td>)}</tr>)}</tbody>
      </table>
    </div>)}
  </section>;
}
