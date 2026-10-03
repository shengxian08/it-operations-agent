import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Protocol

from qdrant_client import AsyncQdrantClient, models
from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import KnowledgeChunk as StoredKnowledgeChunk
from app.db.models import KnowledgeDocument
from app.rag.ingest import Embedder, chunk_search_text, chunk_structure, tokenize
from app.rag.markdown import PARSER_VERSION
from app.rag.pdf import PDF_MARKER


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
    page_number: int | None = None
    table_id: str | None = None
    row_index: int | None = None
    index_revision: str | None = None
    section_path: tuple[str, ...] = ()
    char_start: int | None = None
    char_end: int | None = None


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
            title = normalized_document.partition("\n")[0]
            title_tokens = set(tokenize(title))
            overlap = len(query_tokens & document_tokens) / max(len(query_tokens), 1)
            phrase_bonus = (
                0.5
                if normalized_query and normalized_query in normalized_document
                else 0.0
            )
            identifier_bonus = 2.0 * sum(
                identifier in normalized_document for identifier in identifiers
            )
            title_matches = {
                token
                for token in query_tokens & title_tokens
                if len(token) > 1 and token not in {"公司", "应该", "检查", "什么", "怎么", "处理"}
            }
            title_bonus = min(0.08 * len(title_matches), 0.32)
            if any(phrase in normalized_query for phrase in ("连不上", "无法连接", "连接失败")):
                title_bonus += 0.18 * ("连接" in title) + 0.18 * ("故障" in title)
            scores.append(overlap + phrase_bonus + identifier_bonus + title_bonus)
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
            chunk_search_text(chunk) for chunk in chunks
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
                chunk_search_text(chunks_by_id[chunk_id])
                for chunk_id in ordered_ids
            ],
        )
        ranked_ids = sorted(
            zip(ordered_ids, rerank_scores, strict=True),
            key=lambda item: (item[1], combined[item[0]]),
            reverse=True,
        )
        scored_ids: list[tuple[str, float]] = []
        document_counts: dict[str, int] = {}
        for chunk_id, rerank_score in ranked_ids:
            source_path = chunks_by_id[chunk_id].source_path
            if document_counts.get(source_path, 0) >= 2:
                continue
            scored_ids.append((chunk_id, rerank_score))
            document_counts[source_path] = document_counts.get(source_path, 0) + 1
            if len(scored_ids) >= min(limit, 5):
                break

        return [
            _to_hit(
                chunks_by_id[chunk_id],
                query,
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
    query: str,
    rerank_score: float,
    vector_score: float,
    bm25_score: float,
    combined_score: float,
) -> RetrievalHit:
    marker = PDF_MARKER.match(chunk.content) if chunk.source_path.lower().endswith(".pdf") else None
    metadata = chunk_structure(chunk)
    structured_markdown = (not chunk.source_path.lower().endswith(".pdf") and metadata is not None
                           and metadata.get("parser_version") == PARSER_VERSION)
    excerpt = chunk.content if marker or structured_markdown else _relevant_excerpt(chunk.content, query, chunk.source_title)
    return RetrievalHit(
        citation=Citation(
            document_id=chunk.document_id,
            source_title=chunk.source_title,
            source_path=chunk.source_path,
            chunk_index=chunk.chunk_index,
            excerpt=excerpt,
            page_number=int(marker.group("page")) if marker else None,
            table_id=marker.group("table") if marker else None,
            row_index=int(marker.group("row")) if marker and marker.group("row") else None,
            index_revision=getattr(chunk, "revision_id", None),
            section_path=tuple(metadata["section_path"]) if structured_markdown else (),
            char_start=chunk.char_start if structured_markdown else None,
            char_end=chunk.char_end if structured_markdown else None,
        ),
        score=rerank_score,
        vector_score=vector_score,
        bm25_score=bm25_score,
        combined_score=combined_score,
    )


def _relevant_excerpt(content: str, query: str, source_title: str) -> str:
    normalized = " ".join(content.split())
    if len(normalized) <= 240:
        return normalized

    title = source_title.casefold()
    question = query[:200].casefold()
    matches = SequenceMatcher(
        None, question, normalized.casefold(), autojunk=False
    ).get_matching_blocks()
    relevant = [
        match
        for match in matches
        if match.size >= 3
        and question[match.a : match.a + match.size].strip()
        and question[match.a : match.a + match.size] not in title
    ]
    if not relevant:
        return normalized[:239] + "…"

    match = max(relevant, key=lambda item: item.size)
    headings = list(re.finditer(r"(?<!\d)\d+(?:\.\d+)+\s+", normalized))
    for index in range(len(headings) - 1, -1, -1):
        heading = headings[index]
        if heading.start() > match.b:
            continue
        if match.b - heading.start() <= 240:
            section_end = (
                headings[index + 1].start()
                if index + 1 < len(headings)
                else len(normalized)
            )
            section = normalized[heading.start() : section_end].strip()
            if len(section) <= 320:
                return section
        break

    start = max(0, match.b - 24)
    end = min(len(normalized), start + 240)
    return ("…" if start else "") + normalized[start:end] + ("…" if end < len(normalized) else "")
