import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from qdrant_client import AsyncQdrantClient
from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, User
from app.production.knowledge_models import KnowledgeActivePointer, KnowledgeIndexJob, KnowledgeJob, KnowledgeRevision, KnowledgeSnapshot, KnowledgeSource, RevisionChunk
from app.production.business_models import BusinessAudit
from app.production.identity_models import IdentityAccount


@pytest.mark.asyncio
async def test_legacy_pdf_is_preserved_but_not_laundered_into_new_index(knowledge_context, tmp_path):
    import hashlib
    from app.production.knowledge import documents
    from app.production.identity import Principal
    from tests.pdf_samples import table_pdf
    c = knowledge_context
    c.settings.knowledge_max_upload_bytes = 100_000
    path = table_pdf(tmp_path / "legacy-a.pdf")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    async with c.factory.begin() as session:
        for filename in ["legacy-a.pdf", "legacy-b.pdf"]:
            session.add(KnowledgeSource(id=filename, title=filename, source_path=filename, content_hash=digest,
                content="服务等级 响应时间 普通 紧急 240 15", access_level="employee", status="active", version=1))
    job = await c.service.enqueue_upload(c.owner, "new.md", "新指南", "employee", "# 新指南\n\n已核对的新正文".encode())
    await c.service.process_job(job["id"])
    await c.service.job_detail(job["id"])
    first = await c.service.execute_publish(c.owner, job["id"])
    assert set(first.get("excluded_documents", [])) == {"legacy-a.pdf", "legacy-b.pdf"}
    async with c.factory() as session:
        snapshots = list(await session.scalars(select(KnowledgeSnapshot).where(KnowledgeSnapshot.revision_id == first["revision_id"])))
        assert [snapshot.source_path for snapshot in snapshots] == ["new.md"]
        legacy = await session.get(KnowledgeSource, "legacy-a.pdf")
        assert legacy.content == "服务等级 响应时间 普通 紧急 240 15" and legacy.status == "active"
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(knowledge_service=c.service)))
    listing = await documents(request, principal=Principal(c.owner, "Admin", "admin"))
    assert all(row["requires_reparse"] for row in listing["documents"] if row["id"].startswith("legacy"))
    # Several old PDFs can be upgraded one at a time without blocking publication.
    replacement = await c.service.enqueue_upload(c.owner, path.name, "重解析规范", "employee", path.read_bytes())
    await c.service.process_job(replacement["id"])
    await c.service.job_detail(replacement["id"])
    second = await c.service.execute_publish(c.owner, replacement["id"])
    assert second["excluded_documents"] == ["legacy-b.pdf"]
    async with c.factory() as session:
        paths = set(await session.scalars(select(KnowledgeSnapshot.source_path).where(KnowledgeSnapshot.revision_id == second["revision_id"])))
        assert paths == {"legacy-a.pdf", "new.md"}


@pytest.mark.asyncio
async def test_import_same_pdf_bytes_requeues_legacy_flattened_source(knowledge_context, tmp_path):
    import hashlib
    import importlib.util
    from pathlib import Path
    from tests.pdf_samples import table_pdf
    c = knowledge_context
    c.settings.knowledge_max_upload_bytes = 100_000
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    path = table_pdf(corpus / "legacy.pdf")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    async with c.factory.begin() as session:
        session.add(KnowledgeSource(id="legacy", title="旧资料", source_path=path.name, content_hash=digest,
            content="旧版平铺正文", access_level="employee", status="active", version=1))
    spec = importlib.util.spec_from_file_location("pdf_upgrade_import", Path(__file__).resolve().parents[3] / "scripts" / "ingest_knowledge.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    args = cli.build_parser().parse_args(["--directory", str(corpus), "--actor-id", c.owner, "--dry-run"])
    preview = await cli.prepare_production(args, c.settings, c.factory, c.service)
    assert preview[0]["status"] == "would_enqueue"
    args.dry_run = False
    prepared = await cli.prepare_production(args, c.settings, c.factory, c.service)
    assert prepared[0]["status"] == "queued"
    await c.service.process_job(prepared[0]["job_id"])
    await c.service.job_detail(prepared[0]["job_id"])
    await c.service.execute_publish(c.owner, prepared[0]["job_id"])
    assert (await cli.prepare_production(args, c.settings, c.factory, c.service))[0]["status"] == "unchanged"


@pytest.mark.asyncio
async def test_cited_source_opens_its_snapshot_and_still_checks_current_permissions(knowledge_context):
    from app.production.knowledge import document_detail
    from app.production.identity import Principal
    from fastapi import HTTPException
    c = knowledge_context
    job = await c.service.enqueue_upload(c.owner, "response.md", "响应规范", "employee", "# 响应规范\n\n紧急响应15分钟。".encode())
    await c.service.process_job(job["id"])
    await c.service.job_detail(job["id"])
    first = await c.service.execute_publish(c.owner, job["id"])
    document_id = (await c.service.job_detail(job["id"]))["document_id"]
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(knowledge_service=c.service)))
    employee = Principal("test-employee", "Employee", "employee")
    async with c.factory.begin() as session:
        source = await session.get(KnowledgeSource, document_id)
        source.content = "紧急响应60分钟。"
    import inspect
    assert "index_revision" in inspect.signature(document_detail).parameters, "must resolve cited snapshot rather than latest source"
    original = await document_detail(document_id, request, employee, index_revision=first["revision_id"])
    assert "15分钟" in original["content"] and "60分钟" not in original["content"]
    with pytest.raises(HTTPException) as missing:
        await document_detail(document_id, request, employee, index_revision="missing-revision")
    assert missing.value.status_code == 404
    async with c.factory.begin() as session:
        source = await session.get(KnowledgeSource, document_id)
        source.access_level = "admin"
    with pytest.raises(HTTPException) as denied:
        await document_detail(document_id, request, employee, index_revision=first["revision_id"])
    assert denied.value.status_code == 404


