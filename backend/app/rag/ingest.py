import asyncio
import hashlib
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient, models
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import KnowledgeChunk as StoredKnowledgeChunk
from app.db.models import KnowledgeDocument


SUPPORTED_EXTENSIONS = {".md", ".pdf"}
ACCESS_LEVELS = {"employee", "support", "admin"}
REQUIRED_ARTICLE_HEADINGS = {"适用对象", "前置条件", "操作步骤", "何时转人工"}


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    document_id: str
    source_title: str
    source_path: str
    version: str
    chunk_index: int
    content: str
    char_start: int
    char_end: int
    access_level: str = "employee"


@dataclass(frozen=True, slots=True)
class IngestionSummary:
    document_count: int
    chunk_count: int


class Embedder(Protocol):
    dimensions: int

    async def encode(self, texts: Sequence[str]) -> list[list[float]]: ...


class DeterministicEmbedder:
    """Small local embedder used for deterministic development and tests."""

    def __init__(self, *, dimensions: int = 256) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.dimensions = dimensions

    async def encode(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._encode_one(text) for text in texts]

    def _encode_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in tokenize(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        magnitude = math.sqrt(sum(value * value for value in vector))
        if magnitude:
            return [value / magnitude for value in vector]
        return vector


class SentenceTransformerEmbedder:
    """Optional production adapter; the model is loaded only when selected."""

    def __init__(self, model_name: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise RuntimeError(
                "install the 'rag' optional dependencies for semantic embeddings"
            ) from error

        self._model = SentenceTransformer(model_name)
        dimensions = self._model.get_sentence_embedding_dimension()
        if dimensions is None:
            raise ValueError("embedding model did not report its dimensions")
        self.dimensions = dimensions

    async def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = await asyncio.to_thread(
            self._model.encode,
            list(texts),
            normalize_embeddings=True,
        )
        return [vector.tolist() for vector in vectors]


def extract_text(path: str | Path) -> str:
    source_path = Path(path)
    extension = source_path.suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"unsupported knowledge file: {source_path.name}")

    if extension == ".md":
        try:
            text = source_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(
                f"Markdown file must be valid UTF-8: {source_path.name}"
            ) from error
    else:
        try:
            import pymupdf
        except ImportError as error:
            raise RuntimeError("PyMuPDF is required to read PDF knowledge") from error

        try:
            with pymupdf.open(source_path) as document:
                page_texts = []
                for page in document:
                    blocks = [
                        block[4].strip()
                        for block in page.get_text("blocks")
                        if block[4].strip()
                    ]
                    page_texts.append("\n\n".join(blocks))
                text = "\n\n".join(page_texts)
        except (FileNotFoundError, RuntimeError, ValueError) as error:
            raise ValueError(f"unable to decode PDF: {source_path.name}") from error

    if not text.strip():
        raise ValueError(f"knowledge file is empty: {source_path.name}")
    return text


def chunk_markdown(
    markdown: str,
    *,
    source_path: str,
    max_chars: int = 800,
    overlap_chars: int = 120,
    version: str | None = None,
    access_level: str = "employee",
    source_title: str | None = None,
) -> list[KnowledgeChunk]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("overlap_chars must be between zero and max_chars")
    if not markdown.strip():
        raise ValueError("knowledge text is empty")

    title_match = re.search(r"(?m)^#\s+(.+?)\s*$", markdown)
    resolved_title = source_title or (
        title_match.group(1).strip() if title_match else Path(source_path).stem
    )
    resolved_version = version or hashlib.sha256(
        markdown.encode("utf-8")
    ).hexdigest()[:12]
    document_id = str(
        uuid5(NAMESPACE_URL, f"knowledge:{source_path}:{resolved_version}")
    )
    paragraph_pattern = re.compile(
        r"\S(?:.*?\S)?(?=\n[ \t]*\n|[ \t\r\n]*\Z)",
        flags=re.DOTALL,
    )
    windows: list[tuple[int, int, str]] = []

    for match in paragraph_pattern.finditer(markdown):
        paragraph = match.group(0)
        stripped = paragraph.strip()
        if _is_metadata_or_heading(stripped):
            continue

        paragraph_start = match.start()
        if len(paragraph) <= max_chars:
            windows.append((paragraph_start, match.end(), paragraph))
            continue

        step = max_chars - overlap_chars
        local_start = 0
        while local_start < len(paragraph):
            local_end = min(local_start + max_chars, len(paragraph))
            windows.append(
                (
                    paragraph_start + local_start,
                    paragraph_start + local_end,
                    paragraph[local_start:local_end],
                )
            )
            if local_end == len(paragraph):
                break
            local_start += step

    if not windows:
        raise ValueError("knowledge text has no chunkable content")

    return [
        KnowledgeChunk(
            document_id=document_id,
            source_title=resolved_title,
            source_path=source_path,
            version=resolved_version,
            chunk_index=index,
            content=content,
            char_start=char_start,
            char_end=char_end,
            access_level=access_level,
        )
        for index, (char_start, char_end, content) in enumerate(windows)
    ]


def _is_metadata_or_heading(paragraph: str) -> bool:
    if paragraph.startswith("---") and paragraph.endswith("---"):
        return True
    return bool(re.fullmatch(r"#{1,6}\s+.+", paragraph))


def tokenize(text: str) -> list[str]:
    normalized = text.casefold()
    latin_tokens = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", normalized)
    cjk_runs = re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]+", normalized)
    cjk_tokens: list[str] = []
    for run in cjk_runs:
        cjk_tokens.extend(run)
        cjk_tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return latin_tokens + cjk_tokens


