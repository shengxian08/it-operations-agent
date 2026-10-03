from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FrozenSchema(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)


class TicketDraft(FrozenSchema):
    title: str = Field(min_length=1, max_length=300)
    category: str = Field(min_length=1, max_length=100)
    priority: Literal["low", "medium", "high", "critical"]
    description: str = Field(min_length=1, max_length=10_000)
    attempted_steps: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    problem: str | None = Field(default=None, min_length=2, max_length=2000)
    impact: str | None = Field(default=None, min_length=2, max_length=1000)
    intake_version: Literal[1] | None = None


class TicketStatusResult(FrozenSchema):
    found: bool
    ticket_number: str | None = None
    status: str | None = None
    latest_update: str
    updated_at: datetime | None = None
    update_kind: str | None = None
    progress_source: dict[str, Any] | None = None


class TicketCreateResult(FrozenSchema):
    ticket_number: str
    status: str
