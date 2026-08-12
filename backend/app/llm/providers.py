import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol, cast

import httpx
from openai import AsyncOpenAI
from pydantic import HttpUrl, SecretStr

from app.core.config import Settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ChatResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


class ChatProvider(Protocol):
    async def complete(self, prompt: str) -> ChatResult: ...


class MockChatProvider:
    model = "mock-itops-v1"

    async def complete(self, prompt: str) -> ChatResult:
        text = self._answer_for(prompt)
        return ChatResult(
            text=text,
            input_tokens=self._estimate_tokens(prompt),
            output_tokens=self._estimate_tokens(text),
            model=self.model,
        )

    @staticmethod
    def _answer_for(prompt: str) -> str:
        normalized = prompt.strip()

        if "VPN" in normalized.upper():
            return "请先确认网络连接，然后重新连接 VPN。"
        if "工单" in normalized and any(
            keyword in normalized for keyword in ("查询", "状态", "进度")
        ):
            return "我会通过工单查询工具核对当前状态。"
        if any(
            keyword in normalized for keyword in ("建单", "创建工单", "提交工单")
        ):
            return "创建工单前，请补充问题标题、类别、优先级和问题描述。"
        return "现有知识不足以确认该问题，建议转人工支持。"

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, len(text.encode("utf-8")) // 4)


class OpenAICompatibleProvider:
    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if (
            settings.model_mode != "openai"
            or settings.openai_base_url is None
            or settings.openai_api_key is None
            or settings.openai_model is None
        ):
            raise ValueError(
                "OpenAICompatibleProvider requires validated openai settings"
            )

        base_url = cast(HttpUrl, settings.openai_base_url)
        api_key = cast(SecretStr, settings.openai_api_key)
        model = cast(str, settings.openai_model)

        self._model = model
        self._client = AsyncOpenAI(
            api_key=api_key.get_secret_value(),
            base_url=str(base_url),
            http_client=http_client,
        )

    async def complete(self, prompt: str) -> ChatResult:
        started_at = perf_counter()
        completion = await self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
        )
        latency_ms = int((perf_counter() - started_at) * 1000)

        usage = completion.usage
        result = ChatResult(
            text=completion.choices[0].message.content or "",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            model=completion.model,
        )
        logger.info(
            "model completion finished",
            extra={
                "model": result.model,
                "latency_ms": latency_ms,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            },
        )
        return result
