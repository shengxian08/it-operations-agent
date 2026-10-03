import json
import httpx
import pytest
from app.core.config import Settings
from app.llm.providers import OpenAICompatibleProvider


@pytest.mark.asyncio
async def test_formal_provider_sets_policy_output_limit_and_json_format():
    sent = []
    def response(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200,json={"id":"1","object":"chat.completion","created":1,"model":"test","choices":[{"index":0,"message":{"role":"assistant","content":"{}"},"finish_reason":"stop"}]})
    settings = Settings(_env_file=None,model_mode="openai",demo_enabled=False,openai_base_url="https://example.test/v1",openai_api_key="test",openai_model="test")
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        await OpenAICompatibleProvider(settings,http_client=client).complete("evidence")
    assert sent[0]["messages"][0]["role"] == "system"
    assert sent[0]["max_tokens"] == settings.model_max_output_tokens
    assert sent[0]["response_format"] == {"type":"json_object"}


@pytest.mark.asyncio
async def test_over_budget_prompt_is_rejected_before_external_call():
    settings=Settings(_env_file=None,model_mode="openai",openai_base_url="https://example.test/v1",openai_api_key="test",openai_model="test",budget_max_input_tokens=1000)
    def no_call(request):
        pytest.fail("oversized prompt reached external provider")
    async with httpx.AsyncClient(transport=httpx.MockTransport(no_call)) as client:
        with pytest.raises(ValueError,match="input budget"):
            await OpenAICompatibleProvider(settings,http_client=client).complete("x" * 1200)
