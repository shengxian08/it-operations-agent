import importlib
import json

import pytest

from app.llm.providers import ChatResult
from app.rag.retriever import Citation, RetrievalHit


def module():
    try:
        return importlib.import_module("app.agent.agentic_rag")
    except ModuleNotFoundError:
        pytest.fail("The bounded Agentic RAG workflow is missing")


class Retriever:
    def __init__(self):
        self.queries = []

    async def retrieve(self, query, *, user_access_level, limit=5):
        assert user_access_level == "employee"
        self.queries.append(query)
        if query != "VPN 错误 691":
            return []
        return [
            RetrievalHit(
                Citation(
                    "vpn",
                    "VPN",
                    "public/vpn.md",
                    0,
                    "错误691：核对账户和密码。",
                    index_revision="r1",
                ),
                0.9,
                0.9,
                0.9,
                0.9,
            )
        ]


class Script:
    def __init__(self, *decisions):
        self.decisions = iter(decisions)

    async def complete(self, prompt):
        return ChatResult(
            json.dumps(next(self.decisions), ensure_ascii=False),
            20,
            20,
            "scripted-control",
        )


@pytest.mark.asyncio
async def test_agent_rewrites_an_empty_search_and_keeps_original_provenance():
    m = module()
    retriever = Retriever()
    provider = Script(
        {
            "kind": "call_tool",
            "tool": "search_knowledge",
            "arguments": {"query": "VPN 错误 691"},
        },
        {"kind": "finish"},
    )
    result = await m.AgenticRetriever(retriever, provider).run(
        "连不上怎么办", user_access_level="employee"
    )
    assert retriever.queries == ["连不上怎么办", "VPN 错误 691"]
    assert result.hits[0].citation.index_revision == "r1"
    assert result.trace[1]["phase"] == "decision"
    assert result.error is None


@pytest.mark.asyncio
async def test_third_additional_search_is_never_executed():
    m = module()
    retriever = Retriever()
    provider = Script(
        *(
            {
                "kind": "call_tool",
                "tool": "search_knowledge",
                "arguments": {"query": f"q{i}"},
            }
            for i in range(3)
        )
    )
    result = await m.AgenticRetriever(retriever, provider).run(
        "unknown", user_access_level="employee"
    )
    assert retriever.queries == ["unknown", "q0", "q1"]
    assert result.error == "retrieval_round_limit"


@pytest.mark.asyncio
async def test_uncertain_problem_returns_clarification_without_fabricating_evidence():
    m = module()
    result = await m.AgenticRetriever(
        Retriever(), Script({"kind": "ask_input", "answer": "具体错误码是什么？"})
    ).run("它不行了", user_access_level="employee")
    assert result.hits == []
    assert result.clarification == "具体错误码是什么？"


@pytest.mark.asyncio
async def test_model_cannot_read_an_unretrieved_source():
    m = module()
    provider = Script(
        {
            "kind": "call_tool",
            "tool": "read_source",
            "arguments": {"document_id": "secret", "chunk_index": 0},
        }
    )
    result = await m.AgenticRetriever(Retriever(), provider).run(
        "unknown", user_access_level="employee"
    )
    assert result.error == "source_not_in_evidence"
    assert not result.hits


@pytest.mark.asyncio
async def test_langgraph_stops_for_agent_clarification_before_answer_generation():
    from app.agent.graph import GraphDependencies, build_graph

    m = module()
    provider = Script({"kind": "ask_input", "answer": "错误码是什么？"})
    deps = GraphDependencies(
        Retriever(),
        provider,
        object(),
        agentic_retriever=m.AgenticRetriever(Retriever(), provider),
    )
    result = await build_graph(deps).ainvoke(
        {"user_id": "u", "conversation_id": "c", "message": "它不行了", "step_count": 0}
    )
    assert result["final_state"] == "knowledge_clarification"
    assert result["answer"] == "错误码是什么？"
    assert result["agent_trace"]


@pytest.mark.asyncio
async def test_invalid_agent_action_never_generates_answer_from_partial_hits():
    from app.agent.graph import GraphDependencies, build_graph

    provider = Script({"kind": "call_tool", "tool": "delete_index", "arguments": {}})
    retriever = Retriever()
    result = await build_graph(
        GraphDependencies(
            retriever,
            provider,
            object(),
            agentic_retriever=module().AgenticRetriever(retriever, provider),
        )
    ).ainvoke(
        {
            "user_id": "u",
            "conversation_id": "c",
            "message": "VPN 错误 691",
            "step_count": 0,
        }
    )
    assert result["final_state"] == "handoff"
    assert result["handoff_reason"] == "retrieval_failed"


@pytest.mark.asyncio
async def test_relation_context_reaches_decision_with_immutable_source():
    retriever = Retriever()
    retriever.latest_relations = {
        "graph_version": "g1",
        "knowledge_revision": "r1",
        "relations": [
            {
                "left": "VPN",
                "right": "identity",
                "predicate": "depends_on",
                "source": {
                    "document_id": "vpn",
                    "chunk_index": 0,
                    "index_revision": "r1",
                },
            }
        ],
    }

    class Provider:
        async def complete(self, prompt):
            data = json.loads(prompt.split("\n", 1)[1].rsplit("\n", 1)[0])
            assert data["relation_context"]["graph_version"] == "g1"
            return ChatResult('{"kind":"finish"}', 10, 10, "scripted")

    result = (
        await module()
        .AgenticRetriever(retriever, Provider())
        .run("VPN 错误 691", user_access_level="employee")
    )
    assert result.error is None
    assert any(t["phase"] == "relations" for t in result.trace)
