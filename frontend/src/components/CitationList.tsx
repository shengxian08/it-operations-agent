import type { Citation } from "../types";

interface CitationListProps {
  citations: Citation[];
  onOpenSource: (documentId: string, indexRevision?: string) => void;
}

export function CitationList({ citations, onOpenSource }: CitationListProps) {
  if (!citations.length) return null;

  return (
    <section className="result-card citations" aria-labelledby="citations-title">
      <div className="card-title-row">
        <div>
          <span className="section-kicker">EVIDENCE</span>
          <h2 id="citations-title">本次检索证据</h2>
        </div>
        <strong>{citations.length.toString().padStart(2, "0")}</strong>
      </div>
      <p className="citation-help">这些片段被检索并提供给回答生成；同一文章可能命中多段。可打开原文核对，完整资料见下方知识库。</p>
      <ol>
        {citations.map((citation, index) => (
            <li key={`${citation.document_id}-${citation.chunk_index}`}>
              <span className="citation-index">{String(index + 1).padStart(2, "0")}</span>
              <div>
                <h3>{citation.source_title}</h3>
                <small>{citation.source_path} · 片段 {citation.chunk_index + 1}{citation.page_number ? ` · 第 ${citation.page_number} 页` : ""}{citation.table_id ? ` · 表 ${citation.table_id}` : ""}{citation.row_index ? ` · 数据行 ${citation.row_index}` : ""}</small>
                <p>{citation.excerpt}</p>
                <button type="button" onClick={() => citation.index_revision ? onOpenSource(citation.document_id, citation.index_revision) : onOpenSource(citation.document_id)}>查看原文 ↗</button>
              </div>
            </li>
        ))}
      </ol>
    </section>
  );
}
