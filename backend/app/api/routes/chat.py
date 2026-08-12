import json
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.routes.tickets import ApiProblem
from app.services.chat import ChatService, ConversationNotFoundError, MessageNotFoundError


router = APIRouter(prefix="/api", tags=["chat"])


class StreamMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    user_id: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=10_000)


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: Literal["resolved", "unresolved"]


def get_chat_service(request: Request) -> ChatService:
    return request.app.state.chat_service


@router.post("/conversations/{conversation_id}/messages:stream")
async def stream_message(
    conversation_id: str,
    payload: StreamMessageRequest,
    request: Request,
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> StreamingResponse:
    trace_id = request.state.trace_id
    try:
        events = service.stream_message(
            user_id=payload.user_id,
            conversation_id=conversation_id,
            content=payload.content,
            trace_id=trace_id,
        )
        first_event = await anext(events)
    except ConversationNotFoundError as error:
        raise ApiProblem(404, "conversation_not_found", "Conversation was not found.") from error

    async def stream() -> AsyncIterator[str]:
        yield _format_sse(first_event.name, {**first_event.data, "trace_id": trace_id})
        async for event in events:
            yield _format_sse(event.name, {**event.data, "trace_id": trace_id})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Trace-Id": trace_id},
    )


@router.post("/messages/{message_id}/feedback")
async def record_feedback(
    message_id: str,
    payload: FeedbackRequest,
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> dict[str, str]:
    try:
        await service.record_feedback(message_id=message_id, feedback=payload.feedback)
    except MessageNotFoundError as error:
        raise ApiProblem(404, "message_not_found", "Message was not found.") from error
    return {"message_id": message_id, "feedback": payload.feedback}


def _format_sse(event_name: str, data: dict[str, object]) -> str:
    serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event_name}\ndata: {serialized}\n\n"
