from typing import Any, Literal, NotRequired, Required, TypedDict

from app.rag.retriever import Citation, RetrievalHit
from app.schemas import TicketDraft


AgentIntent = Literal["knowledge", "ticket_lookup", "ticket_create"]
FinalState = Literal["answered", "awaiting_confirmation", "handoff"]


class AgentState(TypedDict):
    user_id: Required[str]
    conversation_id: Required[str]
    message: Required[str]
    step_count: Required[int]
    intent: NotRequired[AgentIntent]
    retrieval_hits: NotRequired[list[RetrievalHit]]
    citations: NotRequired[list[Citation]]
    ticket_number: NotRequired[str]
    ticket_draft: NotRequired[TicketDraft]
    confirmation_token: NotRequired[str]
    tool_history: NotRequired[list[dict[str, Any]]]
    answer: NotRequired[str]
    final_state: NotRequired[FinalState]
    handoff_reason: NotRequired[str]
    trace_id: NotRequired[str]
    error: NotRequired[str]


class AgentStateUpdate(TypedDict, total=False):
    user_id: str
    conversation_id: str
    message: str
    step_count: int
    intent: AgentIntent
    retrieval_hits: list[RetrievalHit]
    citations: list[Citation]
    ticket_number: str
    ticket_draft: TicketDraft
    confirmation_token: str
    tool_history: list[dict[str, Any]]
    answer: str
    final_state: FinalState
    handoff_reason: str
    trace_id: str
    error: str
