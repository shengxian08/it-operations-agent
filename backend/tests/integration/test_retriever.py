import os
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient, models
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.models import KnowledgeChunk as StoredKnowledgeChunk
from app.db.models import KnowledgeDocument
from app.rag.ingest import DeterministicEmbedder, KnowledgeIngestor
from app.rag.retriever import HybridRetriever, LexicalReranker


@dataclass(frozen=True, slots=True)
class RetrievalContext:
    retriever: HybridRetriever
    ingestor: KnowledgeIngestor
    session_factory: async_sessionmaker[AsyncSession]
    qdrant: AsyncQdrantClient
    collection_name: str
    vpn_path: Path
    printer_path: Path
    support_path: Path
    pdf_path: Path


@pytest_asyncio.fixture
async def retrieval_context(tmp_path: Path) -> AsyncIterator[RetrievalContext]:
    suffix = uuid4().hex
    vpn_path = tmp_path / f"vpn-connection-{suffix}.md"
    printer_path = tmp_path / f"printer-queue-{suffix}.md"
    support_path = tmp_path / f"breakglass-access-{suffix}.md"
    pdf_path = tmp_path / f"vpn-error-codes-{suffix}.pdf"
    vpn_path.write_text(
        _article(
            title="VPN 连接故障处理",
            access_level="employee",
            version=f"test-{suffix}",
            audience="远程办公员工",
            prerequisites="确认互联网连接正常并记录 VPN 错误提示。",
            steps="VPN 连不上时，先检查系统时间，再重启客户端并重新连接。",
            handoff="连续三次失败或出现证书错误时转人工。",
        ),
        encoding="utf-8",
    )
    printer_path.write_text(
        _article(
            title="打印队列清理",
            access_level="employee",
            version=f"test-{suffix}",
            audience="使用楼层打印机的员工",
            prerequisites="确认打印机在线且纸张充足。",
            steps="暂停打印任务，清空队列，然后重新提交测试页。",
            handoff="队列无法删除或设备离线时转人工。",
        ),
        encoding="utf-8",
    )
    support_path.write_text(
        _article(
            title="堡垒机紧急账号流程",
            access_level="support",
            version=f"test-{suffix}",
            audience="IT 支持工程师",
            prerequisites="取得值班负责人批准并记录事件编号。",
            steps="通过堡垒机申请 breakglass root 临时授权，不得共享口令。",
            handoff="审批链缺失或审计异常时立即转安全团队。",
        ),
        encoding="utf-8",
    )
    fixture_pdf = (
        Path(__file__).resolve().parents[1] / "fixtures" / "vpn-error-codes.pdf"
    )
    shutil.copyfile(fixture_pdf, pdf_path)

    database_url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://itops:itops-local-only@localhost:15432/itops",
    )
    qdrant_url = os.getenv("TEST_QDRANT_URL", "http://localhost:16333")
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    qdrant = AsyncQdrantClient(url=qdrant_url)
    collection_name = f"test_knowledge_{suffix}"
    embedder = DeterministicEmbedder(dimensions=96)
    ingestor = KnowledgeIngestor(
        session_factory,
        qdrant,
        embedder,
        collection_name=collection_name,
        max_chars=400,
        overlap_chars=40,
    )
    retriever = HybridRetriever(
        session_factory,
        qdrant,
        embedder,
        LexicalReranker(),
        collection_name=collection_name,
    )
    source_paths = [
        path.name for path in (vpn_path, printer_path, support_path, pdf_path)
    ]

    try:
        summary = await ingestor.ingest_paths(
            [vpn_path, printer_path, support_path, pdf_path]
        )
        assert summary.document_count == 4
        assert summary.chunk_count >= 16
        yield RetrievalContext(
            retriever=retriever,
            ingestor=ingestor,
            session_factory=session_factory,
            qdrant=qdrant,
            collection_name=collection_name,
            vpn_path=vpn_path,
            printer_path=printer_path,
            support_path=support_path,
            pdf_path=pdf_path,
        )
    finally:
        async with session_factory.begin() as session:
            await session.execute(
                delete(KnowledgeDocument).where(
                    KnowledgeDocument.source_path.in_(source_paths)
                )
            )
        if await qdrant.collection_exists(collection_name):
            await qdrant.delete_collection(collection_name)
        await qdrant.close()
        await engine.dispose()