@pytest.mark.asyncio
async def test_pdf_table_upload_index_answer_and_citation_keep_cell_relationships(knowledge_context, tmp_path):
    import json
    import time
    from dataclasses import asdict
    from pathlib import Path
    from app.agent.graph import GraphDependencies, build_graph
    from app.llm.providers import ChatResult
    from app.production.knowledge import build_retriever
    from tests.pdf_samples import CONTEXT, HEADERS, ROWS, table_pdf
    c = knowledge_context
    path = table_pdf(tmp_path / "response.pdf")
    c.settings.knowledge_max_upload_bytes = 100_000
    started = time.perf_counter()
    upload = await c.service.enqueue_upload(c.owner, path.name, "服务响应规范", "employee", path.read_bytes())
    await c.service.process_job(upload["id"])
    parse_seconds = time.perf_counter() - started
    preview = await c.service.job_detail(upload["id"])
    assert preview["status"] == "ready", preview["error"]
    assert preview["sections"][0]["tables"][0]["headers"] == HEADERS
    assert preview["sections"][0]["tables"][0]["rows"] == ROWS
    published = await c.service.execute_publish(c.owner, upload["id"])
    revision = published["revision_id"]
    preview = await c.service.job_detail(upload["id"])
    async with c.factory() as session:
        rows = list(await session.scalars(select(RevisionChunk).where(RevisionChunk.revision_id == revision)))
        stored_revision = await session.get(KnowledgeRevision, revision)
    vector_count = (await c.qdrant.count(collection_name=stored_revision.collection_name, exact=True)).count
    assert vector_count == len(rows) == 2
    points, _ = await c.qdrant.scroll(collection_name=stored_revision.collection_name, limit=100,
                                    with_payload=True, with_vectors=False)
    assert {point.payload["document_id"] for point in points} == {preview["document_id"]}
    query = "工作日紧急业务中断问题响应时间多少分钟"
    retriever = await build_retriever(c.factory, c.qdrant, c.settings, revision)
    hits = await retriever.retrieve(query, user_access_level="employee")
    assert hits and "服务等级：紧急" in hits[0].citation.excerpt
    assert "响应时间（分钟）：15" in hits[0].citation.excerpt
    assert CONTEXT in hits[0].citation.excerpt

    class EvidenceReader:
        """Controlled model: derives the answer from supplied cells, no API call."""
        prompt = ""
        async def complete(self, prompt):
            import re
            self.prompt = prompt
            data = json.loads(prompt.split("<knowledge_evidence>\n", 1)[1].split("\n</knowledge_evidence>", 1)[0])
            selected = next(item for item in data if "服务等级：紧急" in item["excerpt"])
            value = re.search(r"响应时间（分钟）：(\d+)", selected["excerpt"]).group(1)
            number = selected["citation"]
            return ChatResult(json.dumps({"answer": f"工作日紧急问题响应时间是{value}分钟，响应不等于解决。[{number}]",
                                          "citation_ids": [number]}, ensure_ascii=False), 100, 20, "controlled-test-model")
    provider = EvidenceReader()
    graph = build_graph(GraphDependencies(retriever, provider, SimpleNamespace(), require_structured_citations=True))
    answer = await graph.ainvoke({"user_id": "employee", "conversation_id": "test", "message": query, "step_count": 0})
    assert answer["final_state"] == "answered"
    assert "15分钟" in answer["answer"] and "240" not in answer["answer"]
    assert "响应不等于解决" in answer["answer"]
    citation = answer["citations"][0]
    assert (citation.page_number, citation.table_id, citation.row_index, citation.index_revision) == (1, "p1-t1", 2, revision)
    assert citation.document_id == preview["document_id"]

    evidence_directory = os.getenv("PDF_EVIDENCE_DIRECTORY")
    if evidence_directory:
        import pymupdf
        directory = Path(evidence_directory)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "synthetic-response.pdf").write_bytes(path.read_bytes())
        with pymupdf.open(path) as document:
            before = "\n\n".join(page.get_text() for page in document)
            document[0].get_pixmap(matrix=pymupdf.Matrix(1.25, 1.25)).save(directory / "synthetic-response-page.png")
        report = {"sample_type": "synthetic_not_customer_incident", "model_mode": "controlled_mock",
                  "embedding_mode": "deterministic", "dependencies": "real_isolated_PostgreSQL_and_Qdrant",
                  "raw_page_text_before": before, "preview": preview,
                  "chunks": [{"id": row.id, "content": row.content, "char_start": row.char_start, "char_end": row.char_end} for row in rows],
                  "vector_count": vector_count, "postgres_chunk_count": len(rows),
                  "query": query, "hits": [asdict(hit) for hit in hits], "model_context": provider.prompt,
                  "answer": answer["answer"], "citations": [asdict(item) for item in answer["citations"]],
                  "upload_and_subprocess_parse_seconds": parse_seconds,
                  "total_acceptance_seconds": time.perf_counter() - started,
                  "real_pdf_quality_verified": False, "real_model_quality_verified": False}
        (directory / "chain-evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    await c.service.execute_deactivate(c.owner, preview["document_id"])
    assert await retriever.retrieve(query, user_access_level="employee") == []


@pytest.mark.asyncio
async def test_unreliable_pdf_parse_is_failed_and_cannot_publish(knowledge_context, tmp_path):
    from fastapi import HTTPException
    from tests.pdf_samples import scan_pdf
    c = knowledge_context
    c.settings.knowledge_max_upload_bytes = 100_000
    path = scan_pdf(tmp_path / "scan-with-footer.pdf")
    upload = await c.service.enqueue_upload(c.owner, path.name, "扫描文件", "employee", path.read_bytes())
    await c.service.process_job(upload["id"])
    preview = await c.service.job_detail(upload["id"])
    assert preview["status"] == "failed" and "扫描" in preview["error"]
    with pytest.raises(HTTPException) as failed:
        await c.service.execute_publish(c.owner, upload["id"])
    assert failed.value.status_code == 409
    assert await c.service.active_revision_id() is None


@pytest_asyncio.fixture
async def knowledge_context(tmp_path):
    url, qurl = os.getenv("TEST_DATABASE_URL"), os.getenv("TEST_QDRANT_URL")
    if not url or not qurl:
        pytest.skip("explicit isolated PG and Qdrant URLs required")
    from app.production.knowledge import KnowledgeService
    schema = "knowledge_test_" + uuid4().hex
    bootstrap = create_async_engine(url)
    async with bootstrap.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    qdrant = AsyncQdrantClient(url=qurl)
    tables = [User.__table__, IdentityAccount.__table__, BusinessAudit.__table__, KnowledgeIndexJob.__table__, KnowledgeSource.__table__, KnowledgeJob.__table__, KnowledgeRevision.__table__, KnowledgeActivePointer.__table__, KnowledgeSnapshot.__table__, RevisionChunk.__table__]
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    owner = "knowledge-test-" + uuid4().hex
    collection = "test_knowledge_" + uuid4().hex
    settings = SimpleNamespace(session_secret="test", knowledge_storage_path=str(tmp_path), knowledge_max_upload_bytes=4096,
        knowledge_max_pdf_pages=10, knowledge_parse_timeout_seconds=5, lease_seconds=30, embedding_mode="hash",
        embedding_hash_dim=32, embedding_batch_size=16, embedding_model="hash", embedding_revision="v1", qdrant_collection=collection)
    async with factory.begin() as session:
        session.add(User(id=owner, display_name="Admin", access_level="admin"))
        await session.flush()
        session.add(IdentityAccount(user_id=owner, issuer="https://identity.test", subject=owner))
    service = KnowledgeService(factory, qdrant, settings)
    context = SimpleNamespace(factory=factory, qdrant=qdrant, settings=settings, service=service, owner=owner)
    try:
        yield context
    finally:
        await engine.dispose()
        async with bootstrap.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await bootstrap.dispose()
        for item in (await qdrant.get_collections()).collections:
            if item.name.startswith(collection):
                await qdrant.delete_collection(item.name)
        await qdrant.close()


@pytest.mark.asyncio
async def test_upload_requires_worker_preview_then_publish_deactivate_and_rollback(knowledge_context):
    from app.production.knowledge import build_retriever
    c = knowledge_context
    data = "# VPN指南\n\n## 适用对象\n员工\n\n## 操作步骤\nVPN错误619先重启客户端。\n".encode()
    job = await c.service.enqueue_upload(c.owner, "vpn.md", "VPN指南", "employee", data)
    assert job["status"] == "queued"
    assert await c.service.active_revision_id() is None
    with pytest.raises(Exception) as not_ready:
        await c.service.execute_publish(c.owner, job["id"])
    assert not_ready.value.status_code == 409
    assert await c.service.process_next_job()
    preview = await c.service.job_detail(job["id"])
    assert preview["status"] == "ready" and preview["sections"]
    published = await c.service.execute_publish(c.owner, job["id"])
    first_revision = published["revision_id"]
    retriever = await build_retriever(c.factory, c.qdrant, c.settings, first_revision)
    hits = await retriever.retrieve("VPN错误619", user_access_level="employee")
    assert hits and "619" in hits[0].citation.excerpt
    detail = await c.service.job_detail(job["id"])
    await c.service.execute_deactivate(c.owner, detail["document_id"])
    assert await retriever.retrieve("VPN错误619", user_access_level="employee") == []
    await c.service.activate_revision(c.owner, first_revision)
    assert await c.service.active_revision_id() == first_revision
    assert await retriever.retrieve("VPN错误619", user_access_level="employee")


@pytest.mark.asyncio
async def test_stable_snapshot_rechecks_current_access_and_empty_revision(knowledge_context):
    from app.production.knowledge import build_retriever
    c = knowledge_context
    job = await c.service.enqueue_upload(c.owner, "secret.md", "账户指南", "employee", "# 账户\n\n恢复账户请联系支持。".encode())
    await c.service.process_next_job()
    await c.service.job_detail(job["id"])
    published = await c.service.execute_publish(c.owner, job["id"])
    retriever = await build_retriever(c.factory, c.qdrant, c.settings, published["revision_id"])
    assert await retriever.retrieve("恢复账户", user_access_level="employee")
    detail = await c.service.job_detail(job["id"])
    async with c.factory.begin() as session:
        source = await session.get(KnowledgeSource, detail["document_id"])
        source.access_level = "admin"
    assert await retriever.retrieve("恢复账户", user_access_level="employee") == []
    empty = await build_retriever(c.factory, c.qdrant, c.settings, "")
    assert await empty.retrieve("恢复账户", user_access_level="admin") == []


@pytest.mark.asyncio
async def test_semantic_factory_publishes_512_dimensions_and_rejects_mismatch(knowledge_context, monkeypatch):
    import app.production.knowledge as module
    from app.rag.ingest import DeterministicEmbedder
    c = knowledge_context
    c.settings.embedding_mode = "sentence-transformer"
    c.settings.embedding_model = "BAAI/bge-small-zh-v1.5"
    c.settings.embedding_revision = "7999e1d3359715c523056ef9478215996d62a620"
    c.settings.embedding_dimensions = 512
    class FakeSemantic512(DeterministicEmbedder):
        def __init__(self, model, revision):
            assert model == c.settings.embedding_model and revision == c.settings.embedding_revision
            super().__init__(dimensions=512)
    monkeypatch.setattr(module, "SentenceTransformerEmbedder", FakeSemantic512)
    monkeypatch.setattr(module, "_embedder_cache", {})
    first = await c.service.enqueue_upload(c.owner, "semantic.md", "语义", "employee", "# VPN\n\n先重启VPN客户端。".encode())
    await c.service.process_job(first["id"])
    await c.service.job_detail(first["id"])
    result = await c.service.execute_publish(c.owner, first["id"])
    async with c.factory() as session:
        revision = await session.get(KnowledgeRevision, result["revision_id"])
        assert revision.dimensions == 512
        assert revision.pipeline_config["query_instruction"] == "为这个句子生成表示以用于检索相关文章："
        vectors = (await c.qdrant.get_collection(revision.collection_name)).config.params.vectors
        assert vectors.size == 512
    assert (await c.service.readiness())["ready"]
    second = await c.service.enqueue_upload(c.owner, "second.md", "第二文档", "employee", "# VPN\n\n再检查VPN证书。".encode())
    await c.service.process_job(second["id"])
    await c.service.job_detail(second["id"])
    c.settings.embedding_dimensions = 384
    with pytest.raises(ValueError, match="dimensions"):
        await c.service.execute_publish(c.owner, second["id"])
    assert await c.service.active_revision_id() == result["revision_id"]


@pytest.mark.asyncio
async def test_production_cli_is_idempotent_prepare_and_same_name_updates_snapshot(knowledge_context, tmp_path):
    import importlib.util
    from pathlib import Path
    c = knowledge_context
    spec = importlib.util.spec_from_file_location("safe_production_import", Path(__file__).resolve().parents[3] / "scripts" / "ingest_knowledge.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    path = corpus / "vpn.md"
    path.write_text("# VPN\n\n先重启VPN客户端。", encoding="utf-8")
    args = cli.build_parser().parse_args(["--directory", str(corpus), "--actor-id", c.owner])
    first = await cli.prepare_production(args, c.settings, c.factory, c.service)
    repeated = await cli.prepare_production(args, c.settings, c.factory, c.service)
    assert repeated[0]["job_id"] == first[0]["job_id"]
    await c.service.process_job(first[0]["job_id"])
    await c.service.job_detail(first[0]["job_id"])
    published = await c.service.execute_publish(c.owner, first[0]["job_id"])
    assert (await cli.prepare_production(args, c.settings, c.factory, c.service))[0]["status"] == "unchanged"
    path.write_text("# VPN\n\n更新后先检查VPN证书。", encoding="utf-8")
    updated = await cli.prepare_production(args, c.settings, c.factory, c.service)
    await c.service.process_job(updated[0]["job_id"])
    await c.service.job_detail(updated[0]["job_id"])
    await c.service.execute_publish(c.owner, updated[0]["job_id"])
    async with c.factory() as session:
        active = list(await session.scalars(select(KnowledgeSource).where(KnowledgeSource.status == "active")))
        assert len(active) == 1 and active[0].version == 2
        old = await session.scalar(select(KnowledgeSnapshot).where(KnowledgeSnapshot.revision_id == published["revision_id"]))
        assert "重启" in old.content and "更新" not in old.content


@pytest.mark.asyncio
async def test_publish_requires_a_preview_read(knowledge_context):
    from fastapi import HTTPException
    c = knowledge_context
    job = await c.service.enqueue_upload(c.owner, "vpn.md", "VPN指南", "employee", "# VPN\n\n步骤：重启VPN。".encode())
    await c.service.process_job(job["id"])
    with pytest.raises(HTTPException) as unseen:
        await c.service.execute_publish(c.owner, job["id"])
    assert unseen.value.status_code == 409
    await c.service.job_detail(job["id"])
    assert (await c.service.execute_publish(c.owner, job["id"]))["revision_id"]


@pytest.mark.asyncio
async def test_upload_limits_and_failed_parse_are_recorded(knowledge_context):
    from fastapi import HTTPException
    c = knowledge_context
    with pytest.raises(HTTPException) as oversize:
        await c.service.enqueue_upload(c.owner, "large.md", "large", "employee", b"x" * 4097)
    assert oversize.value.status_code == 413
    with pytest.raises(HTTPException) as unsupported:
        await c.service.enqueue_upload(c.owner, "file.exe", "bad", "employee", b"x")
    assert unsupported.value.status_code == 415
    invalid = await c.service.enqueue_upload(c.owner, "invalid.md", "invalid", "employee", b"\xff\xfe")
    await c.service.process_job(invalid["id"])
    detail = await c.service.job_detail(invalid["id"])
    assert detail["status"] == "failed" and detail["error"]


@pytest.mark.asyncio
async def test_qdrant_failure_does_not_publish_partial_revision(knowledge_context):
    c = knowledge_context
    job = await c.service.enqueue_upload(c.owner, "vpn.md", "VPN指南", "employee", "# VPN\n\n步骤：重启VPN。".encode())
    await c.service.process_job(job["id"])
    await c.service.job_detail(job["id"])
    class FailingVectors:
        async def create_collection(self, **kwargs):
            raise RuntimeError("injected vector failure")
        async def delete_collection(self, **kwargs):
            pass
    original = c.service.qdrant
    c.service.qdrant = FailingVectors()
    try:
        with pytest.raises(RuntimeError, match="injected vector"):
            await c.service.execute_publish(c.owner, job["id"])
    finally:
        c.service.qdrant = original
    assert await c.service.active_revision_id() is None
    detail = await c.service.job_detail(job["id"])
    assert detail["status"] == "ready" and detail["document_id"] is None
    assert (await c.service.execute_publish(c.owner, job["id"]))["revision_id"]


@pytest.mark.asyncio
async def test_pdf_page_limit_and_parse_timeout_are_terminal_failures(knowledge_context):
    import pymupdf
    c = knowledge_context
    pdf = pymupdf.open()
    for _ in range(11):
        pdf.new_page()
    data = pdf.tobytes()
    pdf.close()
    oversized_pdf = await c.service.enqueue_upload(c.owner, "too-many-pages.pdf", "Pages", "employee", data)
    await c.service.process_job(oversized_pdf["id"])
    failed = await c.service.job_detail(oversized_pdf["id"])
    assert failed["status"] == "failed" and "page limit" in failed["error"]
    c.settings.knowledge_parse_timeout_seconds = 0.000001
    timed = await c.service.enqueue_upload(c.owner, "slow.md", "Timeout", "employee", "# 文档\n\n解析超时。".encode())
    await c.service.process_job(timed["id"])
    failure = await c.service.job_detail(timed["id"])
    assert failure["status"] == "failed" and "timed out" in failure["error"]


@pytest.mark.asyncio
async def test_worker_rejects_tampered_immutable_upload(knowledge_context):
    from pathlib import Path
    c = knowledge_context
    job = await c.service.enqueue_upload(c.owner, "vpn.md", "VPN", "employee", "# VPN\n\n重启客户端。".encode())
    async with c.factory() as session:
        record = await session.get(KnowledgeJob, job["id"])
        Path(record.source_path).write_bytes(b"tampered")
    await c.service.process_job(job["id"])
    failed = await c.service.job_detail(job["id"])
    assert failed["status"] == "failed" and "integrity" in failed["error"]


@pytest.mark.asyncio
async def test_every_new_revision_is_a_complete_snapshot_and_old_runs_remain_pinned(knowledge_context):
    from app.production.knowledge import build_retriever
    c = knowledge_context
    revisions, document_ids = [], []
    for filename, title, content in [("vpn.md", "VPN指南", "# VPN\n\nVPN故障先重启客户端。"), ("email.md", "邮箱指南", "# 邮箱\n\n邮箱登录失败请检查密码。")]:
        job = await c.service.enqueue_upload(c.owner, filename, title, "employee", content.encode())
        await c.service.process_job(job["id"])
        await c.service.job_detail(job["id"])
        revisions.append((await c.service.execute_publish(c.owner, job["id"]))["revision_id"])
        document_ids.append((await c.service.job_detail(job["id"]))["document_id"])
    async with c.factory() as session:
        old_ids = set(await session.scalars(select(KnowledgeSnapshot.document_id).where(KnowledgeSnapshot.revision_id == revisions[0])))
        new_ids = set(await session.scalars(select(KnowledgeSnapshot.document_id).where(KnowledgeSnapshot.revision_id == revisions[1])))
        assert document_ids[0] in old_ids and document_ids[1] not in old_ids
        assert set(document_ids).issubset(new_ids)
        assert old_ids.issubset(new_ids)
    pinned = await build_retriever(c.factory, c.qdrant, c.settings, revisions[0])
    current = await build_retriever(c.factory, c.qdrant, c.settings)
    old_hits = await pinned.retrieve("邮箱登录失败", user_access_level="employee")
    current_hits = await current.retrieve("邮箱登录失败", user_access_level="employee")
    assert all(hit.citation.document_id != document_ids[1] for hit in old_hits)
    assert current_hits[0].citation.document_id == document_ids[1]
    assert (await c.service.readiness())["ready"]


@pytest.mark.asyncio
async def test_knowledge_admin_api_upload_preview_publish_and_rollback(knowledge_context):
    import json
    import httpx
    from fastapi import FastAPI
    from redis.asyncio import Redis
    from app.production.knowledge import router, admin_router
    from app.production.identity import IdentityService
    from app.production.identity_models import IdentityAccount
    c = knowledge_context
    redis_url = os.getenv("TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("explicit isolated Redis required")
    redis = Redis.from_url(redis_url)
    config = SimpleNamespace(session_secret="integration-secret", oidc_http_timeout_seconds=5,
        session_ttl_seconds=3600, session_cookie_name="it_assistant_session", public_base_url="https://assistant.test",
        oidc_allowed_roles=("employee", "support", "admin"))
    identity = IdentityService(config, redis, c.factory)
    async with c.factory.kw["bind"].begin() as connection:
        await connection.run_sync(lambda sync: IdentityAccount.__table__.create(sync, checkfirst=True))
    sid = "knowledge-test-" + uuid4().hex
    await redis.set(identity._key("session", sid), json.dumps({"user_id": c.owner, "csrf_token": "test-csrf"}), ex=3600)
    app = FastAPI()
    app.state.identity_service, app.state.knowledge_service = identity, c.service
    app.include_router(router)
    app.include_router(admin_router)
    headers = {"Origin": "https://assistant.test", "X-CSRF-Token": "test-csrf"}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="https://assistant.test") as client:
            assert (await client.get("/api/v1/knowledge/jobs")).status_code == 401
            client.cookies.set(config.session_cookie_name, sid)
            upload = await client.post("/api/v1/knowledge/uploads", data={"title": "VPN指南", "access_level": "employee"},
                files={"file": ("vpn.md", "# VPN\n\n重启VPN客户端。".encode(), "text/markdown")}, headers=headers)
            assert upload.status_code == 202
            job = upload.json()
            await c.service.process_job(job["id"])
            preview = await client.get(f"/api/v1/knowledge/jobs/{job['id']}")
            assert preview.json()["status"] == "ready" and preview.json()["sections"]
            published = await client.post(f"/api/v1/knowledge/jobs/{job['id']}/publish", headers=headers)
            assert published.status_code == 202 and published.json()["status"] == "queued"
            index_job_id = published.json()["index_job_id"]
            await c.service.process_next_job()
            index_job = (await client.get(f"/api/v1/admin/knowledge/index-jobs/{index_job_id}")).json()
            assert index_job["status"] == "completed"
            revision = index_job["result"]["revision_id"]
            detail = (await client.get(f"/api/v1/knowledge/jobs/{job['id']}")).json()
            document_id = detail["document_id"]
            article = await client.get(f"/api/v1/knowledge/documents/{document_id}")
            assert article.json()["sections"] and article.json()["title"] == "VPN指南"
            assert (await client.post(f"/api/v1/knowledge/documents/{document_id}/deactivate", headers=headers)).status_code == 202
            await c.service.process_next_job()
            activated = await client.post(f"/api/v1/knowledge/revisions/{revision}/activate", headers=headers)
            assert activated.status_code == 200 and activated.json()["revision_id"] == revision
            revisions = (await client.get("/api/v1/knowledge/revisions")).json()["revisions"]
            assert any(item["id"] == revision and item["active"] for item in revisions)
    finally:
        await redis.delete(identity._key("session", sid))
        await identity.close()
        await redis.aclose()


@pytest.mark.asyncio
async def test_index_queue_is_idempotent_and_recovers_worker_restart(knowledge_context, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.production.knowledge import KnowledgeService
    c = knowledge_context
    upload = await c.service.enqueue_upload(c.owner, "vpn.md", "VPN", "employee", "# VPN\n\n重启VPN客户端。".encode())
    await c.service.process_job(upload["id"])
    await c.service.job_detail(upload["id"])
    def no_api_model_load(*args):
        raise AssertionError("API attempted to load embedding weights")
    with monkeypatch.context() as patch:
        patch.setattr("app.production.knowledge.build_embedder", no_api_model_load)
        first = await c.service.enqueue_publish(c.owner, upload["id"])
        replay = await c.service.enqueue_publish(c.owner, upload["id"])
    assert first["id"] == replay["id"] and first["status"] == "queued"
    assert await c.service.active_revision_id() is None
    claimed = await c.service._claim_index_job()
    async with c.factory.begin() as session:
        job = await session.get(KnowledgeIndexJob, claimed["id"])
        job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    restarted = KnowledgeService(c.factory, c.qdrant, c.settings)
    assert await restarted.process_next_job()
    completed = await restarted.index_job_detail(first["id"])
    assert completed["status"] == "completed" and completed["result"]["revision_id"]
    assert (await restarted.enqueue_publish(c.owner, upload["id"]))["id"] == first["id"]
    document_id = (await restarted.job_detail(upload["id"]))["document_id"]
    deactivate = await restarted.enqueue_deactivate(c.owner, document_id)
    assert (await restarted.enqueue_deactivate(c.owner, document_id))["id"] == deactivate["id"]
    await restarted.process_next_job()
    assert (await restarted.enqueue_deactivate(c.owner, document_id))["id"] == deactivate["id"]


@pytest.mark.asyncio
async def test_index_failure_is_durable_and_retry_keeps_active_revision_safe(knowledge_context):
    c = knowledge_context
    upload = await c.service.enqueue_upload(c.owner, "vpn.md", "VPN", "employee", "# VPN\n\n重启客户端。".encode())
    await c.service.process_job(upload["id"])
    await c.service.job_detail(upload["id"])
    task = await c.service.enqueue_publish(c.owner, upload["id"])
    class UnavailableVectors:
        async def create_collection(self, **kwargs):
            raise RuntimeError("private provider detail that must not be returned")
        async def delete_collection(self, **kwargs):
            pass
    original = c.service.qdrant
    c.service.qdrant = UnavailableVectors()
    try:
        await c.service.process_next_job()
    finally:
        c.service.qdrant = original
    failed = await c.service.index_job_detail(task["id"])
    assert failed["status"] == "failed" and failed["error"] == "index_build_failed"
    assert await c.service.active_revision_id() is None
    retried = await c.service.enqueue_publish(c.owner, upload["id"])
    assert retried["id"] == task["id"] and retried["status"] == "queued"
    await c.service.process_next_job()
    assert (await c.service.index_job_detail(task["id"]))["status"] == "completed"


@pytest.mark.asyncio
async def test_expired_worker_cannot_switch_pointer_after_finishing_qdrant_write(knowledge_context, monkeypatch):
    from datetime import datetime, timedelta, timezone
    c = knowledge_context
    upload = await c.service.enqueue_upload(c.owner, "vpn.md", "VPN", "employee", "# VPN\n\n重启客户端。".encode())
    await c.service.process_job(upload["id"])
    await c.service.job_detail(upload["id"])
    task = await c.service.enqueue_publish(c.owner, upload["id"])
    claimed = await c.service._claim_index_job()
    original = c.service._create_revision
    async def superseded_after_vectors(*args, **kwargs):
        revision = await original(*args, **kwargs)
        async with c.factory.begin() as session:
            job = await session.get(KnowledgeIndexJob, task["id"])
            job.lease_token = "replacement-worker"
            job.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=30)
        return revision
    with monkeypatch.context() as patch:
        patch.setattr(c.service, "_create_revision", superseded_after_vectors)
        with pytest.raises(PermissionError, match="lease"):
            await c.service.execute_publish(c.owner, upload["id"], index_job_id=task["id"], lease_token=claimed["lease_token"])
    assert await c.service.active_revision_id() is None
    assert (await c.service.job_detail(upload["id"]))["status"] == "ready"
    async with c.factory() as session:
        job = await session.get(KnowledgeIndexJob, task["id"])
        assert len(job.staging_collections) == 1
        for collection in job.staging_collections:
            assert not await c.qdrant.collection_exists(collection)


@pytest.mark.asyncio
async def test_revision_pipeline_and_qdrant_dimensions_are_checked_without_loading_weights(knowledge_context):
    from app.production.knowledge import build_retriever
    from qdrant_client import models
    c = knowledge_context
    upload = await c.service.enqueue_upload(c.owner, "vpn.md", "VPN", "employee", "# VPN\n\n重启客户端。".encode())
    await c.service.process_job(upload["id"])
    await c.service.job_detail(upload["id"])
    revision = (await c.service.execute_publish(c.owner, upload["id"]))["revision_id"]
    c.settings.minimum_evidence_score = 0.7
    with pytest.raises(ValueError, match="model"):
        await build_retriever(c.factory, c.qdrant, c.settings, revision)
    c.settings.minimum_evidence_score = 0.35
    document = (await c.service.job_detail(upload["id"]))["document_id"]
    empty_revision = (await c.service.execute_deactivate(c.owner, document))["revision_id"]
    async with c.factory() as session:
        record = await session.get(KnowledgeRevision, empty_revision)
        assert record.chunk_count == 0
        collection = record.collection_name
    await c.qdrant.delete_collection(collection)
    await c.qdrant.create_collection(collection_name=collection, vectors_config=models.VectorParams(size=16, distance=models.Distance.COSINE))
    assert not (await c.service.readiness())["ready"]


@pytest.mark.asyncio
async def test_disabled_or_demoted_index_actor_is_rechecked_before_claim_and_commit(knowledge_context, monkeypatch):
    c = knowledge_context
    upload = await c.service.enqueue_upload(c.owner, "vpn.md", "VPN", "employee", "# VPN\n\n重启客户端。".encode())
    await c.service.process_job(upload["id"])
    await c.service.job_detail(upload["id"])
    task = await c.service.enqueue_publish(c.owner, upload["id"])
    async with c.factory.begin() as session:
        user = await session.get(User, c.owner)
        user.access_level = "employee"
    await c.service.process_next_job()
    rejected = await c.service.index_job_detail(task["id"])
    assert rejected["status"] == "failed" and rejected["error"] == "actor_access_changed"
    assert await c.service.active_revision_id() is None
    async with c.factory.begin() as session:
        user = await session.get(User, c.owner)
        user.access_level = "admin"
    await c.service.enqueue_publish(c.owner, upload["id"])
    original = c.service._create_revision
    async def disabled_after_build(*args, **kwargs):
        revision = await original(*args, **kwargs)
        async with c.factory.begin() as session:
            account = await session.scalar(select(IdentityAccount).where(IdentityAccount.user_id == c.owner))
            account.enabled = False
        return revision
    monkeypatch.setattr(c.service, "_create_revision", disabled_after_build)
    await c.service.process_next_job()
    rejected = await c.service.index_job_detail(task["id"])
    assert rejected["status"] == "failed" and rejected["error"] == "actor_access_changed"
    assert await c.service.active_revision_id() is None
