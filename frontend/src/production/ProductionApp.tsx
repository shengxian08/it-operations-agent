import { lazy, Suspense, useEffect, useState } from "react";
import { api, ApiError, isAbort, setSession } from "./api";
import { ConversationWorkspace } from "./ConversationWorkspace";
import { clearPrivateStorage } from "./storage";
import { ErrorNotice, ROLE_LABELS } from "./shared";
import type { Principal } from "./types";
import "./production.css";

const KnowledgePage = lazy(() => import("./KnowledgePage"));
const TicketsPage = lazy(() => import("./TicketsPage"));
const SupportPage = lazy(() => import("./SupportPage"));
const AdminPage = lazy(() => import("./AdminPage"));
const AccountsPage = lazy(() => import("./AccountsPage"));
type Page = "chat" | "knowledge" | "tickets" | "support" | "admin" | "accounts";

export function ProductionApp() {
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [page, setPage] = useState<Page>("chat");
  const [check, setCheck] = useState(0);
  const [loggingOut, setLoggingOut] = useState(false);
  const [source, setSource] = useState<{ id: string; request: number; indexRevision?: string } | null>(null);
  useEffect(() => {
    const expired = () => { setSession(null); setPrincipal(null); setSource(null); setPage("chat"); setError(null); clearPrivateStorage(); };
    window.addEventListener("session-expired", expired);
    return () => window.removeEventListener("session-expired", expired);
  }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError(null);
    api.me(controller.signal).then((me) => { if (controller.signal.aborted) return; setSession(me); setPrincipal(me); }).catch((caught: unknown) => { if (!isAbort(caught) && !(caught instanceof ApiError && caught.status === 401)) setError(caught); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [check]);
  async function logout() {
    setLoggingOut(true); setError(null);
    try { await api.logout(); setSession(null); setPrincipal(null); setSource(null); setPage("chat"); clearPrivateStorage(); }
    catch (caught) { setError(caught); }
    finally { setLoggingOut(false); }
  }
  function openSource(documentId: string, indexRevision?: string) { setSource((current) => ({ id: documentId, indexRevision, request: (current?.request ?? 0) + 1 })); setPage("knowledge"); }
  async function refreshIdentity() { const me = await api.me(); setSession(me); setPrincipal(me); if (me.role !== "admin") setPage("chat"); }
  if (loading) return <main className="login-page"><p role="status">正在验证企业登录…</p></main>;
  if (!principal) return <main className="login-page"><section className="login-card"><span className="section-kicker">IT OPERATIONS / 企业服务台</span><h1>你的 IT 支持<br />从这里开始。</h1><p>使用企业账号访问知识资料、处理故障与跟进工单。所有操作关联当前登录身份。</p><ErrorNotice error={error} onRetry={() => setCheck((value) => value + 1)} /><a className="primary-button" href="/api/v1/auth/login?return_to=%2F">使用企业账号登录</a></section></main>;
  const isSupport = principal.role === "support" || principal.role === "admin";
  return <main className="workspace production-workspace">
    <header className="topbar"><a className="brand" href="#" onClick={(event) => { event.preventDefault(); setPage("chat"); }}><span>IT</span><div><strong>运维知识助手</strong><small>Operations Intelligence</small></div></a><nav className="production-nav" aria-label="工作台导航"><button aria-current={page === "chat" ? "page" : undefined} onClick={() => setPage("chat")}>运维对话</button><button aria-current={page === "knowledge" ? "page" : undefined} onClick={() => setPage("knowledge")}>知识资料</button><button aria-current={page === "tickets" ? "page" : undefined} onClick={() => setPage("tickets")}>{isSupport ? "工单记录" : "我的工单"}</button>{isSupport ? <button aria-current={page === "support" ? "page" : undefined} onClick={() => setPage("support")}>支持工作台</button> : null}{principal.role === "admin" ? <button aria-current={page === "admin" ? "page" : undefined} onClick={() => setPage("admin")}>知识管理</button> : null}{principal.role === "admin" ? <button aria-current={page === "accounts" ? "page" : undefined} onClick={() => setPage("accounts")}>账号管理</button> : null}</nav><div className="account-menu"><strong>{principal.display_name}</strong><small>{ROLE_LABELS[principal.role]}</small><button disabled={loggingOut} onClick={logout}>{loggingOut ? "正在退出" : "退出登录"}</button></div></header>
    <ErrorNotice error={error} />
    <Suspense fallback={<p className="page-loading" role="status">正在加载工作区…</p>}>
      {page === "chat" ? <ConversationWorkspace principal={principal} onOpenSource={openSource} /> : page === "knowledge" ? <KnowledgePage openDocument={source} /> : page === "tickets" ? <TicketsPage role={principal.role} /> : page === "support" && isSupport ? <SupportPage onOpenSource={openSource} /> : page === "admin" && principal.role === "admin" ? <AdminPage userId={principal.id} /> : page === "accounts" && principal.role === "admin" ? <AccountsPage principalId={principal.id} onIdentityChanged={refreshIdentity} /> : null}
    </Suspense>
    <footer><span>企业 IT 服务台</span><span>知识可溯源 · 操作需确认</span></footer>
  </main>;
}