class KnowledgeIngestor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        qdrant: AsyncQdrantClient,
        embedder: Embedder,
        *,
        collection_name: str = "knowledge_chunks",
        max_chars: int = 800,
        overlap_chars: int = 120,
    ) -> None:
        self._session_factory = session_factory
        self._qdrant = qdrant
        self._embedder = embedder
        self._collection_name = collection_name
        self._max_chars = max_chars
        self._overlap_chars = overlap_chars

    async def ingest_directory(self, directory: str | Path) -> IngestionSummary:
        knowledge_dir = Path(directory)
        if not knowledge_dir.is_dir():
            raise ValueError(f"knowledge directory does not exist: {knowledge_dir}")
        unsupported = sorted(
            path.name
            for path in knowledge_dir.iterdir()
            if path.is_file() and path.suffix.lower() not in SUPPORTED_EXTENSIONS
        )
        if unsupported:
            raise ValueError(f"unsupported knowledge files: {', '.join(unsupported)}")
        paths = sorted(
            path
            for path in knowledge_dir.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        )
        if not paths:
            raise ValueError(f"knowledge directory is empty: {knowledge_dir}")
        return await self.ingest_paths(paths)

    async def ingest_paths(self, paths: Sequence[str | Path]) -> IngestionSummary:
        if not paths:
            raise ValueError("at least one knowledge file is required")
        await self._ensure_collection()

        total_chunks = 0
        for raw_path in paths:
            path = Path(raw_path)
            text = extract_text(path)
            metadata = parse_frontmatter(text) if path.suffix.lower() == ".md" else {}
            access_level = metadata.get("access_level", "employee")
            if access_level not in ACCESS_LEVELS:
                raise ValueError(
                    f"invalid access_level '{access_level}' in {path.name}"
                )
            if path.suffix.lower() == ".md":
                validate_article_structure(text, path.name)

            version = metadata.get("version") or hashlib.sha256(
                text.encode("utf-8")
            ).hexdigest()[:12]
            title = _resolve_title(text, path)
            _validate_database_fields(path.name, title, version)
            chunks = chunk_markdown(
                text,
                source_path=path.name,
                source_title=title,
                version=version,
                access_level=access_level,
                max_chars=self._max_chars,
                overlap_chars=self._overlap_chars,
            )
            vectors = await self._embedder.encode(
                [search_text(chunk.source_title, chunk.content) for chunk in chunks]
            )
            if len(vectors) != len(chunks):
                raise ValueError("embedder returned an unexpected vector count")
            if any(len(vector) != self._embedder.dimensions for vector in vectors):
                raise ValueError("embedder returned an unexpected vector dimension")

            await self._prepare_database_document(text, chunks)
            stale_point_ids = await self._upsert_vector_points(chunks, vectors)
            await self._activate_database_document(chunks[0])
            await self._cleanup_vector_points(chunks[0], stale_point_ids)
            total_chunks += len(chunks)

        return IngestionSummary(document_count=len(paths), chunk_count=total_chunks)

    async def _ensure_collection(self) -> None:
        if not await self._qdrant.collection_exists(self._collection_name):
            await self._qdrant.create_collection(
                collection_name=self._collection_name,
                vectors_config=models.VectorParams(
                    size=self._embedder.dimensions,
                    distance=models.Distance.COSINE,
                ),
            )
            return

        collection = await self._qdrant.get_collection(self._collection_name)
        vectors_config = collection.config.params.vectors
        if not isinstance(vectors_config, models.VectorParams):
            raise ValueError(
                f"Qdrant collection {self._collection_name} must use "
                "one unnamed vector"
            )
        if vectors_config.size != self._embedder.dimensions:
            raise ValueError(
                f"Qdrant collection {self._collection_name} vector size is "
                f"{vectors_config.size}; expected {self._embedder.dimensions}. "
                "Use a new collection name or perform a controlled rebuild."
            )
        if vectors_config.distance != models.Distance.COSINE:
            raise ValueError(
                f"Qdrant collection {self._collection_name} distance is "
                f"{vectors_config.distance}; expected Cosine. Use a new "
                "collection name or perform a controlled rebuild."
            )

    async def _prepare_database_document(
        self,
        text: str,
        chunks: Sequence[KnowledgeChunk],
    ) -> None:
        first = chunks[0]
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        async with self._session_factory.begin() as session:
            document = await session.scalar(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.source_path == first.source_path,
                    KnowledgeDocument.version == first.version,
                )
            )
            if document is None:
                document = KnowledgeDocument(
                    id=first.document_id,
                    source_title=first.source_title,
                    source_path=first.source_path,
                    version=first.version,
                    content_hash=content_hash,
                    access_level=first.access_level,
                    status="inactive",
                )
                session.add(document)
            else:
                if document.content_hash != content_hash:
                    raise ValueError(
                        f"knowledge file {first.source_path} changed without "
                        f"a version bump from {first.version}"
                    )
                document.source_title = first.source_title
                document.access_level = first.access_level

            await session.execute(
                delete(StoredKnowledgeChunk).where(
                    StoredKnowledgeChunk.document_id == first.document_id
                )
            )
            for chunk in chunks:
                session.add(
                    StoredKnowledgeChunk(
                        id=_chunk_id(chunk),
                        document_id=chunk.document_id,
                        chunk_index=chunk.chunk_index,
                        content=chunk.content,
                        source_title=chunk.source_title,
                        source_path=chunk.source_path,
                        char_start=chunk.char_start,
                        char_end=chunk.char_end,
                        access_level=chunk.access_level,
                        chunk_metadata={
                            "version": chunk.version,
                            "collection_name": self._collection_name,
                        },
                    )
                )

    async def _upsert_vector_points(
        self,
        chunks: Sequence[KnowledgeChunk],
        vectors: Sequence[Sequence[float]],
    ) -> list[str]:
        first = chunks[0]
        existing_ids = await self._point_ids(
            source_path=first.source_path,
            version=first.version,
        )
        current_ids = {_chunk_id(chunk) for chunk in chunks}
        await self._qdrant.upsert(
            collection_name=self._collection_name,
            points=[
                models.PointStruct(
                    id=_chunk_id(chunk),
                    vector=list(vector),
                    payload={
                        "document_id": chunk.document_id,
                        "source_title": chunk.source_title,
                        "source_path": chunk.source_path,
                        "version": chunk.version,
                        "chunk_index": chunk.chunk_index,
                        "access_level": chunk.access_level,
                    },
                )
                for chunk, vector in zip(chunks, vectors, strict=True)
            ],
            wait=True,
        )
        return sorted(existing_ids - current_ids)

    async def _activate_database_document(self, chunk: KnowledgeChunk) -> None:
        async with self._session_factory.begin() as session:
            await session.execute(
                update(KnowledgeDocument)
                .where(KnowledgeDocument.source_path == chunk.source_path)
                .values(status="inactive")
            )
            await session.execute(
                update(KnowledgeDocument)
                .where(KnowledgeDocument.id == chunk.document_id)
                .values(status="active")
            )

    async def _cleanup_vector_points(
        self,
        chunk: KnowledgeChunk,
        stale_point_ids: Sequence[str],
    ) -> None:
        if stale_point_ids:
            await self._qdrant.delete(
                collection_name=self._collection_name,
                points_selector=list(stale_point_ids),
                wait=True,
            )
        await self._qdrant.delete(
            collection_name=self._collection_name,
            points_selector=models.Filter(
                must=[
                    models.FieldCondition(
                        key="source_path",
                        match=models.MatchValue(value=chunk.source_path),
                    )
                ],
                must_not=[
                    models.FieldCondition(
                        key="version",
                        match=models.MatchValue(value=chunk.version),
                    )
                ],
            ),
            wait=True,
        )

    async def _point_ids(self, *, source_path: str, version: str) -> set[str]:
        point_ids: set[str] = set()
        offset: Any | None = None
        while True:
            records, offset = await self._qdrant.scroll(
                collection_name=self._collection_name,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="source_path",
                            match=models.MatchValue(value=source_path),
                        ),
                        models.FieldCondition(
                            key="version",
                            match=models.MatchValue(value=version),
                        ),
                    ]
                ),
                limit=256,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            point_ids.update(str(record.id) for record in records)
            if offset is None:
                return point_ids


