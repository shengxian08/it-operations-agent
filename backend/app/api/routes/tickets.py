from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.schemas import TicketDraft
from app.services.chat import ChatService, ConversationNotFoundError


router = APIRouter(prefix="/api", tags=["tickets"])


class ApiProblem(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


class TicketConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_id: str = Field(min_length=1, max_length=64)
    confirmation_token: str = Field(min_length=1, max_length=512)
    draft: "TicketConfirmationDraft"
    idempotency_key: str = Field(min_length=1, max_length=128)


class TicketConfirmationDraft(TicketDraft):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def get_chat_service(request: Request) -> ChatService:
    return request.app.state.chat_service


@router.post("/conversations/{conversation_id}/ticket-confirmations", status_code=201)
async def confirm_ticket(
    conversation_id: str,
    payload: TicketConfirmationRequest,
    request: Request,
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> dict[str, str]:
    try:
        return await service.confirm_ticket(
            user_id=payload.user_id,
            conversation_id=conversation_id,
            draft=payload.draft,
            confirmation_token=payload.confirmation_token,
            idempotency_key=payload.idempotency_key,
            trace_id=request.state.trace_id,
        )
    except ConversationNotFoundError as error:
        raise ApiProblem(404, "conversation_not_found", "Conversation was not found.") from error
    except PermissionError as error:
        raise ApiProblem(403, "confirmation_invalid", "Confirmation is invalid.") from error


@router.get("/tickets/{ticket_number}")
async def get_ticket(
    ticket_number: str,
    user_id: Annotated[str, Query(min_length=1, max_length=64)],
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> dict[str, str | bool | None]:
    result = await service.get_ticket_status(
        user_id=user_id,
        ticket_number=ticket_number,
    )
    if not result["found"]:
        raise ApiProblem(404, "ticket_not_found", "Ticket was not found.")
    return result
