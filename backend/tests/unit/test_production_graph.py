import json
from unittest.mock import AsyncMock

import pytest

from app.agent.graph import GraphDependencies, build_graph
from app.llm.providers import ChatResult
from app.rag.retriever import Citation, RetrievalHit
from app.services.chat import ChatService


def dependencies(answer):
    hit = RetrievalHit(Citation("doc", "VPN", "vpn.md", 0, "检查网络"), 0.9, 0.9, 0.9, 0.9)
    return GraphDependencies(
        retriever=AsyncMock(retrieve=AsyncMock(return_value=[hit])),
        chat_provider=AsyncMock(complete=AsyncMock(return_value=ChatResult(answer, 10, 5, "test"))),
        ticket_service=AsyncMock(),
        require_structured_citations=True,
    )


@pytest.mark.asyncio
async def test_unknown_citation_cannot_become_answered():
    deps = dependencies(json.dumps({"answer": "建议", "citation_ids": [99]}))
    result = await build_graph(deps).ainvoke({"user_id":"user", "conversation_id":"conversation", "message":"VPN怎么处理", "step_count":0})
    assert result["final_state"] == "handoff"
    assert result["citations"] == []
    assert "已转人工" not in result["answer"], "the graph has not persisted a human support record"


@pytest.mark.asyncio
async def test_history_is_context_only_and_only_used_citations_return():
    deps = dependencies(json.dumps({"answer": "检查网络 [1]", "citation_ids": [1]}))
    service = ChatService(None, deps, deps.ticket_service)
    events = [event async for event in service.stream_run(user_id="user",conversation_id="conversation",content="VPN怎么处理",trace_id="trace",run_id="run",history=[{"role":"user","content":"我已重新连接"}])]
    assert events[-1].name == "final"
    assert events[-1].data["final_state"] == "answered"
    assert events[-1].data["usage"]["input_tokens"] == 10
    assert len(events[-1].data["citations"]) == 1
    assert "我已重新连接" in deps.chat_provider.complete.call_args.args[0]


@pytest.mark.asyncio
async def test_citation_subset_is_renumbered_to_returned_evidence():
    deps = dependencies(json.dumps({"answer": "检查第二条 [2]", "citation_ids": [2]}))
    first = deps.retriever.retrieve.return_value[0]
    second = RetrievalHit(Citation("doc2", "Network", "network.md", 0, "检查网线"), .9, .9, .9, .9)
    deps.retriever.retrieve.return_value = [first, second]
    result = await build_graph(deps).ainvoke({"user_id":"user", "conversation_id":"conversation", "message":"VPN怎么处理", "step_count":0})
    assert result["final_state"] == "answered"
    assert result["answer"] == "检查第二条 [1]"
    assert result["citations"][0].document_id == "doc2"
