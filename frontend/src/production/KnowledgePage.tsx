import { useEffect, useState } from "react";
import { api, isAbort } from "./api";
import { dateTime, ErrorNotice, PageHeading, ROLE_LABELS, StatusBadge } from "./shared";
import type { KnowledgeArticle, KnowledgeDocument } from "./types";

export default function KnowledgePage({ openDocument }: { openDocument: { id: string; request: number; indexRevision?: string } | null }) {
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [selected, setSelected] = useState<{ id: string; request: number; indexRevision?: string } | null>(openDocument);
  const [article, setArticle] = useState<KnowledgeArticle | null>(null);
  const [loading, setLoading] = useState(true);
  const [articleLoading, setArticleLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [articleError, setArticleError] = useState<unknown>(null);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => { if (openDocument) setSelected(openDocument); }, [openDocument?.id, openDocument?.request, openDocument?.indexRevision]);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError(null);
    api.documents(undefined, controller.signal).then((result) => { if (controller.signal.aborted) return; setDocuments(result.documents); setCursor(result.next_cursor); }).catch((caught: unknown) => { if (!isAbort(caught)) setError(caught); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [refresh]);
  useEffect(() => {
    if (!selected) return; const controller = new AbortController(); setArticle(null); setArticleLoading(true); setArticleError(null);
    api.article(selected.id, controller.signal, selected.indexRevision).then((result) => { if (!controller.signal.aborted) setArticle(result); }).catch((caught: unknown) => { if (!isAbort(caught)) setArticleError(caught); }).finally(() => { if (!controller.signal.aborted) setArticleLoading(false); });
    return () => controller.abort();
  }, [selected?.id, selected?.request, selected?.indexRevision]);
  async function more() {
    if (!cursor || loading) return; setLoading(true); setError(null);
    try { const result = await api.documents(cursor); setDocuments((current) => [...current, ...result.documents.filter((item) => !current.some((existing) => item.id === existing.id))]); setCursor(result.next_cursor); }
    catch (caught) { setError(caught); } finally { setLoading(false); }
  }
  return <section className="production-page knowledge-section"><PageHeading kicker="KNOWLEDGE LIBRARY" title="知识资料" description="这里展示当前账号可以访问的已发布资料。打开原文可核对回答中的引用。" /><div className="button-row"><button disabled={loading} onClick={() => setRefresh((value) => value + 1)}>刷新知识资料</button></div><ErrorNotice error={error} onRetry={() => setRefresh((value) => value + 1)} /><div className="record-detail-layout"><div className="record-list" aria-label="知识文档列表">{documents.map((document) => <button className={selected?.id === document.id ? "record-row selected" : "record-row"} key={document.id} onClick={() => setSelected((current) => ({ id: document.id, request: (current?.request ?? 0) + 1 }))}><strong>{document.title}</strong><small>版本 {document.version} · {ROLE_LABELS[document.access_level] ?? document.access_level}</small><StatusBadge status={document.status} /></button>)}{loading ? <p role="status">正在加载知识资料…</p> : !documents.length ? <p className="empty-note">暂无可访问的已发布资料。</p> : null}{cursor ? <button disabled={loading} onClick={more}>加载更多文档</button> : null}</div><article className="detail-panel" aria-label="知识原文"><ErrorNotice error={articleError} onRetry={() => setSelected((current) => current ? { ...current, request: current.request + 1 } : null)} />{articleLoading ? <p role="status">正在打开原文…</p> : article ? <><span className="section-kicker">SOURCE / {article.id}</span><h2>{article.title}</h2><p className="detail-meta">版本 {article.version} · {dateTime(article.created_at)}</p>{article.index_revision ? <p className="detail-meta">引用时的知识版本：{article.index_revision}</p> : null}{article.requires_reparse ? <p role="status">此 PDF 正在等待重新解析和核对，暂不作为回答证据。</p> : null}{article.sections?.length ? article.sections.map((section, index) => <section className="article-section" key={`${section.heading}-${index}`}><h3>{section.heading}</h3><p>{section.content}</p></section>) : <p className="article-text">{article.content || "此资料暂无正文。"}</p>}</> : !articleError ? <div className="workspace-empty"><h3>选择文档查看原文</h3><p>从列表选择资料，或在对话结果中点击“查看原文”。</p></div> : null}</article></div></section>;
}
