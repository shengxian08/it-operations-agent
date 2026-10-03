import { errorMessage } from "./api";

export function ErrorNotice({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null;
  return <div className="page-error" role="alert"><span>{typeof error === "string" ? error : errorMessage(error)}</span>{onRetry ? <button type="button" onClick={onRetry}>重试</button> : null}</div>;
}
export function dateTime(value: string | undefined) { if (!value) return "—"; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false }); }
export const STATUS_LABELS: Record<string, string> = { pending: "待处理", queued: "排队中", running: "处理中", in_progress: "处理中", resolved: "已解决", closed: "已关闭", completed: "已完成", failed: "失败", cancelled: "已取消", active: "有效", archived: "已归档", ready: "待发布", published: "已发布", inactive: "已停用" };
export const ROLE_LABELS: Record<string, string> = { employee: "员工", support: "支持专员", admin: "管理员" };
export function StatusBadge({ status }: { status: string }) { return <span className={`status-badge status-${status}`}>{STATUS_LABELS[status] ?? status}</span>; }
export function PageHeading({ kicker, title, description }: { kicker: string; title: string; description: string }) { return <div className="section-heading"><div><span className="section-kicker">{kicker}</span><h2>{title}</h2></div><p>{description}</p></div>; }