def parse_frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", text, re.DOTALL)
    if not match:
        return {}
    metadata: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if separator:
            metadata[key.strip()] = value.strip().strip("'\"")
    return metadata


def _resolve_title(text: str, path: Path) -> str:
    heading = re.search(r"(?m)^#\s+(.+?)\s*$", text)
    if heading:
        return heading.group(1).strip()
    if path.suffix.lower() == ".pdf":
        import pymupdf

        with pymupdf.open(path) as document:
            metadata_title = (document.metadata.get("title") or "").strip()
        if metadata_title:
            return metadata_title[:300]
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first_line[:300] or path.stem


def validate_article_structure(text: str, source_name: str) -> None:
    headings = {
        match.group(1).strip()
        for match in re.finditer(r"(?m)^##\s+(.+?)\s*$", text)
    }
    missing = sorted(REQUIRED_ARTICLE_HEADINGS - headings)
    if missing:
        raise ValueError(
            f"knowledge article {source_name} is missing headings: {', '.join(missing)}"
        )


def _validate_database_fields(
    source_path: str,
    source_title: str,
    version: str,
) -> None:
    limits = {
        "source path": (source_path, 500),
        "source title": (source_title, 300),
        "version": (version, 100),
    }
    for field_name, (value, maximum) in limits.items():
        if len(value) > maximum:
            raise ValueError(
                f"knowledge {field_name} exceeds {maximum} characters: "
                f"{source_path}"
            )


def _chunk_id(chunk: KnowledgeChunk) -> str:
    name = f"knowledge-chunk:{chunk.document_id}:{chunk.chunk_index}"
    return str(uuid5(NAMESPACE_URL, name))


def search_text(source_title: str, content: str) -> str:
    return f"{source_title}\n{content}"
