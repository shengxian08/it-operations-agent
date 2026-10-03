import { useEffect, useState } from "react";

import { loadKnowledgeArticle, loadKnowledgeDocuments } from "../api";
import type { KnowledgeArticle, KnowledgeDocument } from "../types";

interface KnowledgeLibraryProps {
  userId: string;
  openDocument: { id: string; request: number } | null;
}

export function KnowledgeLibrary({ userId, openDocument }: KnowledgeLibraryProps) {
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [article, setArticle] = useState<KnowledgeArticle | null>(null);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const currentUser = userId.trim();
    const controller = new AbortController();
    setDocuments([]);
    setArticle(null);
    setSelectedId(null);
    setError(null);
    if (!currentUser) {
      setLoading(false);
      return () => controller.abort();
    }
    setLoading(true);
    loadKnowledgeDocuments(currentUser, controller.signal)
      .then((items) => {
        setDocuments(items);
        setSelectedId(items[0]?.id ?? null);
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) {
          setError(caught instanceof Error ? caught.message : "资料载入失败。");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [userId]);

  useEffect(() => {
    if (openDocument) setSelectedId(openDocument.id);
  }, [openDocument]);

  useEffect(() => {
    const currentUser = userId.trim();
    const controller = new AbortController();
    setArticle(null);
    if (!selectedId || !currentUser) return () => controller.abort();
    loadKnowledgeArticle(selectedId, currentUser, controller.signal)
      .then(setArticle)
      .catch((caught: unknown) => {
        if (!controller.signal.aborted) {
          setError(caught instanceof Error ? caught.message : "原文载入失败。");
        }
      });
    return () => controller.abort();
  }, [selectedId, userId]);

  const filtered = documents.filter((document) =>
    `${document.title} ${document.source_path}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()),
  );
  const chunkCount = documents.reduce((total, document) => total + document.chunk_count, 0);

  return (
    <section className="knowledge-section" id="knowledge" aria-labelledby="knowledge-title">
      <div className="section-heading">
        <div>
          <span className="section-kicker">SOURCE LIBRARY</span>
          <h2 id="knowledge-title">已索引的知识资料</h2>
        </div>
        <p>这里显示已索引、且演示身份可查看的资料。增删文件后请重新运行知识导入命令并刷新页面；打开文档可核对原文。</p>
      </div>
      <div className="library-summary">
        <strong>{loading ? "…" : documents.length}<small>篇可见资料</small></strong>
        <strong>{loading ? "…" : chunkCount}<small>个检索片段</small></strong>
        <span>来源：<code>data/knowledge</code> · 更换身份后列表随权限变化</span>
      </div>
      <div className="library-layout">
        <div className="library-list">
          <label htmlFor="knowledge-search">查找资料</label>
          <input
            id="knowledge-search"
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="输入 VPN、账号或文件名"
          />
          {loading && <p className="content-state">正在读取已索引资料…</p>}
          {error && <p className="content-error" role="alert">{error}</p>}
          {!loading && !error && filtered.length === 0 && <p className="content-state">没有匹配的资料。</p>}
          <ul>
            {filtered.map((document) => (
              <li key={document.id}>
                <button
                  type="button"
                  className={selectedId === document.id ? "source-row is-selected" : "source-row"}
                  aria-label={`查看 ${document.title}`}
                  aria-pressed={selectedId === document.id}
                  onClick={() => {
                    setError(null);
                    setSelectedId(document.id);
                  }}
                >
                  <strong>{document.title}</strong>
                  <small>{document.source_path} · {document.chunk_count} 个片段</small>
                </button>
              </li>
            ))}
          </ul>
        </div>
        <article className="article-view" aria-live="polite">
          {article ? (
            <>
              <div className="article-meta"><span>知识原文</span><span>版本 {article.version}</span></div>
              <h3>{article.title}</h3>
              <p className="article-source">源文件：<code>{article.source_path}</code></p>
              {article.sections.map((section) => (
                <section key={section.heading}>
                  <h4>{section.heading}</h4>
                  <p>{section.body}</p>
                </section>
              ))}
            </>
          ) : (
            <p className="content-state">选择左侧资料查看原文。</p>
          )}
        </article>
      </div>
    </section>
  );
}
