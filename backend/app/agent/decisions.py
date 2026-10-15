"""Strict model decisions and one parent-owned budget for all child loops."""

import asyncio
import json
import math
import time
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BudgetExceeded(RuntimeError):
    pass


class TaskBudget:
    def __init__(
        self,
        *,
        max_tool_calls=12,
        max_model_calls=12,
        max_tokens=48000,
        timeout_seconds=90,
        clock=time.monotonic,
        output_reserve=1500,
        max_attempts=1,
    ):
        if any(
            type(n) is not int or n < 1
            for n in (max_tool_calls, max_model_calls, max_tokens)
        ):
            raise ValueError("Budget limits must be positive integers")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("Invalid deadline")
        self.max_tool_calls, self.max_model_calls, self.max_tokens = (
            max_tool_calls,
            max_model_calls,
            max_tokens,
        )
        self.clock, self.deadline = clock, clock() + timeout_seconds
        self.tool_calls = self.model_calls = self.reserved_tokens = (
            self.observed_tokens
        ) = 0
        self.cancelled = False
        self.input_tokens = self.output_tokens = 0
        self.models = set()
        self.usage_uncertain = False
        if (
            type(output_reserve) is not int
            or output_reserve < 1
            or type(max_attempts) is not int
            or not 1 <= max_attempts <= 3
        ):
            raise ValueError("Invalid model reservation bounds")
        self.output_reserve, self.max_attempts = output_reserve, max_attempts

    def check(self):
        if self.cancelled:
            raise asyncio.CancelledError
        if self.clock() >= self.deadline:
            raise BudgetExceeded("time budget exhausted")

    def remaining_seconds(self):
        self.check()
        return self.deadline - self.clock()

    def cancel(self):
        self.cancelled = True

    def take_tool(self):
        self.check()
        if self.tool_calls >= self.max_tool_calls:
            raise BudgetExceeded("tool budget exhausted")
        self.tool_calls += 1

    def take_model(self, prompt: str, *, output_reserve=None):
        self.check()
        # UTF-8 byte count is a conservative upper bound, not a tokenizer claim.
        output_reserve = (
            self.output_reserve if output_reserve is None else output_reserve
        )
        if type(output_reserve) is not int or output_reserve < 1:
            raise ValueError("Invalid output reservation")
        reservation = (len(prompt.encode("utf-8")) + output_reserve) * self.max_attempts
        if self.model_calls >= self.max_model_calls:
            raise BudgetExceeded("model budget exhausted")
        if self.reserved_tokens + reservation > self.max_tokens:
            raise BudgetExceeded("token budget exhausted")
        self.model_calls += 1
        self.reserved_tokens += reservation

    def observe_model(self, result):
        self.input_tokens += result.input_tokens
        self.output_tokens += result.output_tokens
        self.models.add(result.model)
        self.usage_uncertain = self.usage_uncertain or result.usage_uncertain
        self.observed_tokens += result.input_tokens + result.output_tokens
        if self.observed_tokens > self.max_tokens:
            raise BudgetExceeded("observed token budget exhausted")

    def snapshot(self):
        return {
            "tool_calls": self.tool_calls,
            "model_calls": self.model_calls,
            "reserved_token_upper_bound": self.reserved_tokens,
            "observed_tokens": self.observed_tokens,
            "cancelled": self.cancelled,
            "usage_uncertain": self.usage_uncertain,
        }


class EvidenceClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str = Field(min_length=1, max_length=1500)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    quote: str | None = Field(default=None, max_length=2000)
    kind: Literal["observation", "conclusion", "hypothesis"] = "hypothesis"


class AgentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    kind: Literal["call_tool", "ask_input", "finish"]
    tool: str | None = Field(default=None, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)
    answer: str | None = Field(default=None, min_length=1, max_length=6000)
    claims: list[EvidenceClaim] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def valid_action(self) -> Self:
        if len(json.dumps(self.arguments, allow_nan=False).encode()) > 8000:
            raise ValueError("Oversized arguments")
        forbidden = {
            "user_id",
            "tenant_id",
            "role",
            "access_level",
            "allowed_services",
            "authorization",
        }
        if forbidden.intersection(self.arguments):
            raise ValueError("Authority is server-owned")
        if self.kind == "call_tool":
            if not self.tool or self.answer is not None or self.claims:
                raise ValueError("Tool action cannot also finish")
        elif self.tool is not None or self.arguments:
            raise ValueError("Non-tool action cannot contain a tool call")
        if self.kind == "ask_input" and (not self.answer or self.claims):
            raise ValueError("Clarification requires a question without claims")
        return self
