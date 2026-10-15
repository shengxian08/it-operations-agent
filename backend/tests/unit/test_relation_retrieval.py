import importlib

import pytest


def module():
    try:
        return importlib.import_module("app.agent.relations")
    except ModuleNotFoundError:
        pytest.fail("Versioned evidence-backed relationship retrieval is missing")


@pytest.mark.asyncio
async def test_relation_expansion_is_two_hops_and_keeps_source_references():
    m = module()
    edges = [
        m.Relation(
            left=a,
            right=b,
            predicate="depends_on",
            revision="r1",
            source={"document_id": "d", "chunk_index": 0, "index_revision": "r1"},
            status="published",
        )
        for a, b in [("api", "db"), ("db", "storage"), ("storage", "disk")]
    ]

    async def readable(ref):
        return True

    result = await m.expand_relations(edges, ["api"], revision="r1", readable=readable)
    assert [(e.left, e.right) for e in result] == [("api", "db"), ("db", "storage")]
    assert all(e.source["document_id"] == "d" for e in result)


@pytest.mark.asyncio
async def test_private_edge_cannot_bridge_to_public_nodes_and_candidates_stay_hidden():
    m = module()

    def edge(a, b, doc, status="published", revision="r1"):
        return m.Relation(
            left=a,
            right=b,
            predicate="uses",
            revision=revision,
            source={"document_id": doc, "chunk_index": 0, "index_revision": revision},
            status=status,
        )

    async def readable(ref):
        return ref["document_id"] != "private"

    result = await m.expand_relations(
        [
            edge("api", "secret", "private"),
            edge("secret", "leak", "public"),
            edge("api", "candidate", "public", "candidate"),
            edge("api", "old", "public", revision="r0"),
        ],
        ["api"],
        revision="r1",
        readable=readable,
    )
    assert not result


def test_relation_requires_matching_original_revision_and_review_for_document_extraction():
    m = module()
    with pytest.raises(ValueError):
        m.Relation(
            left="api",
            right="db",
            predicate="uses",
            revision="r1",
            source={"document_id": "d", "index_revision": "r0", "chunk_index": 0},
        )
    candidates = m.extract_candidates(
        "服务 api depends_on db\n这是一条不明确的推测",
        {"document_id": "d", "index_revision": "r1", "chunk_index": 0},
    )
    assert len(candidates) == 1
    assert candidates[0].status == "candidate"
