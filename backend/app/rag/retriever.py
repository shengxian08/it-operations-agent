import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from qdrant_client import AsyncQdrantClient, models
from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import KnowledgeChunk as StoredKnowledgeChunk
from app.db.models import KnowledgeDocument
from app.rag.ingest import Embedder, search_text, tokenize


ACCESS_HIERARCHY = {
    "employee": ("employee",),
    "support": ("employee", "support"),
    "admin": ("employee", "support", "admin"),
}


@dataclass(frozen=True, slots=True)
class Citation:
    document_id: str
    source_title: str
    source_path: str
    chunk_index: int
    excerpt: str


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    citation: Citation
    score: float
    vector_score: float
    bm25_score: float
    combined_score: float


class Reranker(Protocol):
    async def score(self, query: str, documents: Sequence[str]) -> list[float]: ...


class LexicalReranker:
    async def score(self, query: str, documents: Sequence[str]) -> list[float]:
        query_tokens = set(tokenize(query))
        normalized_query = query.casefold().strip()
        identifiers = set(
            re.findall(r"\b[a-z][a-z0-9]*-\d+\b", normalized_query)
        )
        scores: list[float] = []
        for document in documents:
            normalized_document = document.casefold()
            document_tokens = set(tokenize(normalized_document))
            overlap = len(query_tokens & document_tokens) / max(len(query_tokens), 1)
            phrase_bonus = (
                0.5
                if normalized_query and normalized_query in normalized_document
                else 0.0
            )
            identifier_bonus = 2.0 * sum(
                identifier in normalized_document for identifier in identifiers
            )
            scores.append(overlap + phrase_bonus + identifier_bonus)
        return scores


class SentenceTransformerReranker:
    """Optional CrossEncoder adapter; loaded only when explicitly selected."""

    def __init__(self, model_name: str) -> None:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as error:
            raise RuntimeError(
                "install the 'rag' optional dependencies for semantic reranking"
            ) from error
        self._model = CrossEncoder(model_name)

    async def score(self, query: str, documents: Sequence[str]) -> list[float]:
        values = await asyncio.to_thread(
            self._model.predict,
            [(query, document) for document in documents],
        )
        return [float(value) for value in values]


class HybridRetriever:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        qdrant: AsyncQdrantClient,
        embedder: Embedder,
        reranker: Reranker,
        *,
        collection_name: str = "knowledge_chunks",
    ) -> None:
        self._session_factory = session_factory
        self._qdrant = qdrant
        self._embedder = embedder
        self._reranker = reranker
        self._collection_name = collection_name

    async def retrieve(
        self,
        query: str,
        *,
        user_access_level: str,
        limit: int = 5,
    ) -> list[RetrievalHit]:
        if not query.strip() or limit <= 0:
            return []
        try:
            allowed_levels = ACCESS_HIERARCHY[user_access_level]
        except KeyError as error:
            raise ValueError(f"unknown access level: {user_access_level}") from error

        chunks = await self._load_accessible_chunks(allowed_levels)
        if not chunks:
            return []
        chunks_by_id = {chunk.id: chunk for chunk in chunks}

        query_vector = (await self._embedder.encode([query]))[0]
        vector_result = await self._qdrant.query_points(
            collection_name=self._collection_name,
            query=query_vector,
            query_filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="access_level",
                        match=models.MatchAny(any=list(allowed_levels)),
                    )
                ]
            ),
            limit=20,
            with_payload=False,
        )
        vector_scores = {
            str(point.id): float(point.score)
            for point in vector_result.points
            if str(point.id) in chunks_by_id
        }

        search_texts = [
            search_text(chunk.source_title, chunk.content) for chunk in chunks
        ]
        tokenized_corpus = [tokenize(text) for text in search_texts]
        query_tokens = tokenize(query)
        bm25 = BM25Okapi(tokenized_corpus)
        raw_bm25 = bm25.get_scores(query_tokens)
        bm25_scores = {
            chunk.id: float(score)
            for chunk, score in sorted(
                zip(chunks, raw_bm25, strict=True),
                key=lambda item: item[1],
                reverse=True,
            )[:20]
        }

        candidate_ids = set(vector_scores) | set(bm25_scores)
        normalized_vector = _min_max(vector_scores)
        normalized_bm25 = _min_max(bm25_scores)
        combined = {
            chunk_id: 0.65 * normalized_vector.get(chunk_id, 0.0)
            + 0.35 * normalized_bm25.get(chunk_id, 0.0)
            for chunk_id in candidate_ids
        }
        ordered_ids = sorted(candidate_ids, key=combined.get, reverse=True)
        rerank_scores = await self._reranker.score(
            query,
            [
                search_text(
                    chunks_by_id[chunk_id].source_title,
                    chunks_by_id[chunk_id].content,
                )
                for chunk_id in ordered_ids
            ],
        )
        scored_ids = sorted(
            zip(ordered_ids, rerank_scores, strict=True),
            key=lambda item: (item[1], combined[item[0]]),
            reverse=True,
        )[: min(limit, 5)]

        return [
            _to_hit(
                chunks_by_id[chunk_id],
                rerank_score,
                vector_scores.get(chunk_id, 0.0),
                bm25_scores.get(chunk_id, 0.0),
                combined[chunk_id],
            )
            for chunk_id, rerank_score in scored_ids
        ]

    async def _load_accessible_chunks(
        self,
        allowed_levels: Sequence[str],
    ) -> list[StoredKnowledgeChunk]:
        async with self._session_factory() as session:
            result = await session.scalars(
                select(StoredKnowledgeChunk)
                .join(
                    KnowledgeDocument,
                    KnowledgeDocument.id == StoredKnowledgeChunk.document_id,
                )
                .where(
                    KnowledgeDocument.status == "active",
                    StoredKnowledgeChunk.access_level.in_(allowed_levels),
                    StoredKnowledgeChunk.chunk_metadata[
                        "collection_name"
                    ].astext
                    == self._collection_name,
                )
                .order_by(
                    StoredKnowledgeChunk.source_path,
                    StoredKnowledgeChunk.chunk_index,
                )
            )
            return list(result)


def _min_max(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    minimum = min(scores.values())
    maximum = max(scores.values())
    if maximum == minimum:
        value = 1.0 if maximum > 0 else 0.0
        return {key: value for key in scores}
    return {
        key: (score - minimum) / (maximum - minimum)
        for key, score in scores.items()
    }


def _to_hit(
    chunk: StoredKnowledgeChunk,
    rerank_score: float,
    vector_score: float,
    bm25_score: float,
    combined_score: float,
) -> RetrievalHit:
    excerpt = " ".join(chunk.content.split())[:240]
    return RetrievalHit(
        citation=Citation(
            document_id=chunk.document_id,
            source_title=chunk.source_title,
            source_path=chunk.source_path,
            chunk_index=chunk.chunk_index,
            excerpt=excerpt,
        ),
        score=rerank_score,
        vector_score=vector_score,
        bm25_score=bm25_score,
        combined_score=combined_score,
    )
