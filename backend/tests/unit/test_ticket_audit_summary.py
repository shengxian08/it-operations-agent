from app.core.telemetry import redact_sensitive
from app.schemas import TicketDraft
from app.tickets.service import TicketService


def test_audit_summary_remains_exact_after_redaction_of_numeric_identifiers() -> None:
    draft = TicketDraft(
        title="VPN 无法连接",
        category="network",
        priority="medium",
        description="客户端提示身份验证失败",
        attempted_steps=("重新打开客户端",),
    )
    conversation_id = "test-conversation-6577248378774619ba1ec9b996420ad4"

    summary = TicketService._request_summary(
        draft,
        TicketService._draft_hash(draft),
        conversation_id,
    )

    assert redact_sensitive(summary) == summary
    assert conversation_id not in summary
    assert draft.title not in summary
    assert draft.description not in summary
