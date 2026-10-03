import importlib.util
from types import SimpleNamespace
import pytest


def test_production_knowledge_service_is_available():
    assert importlib.util.find_spec("app.production.knowledge") is not None


def test_shared_embedder_rejects_unpinned_semantic_model():
    import pytest
    from app.production.knowledge import build_embedder

    with pytest.raises(ValueError, match="revision"):
        build_embedder(SimpleNamespace(embedding_mode="semantic", embedding_model="BAAI/bge-small-zh-v1.5", embedding_revision=""))


def test_revision_collections_are_isolated():
    from app.production.knowledge import revision_collection

    assert revision_collection("knowledge", "abc-123") == "knowledge_abc123"
    assert revision_collection("knowledge", "abc-124") != revision_collection("knowledge", "abc-123")


def test_index_build_work_is_queued_from_the_api():
    from app.production.knowledge import KnowledgeService
    assert hasattr(KnowledgeService, "enqueue_publish")
    assert hasattr(KnowledgeService, "enqueue_deactivate")
    assert hasattr(KnowledgeService, "execute_publish")
    assert hasattr(KnowledgeService, "execute_deactivate")


@pytest.mark.asyncio
async def test_bge_query_uses_official_instruction_but_documents_do_not():
    from app.rag.ingest import SentenceTransformerEmbedder
    class Recorder(SentenceTransformerEmbedder):
        def __init__(self):
            self.query_instruction = "为这个句子生成表示以用于检索相关文章："
            self.seen = []
        async def encode(self, texts):
            self.seen.extend(texts)
            return [[1.0] for _ in texts]
    embedder = Recorder()
    await embedder.encode_query("VPN故障")
    await embedder.encode(["VPN指南文档"])
    assert embedder.seen == ["为这个句子生成表示以用于检索相关文章：VPN故障", "VPN指南文档"]


def test_semantic_dimensions_follow_the_verified_model_configuration():
    from app.production.knowledge import expected_dimensions
    settings = SimpleNamespace(embedding_mode="sentence-transformer", embedding_dimensions=512)
    assert expected_dimensions(settings) == 512
    settings.embedding_dimensions = 768
    assert expected_dimensions(settings) == 768
