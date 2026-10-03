"""Tokenizer interface tests; these controlled counts are not semantic quality."""
from types import SimpleNamespace

import pytest

from app.rag.ingest import SentenceTransformerEmbedder


class Vector(list):
    def tolist(self):
        return list(self)


def adapter_for_counts(counts, *, model_limit=512):
    tokenizer_calls, model_calls = [], []
    def tokenizer(texts, **options):
        tokenizer_calls.append((list(texts), options))
        return {"input_ids": [list(range(count)) for count in counts]}
    def encode(texts, **options):
        model_calls.append(list(texts))
        return [Vector([1.0, 0.0]) for text in texts]
    adapter = SentenceTransformerEmbedder.__new__(SentenceTransformerEmbedder)
    adapter._model = SimpleNamespace(tokenizer=tokenizer, encode=encode, max_seq_length=model_limit)
    adapter.dimensions = 2
    return adapter, tokenizer_calls, model_calls


@pytest.mark.asyncio
async def test_exact_512_tokens_checks_complete_source_context_before_model_encoding():
    adapter, tokenizer_calls, model_calls = adapter_for_counts([512])
    text = "Guide\n章节：Guide > E-42\n```sh\necho inspect\n```"
    assert await adapter.encode([text]) == [[1.0, 0.0]]
    assert tokenizer_calls[0][0] == [text]
    assert tokenizer_calls[0][1]["truncation"] is False
    assert tokenizer_calls[0][1]["add_special_tokens"] is True
    assert model_calls == [[text]]


@pytest.mark.asyncio
@pytest.mark.parametrize("count,model_limit", [(513, 512), (513, 1024), (257, 256)])
async def test_over_budget_never_silently_truncates_or_calls_the_model(count, model_limit):
    adapter, tokenizer_calls, model_calls = adapter_for_counts([count], model_limit=model_limit)
    with pytest.raises(ValueError, match="token|上限"):
        await adapter.encode(["complete title and source context"])
    assert len(tokenizer_calls) == 1 and not model_calls


@pytest.mark.asyncio
async def test_actual_token_counts_are_available_for_pre_index_audit():
    adapter, tokenizer_calls, model_calls = adapter_for_counts([41, 512])
    audit = getattr(adapter, "token_counts", None)
    assert callable(audit), "Semantic adapter must expose complete input token auditing"
    assert await audit(["one", "two"]) == [41, 512]
    assert len(tokenizer_calls) == 1 and not model_calls
