import json
import logging

import httpx
import pytest

from app.core.config import Settings
from app.llm.providers import MockChatProvider, OpenAICompatibleProvider


def test_openai_provider_rejects_non_openai_settings() -> None:
    with pytest.raises(ValueError, match="openai settings"):
        OpenAICompatibleProvider(Settings(model_mode="mock"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prompt", "expected_text"),
    [
        ("VPN 连不上", "请先确认网络连接，然后重新连接 VPN。"),
        ("查询工单 IT-2026-0001 的状态", "我会通过工单查询工具核对当前状态。"),
        (
            "帮我创建工单",
            "创建工单前，请补充问题标题、类别、优先级和问题描述。",
        ),
        ("打印机发出奇怪声音", "现有知识不足以确认该问题，建议转人工支持。"),
    ],
)
async def test_mock_provider_returns_deterministic_branch_answers(
    prompt: str,
    expected_text: str,
) -> None:
    provider = MockChatProvider()

    first = await provider.complete(prompt)
    second = await provider.complete(prompt)

    assert first == second
    assert first.text == expected_text
    assert first.model == "mock-itops-v1"
    assert first.input_tokens > 0
    assert first.output_tokens > 0


@pytest.mark.asyncio
async def test_mock_provider_uses_stable_token_estimate() -> None:
    result = await MockChatProvider().complete("VPN 连不上")

    assert result.input_tokens == 3


@pytest.mark.asyncio
async def test_openai_provider_maps_chat_completion_without_logging_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    requests: list[httpx.Request] = []

    async def handle_request(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status_code=200,
            request=request,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1_786_400_000,
                "model": "test-model-2026-08-12",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "请重新连接 VPN。",
                            "refusal": None,
                        },
                        "finish_reason": "stop",
                        "logprobs": None,
                    }
                ],
                "usage": {
                    "prompt_tokens": 7,
                    "completion_tokens": 5,
                    "total_tokens": 12,
                },
            },
        )

    settings = Settings(
        model_mode="openai",
        openai_base_url="https://models.example.test/v1",
        openai_api_key="unit-test-secret",
        openai_model="test-model",
    )
    transport = httpx.MockTransport(handle_request)

    async with httpx.AsyncClient(transport=transport) as http_client:
        provider = OpenAICompatibleProvider(settings, http_client=http_client)
        with caplog.at_level(logging.INFO, logger="app.llm.providers"):
            result = await provider.complete("VPN error from employee laptop")

    assert result.text == "请重新连接 VPN。"
    assert result.input_tokens == 7
    assert result.output_tokens == 5
    assert result.model == "test-model-2026-08-12"

    assert len(requests) == 1
    request = requests[0]
    assert request.url == "https://models.example.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer unit-test-secret"
    assert json.loads(request.content) == {
        "messages": [
            {
                "role": "user",
                "content": "VPN error from employee laptop",
            }
        ],
        "model": "test-model",
    }

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.model == "test-model-2026-08-12"
    assert record.input_tokens == 7
    assert record.output_tokens == 5
    assert record.latency_ms >= 0
    assert "unit-test-secret" not in caplog.text
    assert "VPN error from employee laptop" not in caplog.text