def _article(
    *,
    title: str,
    access_level: str,
    version: str,
    audience: str,
    prerequisites: str,
    steps: str,
    handoff: str,
) -> str:
    return (
        "---\n"
        f'version: "{version}"\n'
        f"access_level: {access_level}\n"
        "---\n\n"
        f"# {title}\n\n"
        f"## 适用对象\n\n{audience}\n\n"
        f"## 前置条件\n\n{prerequisites}\n\n"
        f"## 操作步骤\n\n{steps}\n\n"
        f"## 何时转人工\n\n{handoff}\n"
    )


@pytest.mark.asyncio
async def test_vpn_query_returns_vpn_citation(
    retrieval_context: RetrievalContext,
) -> None:
    hits = await retrieval_context.retriever.retrieve(
        "VPN 连不上如何处理",
        user_access_level="employee",
    )

    assert hits
    assert hits[0].citation.source_path == retrieval_context.vpn_path.name
    assert hits[0].citation.source_title == "VPN 连接故障处理"
    assert hits[0].citation.chunk_index >= 0
    assert hits[0].citation.excerpt
    assert len(hits) <= 5


@pytest.mark.asyncio
async def test_pdf_error_code_enters_same_retrieval_flow(
    retrieval_context: RetrievalContext,
) -> None:
    hits = await retrieval_context.retriever.retrieve(
        "VPN-720 和证书错误如何处理",
        user_access_level="employee",
    )

    assert hits
    assert hits[0].citation.source_path == retrieval_context.pdf_path.name
    assert "VPN-720" in hits[0].citation.excerpt or "证书" in hits[0].citation.excerpt


@pytest.mark.asyncio
async def test_access_filter_hides_support_document_from_employee(
    retrieval_context: RetrievalContext,
) -> None:
    employee_hits = await retrieval_context.retriever.retrieve(
        "堡垒机 breakglass root 临时授权",
        user_access_level="employee",
    )
    support_hits = await retrieval_context.retriever.retrieve(
        "堡垒机 breakglass root 临时授权",
        user_access_level="support",
    )
    title_hits = await retrieval_context.retriever.retrieve(
        "堡垒机紧急账号流程",
        user_access_level="support",
    )

    assert all(
        hit.citation.source_path != retrieval_context.support_path.name
        for hit in employee_hits
    )
    assert support_hits
    assert support_hits[0].citation.source_path == retrieval_context.support_path.name
    assert title_hits[0].citation.source_path == retrieval_context.support_path.name


@pytest.mark.asyncio
async def test_reingest_replaces_chunks_and_qdrant_points_without_duplicates(
    retrieval_context: RetrievalContext,
) -> None:
    first = await retrieval_context.ingestor.ingest_paths(
        [retrieval_context.vpn_path]
    )
    second = await retrieval_context.ingestor.ingest_paths(
        [retrieval_context.vpn_path]
    )

    async with retrieval_context.session_factory() as session:
        document_id = await session.scalar(
            select(KnowledgeDocument.id).where(
                KnowledgeDocument.source_path == retrieval_context.vpn_path.name
            )
        )
        document_count = await session.scalar(
            select(func.count()).select_from(KnowledgeDocument).where(
                KnowledgeDocument.source_path == retrieval_context.vpn_path.name
            )
        )
        chunk_count = await session.scalar(
            select(func.count()).select_from(StoredKnowledgeChunk).where(
                StoredKnowledgeChunk.document_id == document_id
            )
        )
    point_count = await retrieval_context.qdrant.count(
        retrieval_context.collection_name,
        count_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="source_path",
                    match=models.MatchValue(value=retrieval_context.vpn_path.name),
                )
            ]
        ),
        exact=True,
    )

    assert first == second
    assert document_count == 1
    assert chunk_count == 4
    assert point_count.count == 4


