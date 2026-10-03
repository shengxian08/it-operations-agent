"""Visible ticket evidence and permission checks shared by tools, detail and replay."""
import hashlib
import json
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select, tuple_

from app.db.models import User
from app.tickets.progress import clean_summary
from app.production.business_models import BusinessAudit, ProductionTicket, ProductionTicketComment
from app.production.identity_models import IdentityAccount

STAFF = {"support", "admin"}
STATUS = {"pending": "待处理", "in_progress": "处理中", "resolved": "已解决", "closed": "已关闭"}
REDACTED = "这条历史工单回答的来源当前无法核验或已不可访问，请重新查询工单。"
HISTORY_PLACEHOLDER = "此轮工单查询已完成；再次查单须重新调用工单工具。"


async def current_role(session, user_id: str) -> str:
    row = (await session.execute(select(User.access_level, IdentityAccount.enabled).join(
        IdentityAccount, IdentityAccount.user_id == User.id).where(User.id == user_id))).first()
    if row is None or not row.enabled or row.access_level not in {"employee", "support", "admin"}:
        raise HTTPException(403, "account_access_changed")
    return row.access_level




def safe_audit(row) -> tuple[str, dict] | None:
    if row.event_type == "created":
        return "工单已创建，等待 IT 支持处理。", {}
    if row.event_type != "updated" or not isinstance(row.details, dict):
        return None
    messages, details = [], {}
    status = row.details.get("status")
    if (isinstance(status, dict) and isinstance(status.get("from"), str)
        and isinstance(status.get("to"), str) and status["from"] in STATUS and status["to"] in STATUS
        and status["from"] != status["to"]):
        messages.append(f"状态由{STATUS[status['from']]}变为{STATUS[status['to']]}。")
        details["status"] = {"from": status["from"], "to": status["to"]}
    assignee = row.details.get("assignee_id")
    if (isinstance(assignee, dict) and set(assignee) >= {"from", "to"}
        and all(value is None or isinstance(value, str) and 0 < len(value) <= 64 for value in (assignee["from"], assignee["to"]))
        and assignee["from"] != assignee["to"]):
        messages.append("已分派处理人。" if assignee["to"] else "已取消分派处理人。")
        # Do not expose arbitrary JSON values as names or employee identifiers.
        details["assignment"] = "assigned" if assignee["to"] else "unassigned"
    return ("".join(messages), details) if messages else None


def comment_kind(comment) -> str:
    if comment.visibility == "internal":
        return "internal_note"
    if comment.visibility == "unclassified":
        return "unclassified_note"
    return "support_reply" if comment.author_role in STAFF else "employee_update" if comment.author_role == "employee" else "public_comment"


def fingerprint(content: str, stamp: datetime, kind: str) -> str:
    return hashlib.sha256(json.dumps([content, stamp.isoformat(), kind], ensure_ascii=False).encode()).hexdigest()


async def latest_record(session, ticket_id: str, role: str):
    comment_query = select(ProductionTicketComment).where(ProductionTicketComment.ticket_id == ticket_id)
    if role not in STAFF:
        comment_query = comment_query.where(ProductionTicketComment.visibility == "public")
    audit_query = select(BusinessAudit).where(BusinessAudit.entity_type == "ticket", BusinessAudit.entity_id == ticket_id,
                                             BusinessAudit.event_type.in_(["created", "updated"]))
    candidates = []
    # Keyset batches retain the true latest valid record even after old empty/unknown records.
    for model, query, kind in ((ProductionTicketComment, comment_query, "comment"), (BusinessAudit, audit_query, "audit")):
        while True:
            rows = list(await session.scalars(query.order_by(model.created_at.desc(), model.id.desc()).limit(50)))
            valid = None
            for row in rows:
                if kind == "comment":
                    summary, update_kind, visibility = clean_summary(row.content), comment_kind(row), row.visibility
                    raw = row.content
                else:
                    audited = safe_audit(row)
                    summary, update_kind, visibility = audited[0] if audited else "", "audit", "public"
                    raw = summary
                if summary:
                    valid = {"summary": summary, "updated_at": row.created_at, "update_kind": update_kind,
                             "source_type": kind, "source_id": row.id, "visibility": visibility,
                             "fingerprint": fingerprint(raw, row.created_at, update_kind)}
                    break
            if valid:
                candidates.append(valid)
                break
            if len(rows) < 50:
                break
            query = query.where(tuple_(model.created_at, model.id) < (rows[-1].created_at, rows[-1].id))
    return max(candidates, key=lambda item: (item["updated_at"], item["source_id"], item["source_type"])) if candidates else None


def progress_source(ticket, record) -> dict:
    result = {"schema_version": 1, "ticket_id": ticket.id, "ticket_number": ticket.ticket_number,
              "source_type": "none", "source_id": None, "visibility": "public", "fingerprint": None}
    if record:
        result.update({key: record[key] for key in ("source_type", "source_id", "visibility", "fingerprint")})
    return result


async def source_readable(session, user_id: str, role: str, source) -> bool:
    if not isinstance(source, dict) or source.get("schema_version") != 1:
        return False
    if source.get("visibility") not in {"public", "internal", "unclassified"}:
        return False
    if source["visibility"] != "public" and role not in STAFF:
        return False
    tid = source.get("ticket_id")
    if not isinstance(tid, str) or not 0 < len(tid) <= 64:
        return False
    ticket = await session.get(ProductionTicket, tid)
    if ticket is None or ticket.ticket_number != source.get("ticket_number") or role not in STAFF and ticket.user_id != user_id:
        return False
    if source.get("source_type") == "none":
        return source.get("source_id") is None and source.get("fingerprint") is None
    identifier = source.get("source_id")
    if not isinstance(identifier, str) or not 0 < len(identifier) <= 64:
        return False
    if source.get("source_type") == "comment":
        row = await session.get(ProductionTicketComment, identifier)
        if row is None or row.ticket_id != tid or row.visibility not in {"public", "internal", "unclassified"}:
            return False
        if row.visibility != "public" and role not in STAFF:
            return False
        # Visibility has its own current/original checks; changing it never upgrades the snapshot.
        original_kind = "internal_note" if source["visibility"] == "internal" else "unclassified_note" if source["visibility"] == "unclassified" else (
            "support_reply" if row.author_role in STAFF else "employee_update" if row.author_role == "employee" else "public_comment")
        actual = fingerprint(row.content, row.created_at, original_kind)
    elif source.get("source_type") == "audit":
        row = await session.get(BusinessAudit, identifier)
        audited = safe_audit(row) if row and row.entity_type == "ticket" and row.entity_id == tid else None
        if not audited:
            return False
        actual = fingerprint(audited[0], row.created_at, "audit")
    else:
        return False
    return actual == source.get("fingerprint")


async def readable_result(session, user_id: str, role: str, result):
    if not isinstance(result, dict) or result.get("final_state") != "ticket_status":
        return result
    lookup = result.get("ticket_lookup") or {}
    if lookup.get("outcome") == "not_found":
        return result
    if await source_readable(session, user_id, role, lookup.get("progress_source")):
        return result
    # Preserve protocol identifiers and terminal state, never raw answer or evidence metadata.
    return {**{key: value for key, value in result.items() if key in {
        "run_id", "status", "trace_id", "message_id", "final_state"}}, "answer": REDACTED, "access_redacted": True}
