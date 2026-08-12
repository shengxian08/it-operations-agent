import type { Citation } from "../types";

interface CitationListProps {
  citations: Citation[];
}

export function CitationList({ citations }: CitationListProps) {
  if (!citations.length) return null;

  return (
    <section className="result-card citations" aria-labelledby="citations-title">
      <div className="card-title-row">
        <div>
          <span className="section-kicker">EVIDENCE</span>
          <h2 id="citations-title">知识引用</h2>
        </div>
        <strong>{citations.length.toString().padStart(2, "0")}</strong>
      </div>
      <ol>
        {citations.map((citation, index) => (
            <li key={`${citation.document_id}-${citation.chunk_index}`}>
              <span className="citation-index">{String(index + 1).padStart(2, "0")}</span>
              <div>
                <h3>{citation.source_title}</h3>
                <small>{citation.source_path} · 片段 {citation.chunk_index + 1}</small>
                <p>{citation.excerpt}</p>
              </div>
            </li>
        ))}
      </ol>
    </section>
  );
}