@pytest.mark.asyncio
async def test_vector_write_failure_keeps_previous_document_active(
    retrieval_context: RetrievalContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_text = retrieval_context.vpn_path.read_text(encoding="utf-8")
    previous_version = await _active_version(
        retrieval_context.session_factory,
        retrieval_context.vpn_path.name,
    )
    retrieval_context.vpn_path.write_text(
        original_text.replace(
            f'version: "{previous_version}"',
            f'version: "{previous_version}-next"',
        ),
        encoding="utf-8",
    )

    async def fail_upsert(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected Qdrant write failure")

    monkeypatch.setattr(retrieval_context.qdrant, "upsert", fail_upsert)

    with pytest.raises(RuntimeError, match="injected Qdrant write failure"):
        await retrieval_context.ingestor.ingest_paths(
            [retrieval_context.vpn_path]
        )

    assert (
        await _active_version(
            retrieval_context.session_factory,
            retrieval_context.vpn_path.name,
        )
        == previous_version
    )
    point_count = await retrieval_context.qdrant.count(
        retrieval_context.collection_name,
        count_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="source_path",
                    match=models.MatchValue(
                        value=retrieval_context.vpn_path.name
                    ),
                )
            ]
        ),
        exact=True,
    )
    assert point_count.count == 4


@pytest.mark.asyncio
async def test_existing_collection_must_match_embedding_configuration(
    retrieval_context: RetrievalContext,
) -> None:
    mismatched_collection = f"{retrieval_context.collection_name}_mismatch"
    await retrieval_context.qdrant.create_collection(
        mismatched_collection,
        vectors_config=models.VectorParams(
            size=8,
            distance=models.Distance.DOT,
        ),
    )
    ingestor = KnowledgeIngestor(
        retrieval_context.session_factory,
        retrieval_context.qdrant,
        DeterministicEmbedder(dimensions=96),
        collection_name=mismatched_collection,
    )

    try:
        with pytest.raises(ValueError, match="vector size|distance"):
            await ingestor.ingest_paths([retrieval_context.vpn_path])
    finally:
        await retrieval_context.qdrant.delete_collection(mismatched_collection)


@pytest.mark.asyncio
async def test_all_seed_articles_ingest_idempotently(
    tmp_path: Path,
) -> None:
    suffix = uuid4().hex
    source_dir = Path(__file__).resolve().parents[3] / "data" / "knowledge"
    test_dir = tmp_path / "knowledge"
    test_dir.mkdir()
    for source in source_dir.glob("*.md"):
        shutil.copyfile(source, test_dir / f"{suffix}-{source.name}")
    source_paths = [path.name for path in test_dir.glob("*.md")]

    database_url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://itops:itops-local-only@localhost:15432/itops",
    )
    qdrant_url = os.getenv("TEST_QDRANT_URL", "http://localhost:16333")
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    qdrant = AsyncQdrantClient(url=qdrant_url)
    collection_name = f"test_seed_knowledge_{suffix}"
    ingestor = KnowledgeIngestor(
        session_factory,
        qdrant,
        DeterministicEmbedder(dimensions=96),
        collection_name=collection_name,
    )

    try:
        first = await ingestor.ingest_directory(test_dir)
        second = await ingestor.ingest_directory(test_dir)
        async with session_factory() as session:
            document_count = await session.scalar(
                select(func.count())
                .select_from(KnowledgeDocument)
                .where(KnowledgeDocument.source_path.in_(source_paths))
            )
            chunk_count = await session.scalar(
                select(func.count())
                .select_from(StoredKnowledgeChunk)
                .join(
                    KnowledgeDocument,
                    KnowledgeDocument.id == StoredKnowledgeChunk.document_id,
                )
                .where(KnowledgeDocument.source_path.in_(source_paths))
            )
        point_count = await qdrant.count(collection_name, exact=True)

        assert first == second
        assert 30 <= first.document_count <= 50
        assert first.document_count == document_count == len(source_paths)
        assert first.chunk_count == chunk_count == point_count.count
    finally:
        async with session_factory.begin() as session:
            await session.execute(
                delete(KnowledgeDocument).where(
                    KnowledgeDocument.source_path.in_(source_paths)
                )
            )
        if await qdrant.collection_exists(collection_name):
            await qdrant.delete_collection(collection_name)
        await qdrant.close()
        await engine.dispose()


async def _active_version(
    session_factory: async_sessionmaker[AsyncSession],
    source_path: str,
) -> str:
    async with session_factory() as session:
        version = await session.scalar(
            select(KnowledgeDocument.version).where(
                KnowledgeDocument.source_path == source_path,
                KnowledgeDocument.status == "active",
            )
        )
    assert version is not None
    return version
