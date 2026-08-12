import asyncio
import os
import sys
from pathlib import Path

from qdrant_client import AsyncQdrantClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if (BACKEND_ROOT / "app").is_dir():
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.db.session import async_engine, async_session_factory  # noqa: E402
from app.rag.ingest import (  # noqa: E402
    DeterministicEmbedder,
    KnowledgeIngestor,
    SentenceTransformerEmbedder,
)


KNOWLEDGE_DIR = Path(
    os.getenv("KNOWLEDGE_DIR", str(PROJECT_ROOT / "data" / "knowledge"))
).resolve()
COLLECTION_NAME = os.getenv("QDRANT_COLLECTION", "knowledge_chunks")


def build_embedder() -> DeterministicEmbedder | SentenceTransformerEmbedder:
    mode = os.getenv("RAG_EMBEDDING_MODE", "mock")
    if mode == "mock":
        dimensions = int(os.getenv("RAG_EMBEDDING_DIMENSIONS", "256"))
        return DeterministicEmbedder(dimensions=dimensions)
    if mode == "sentence-transformer":
        model_name = os.getenv(
            "RAG_EMBEDDING_MODEL",
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        )
        return SentenceTransformerEmbedder(model_name)
    raise ValueError(f"unsupported RAG_EMBEDDING_MODE: {mode}")


async def main() -> None:
    settings = get_settings()
    qdrant = AsyncQdrantClient(url=settings.qdrant_url)
    try:
        ingestor = KnowledgeIngestor(
            async_session_factory,
            qdrant,
            build_embedder(),
            collection_name=COLLECTION_NAME,
        )
        summary = await ingestor.ingest_directory(KNOWLEDGE_DIR)
        print(
            f"Ingested {summary.document_count} documents and "
            f"{summary.chunk_count} chunks into {COLLECTION_NAME}."
        )
    finally:
        await qdrant.close()
        await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
