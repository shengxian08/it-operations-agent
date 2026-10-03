# PDF Evidence Implementation Plan

> For agentic workers: execute inline with systematic-debugging, test-driven-development and verification-before-completion. Preserve all existing uncommitted work; review the final scoped diff independently.

**Goal:** 让已声明支持的 PDF 表格在解析、分块、召回、回答和引用之间保留可核查证据。

**Architecture:** 共享纯解析模块将真实表格单元格转换为有页/表/行来源的原子文本。现有任务 JSONB 保存结构预览，现有不可变版本索引继续使用 PG + Qdrant，检索保留完整 PDF 证据。

**Tech Stack:** Python、PyMuPDF、pytest、FastAPI、PostgreSQL、Qdrant、React/TypeScript。

Spec: ../specs/2026-10-02-pdf-evidence-design.md

## Task 1: Reproduction and source structure

- [x] Add tests/pdf_samples.py and unit/test_pdf_structure.py before production changes. Example assertion: `assert "响应时间（分钟）：15" in extract_bounded(str(path), 10)`; reject merged/borderless/scanned samples with `pytest.raises(ValueError)`.
- [x] Run `.venv/Scripts/python.exe -m pytest tests/unit/test_pdf_structure.py -q --tb=short`; record expected assertion failures before changing app code.
- [x] Create app/rag/pdf.py with `extract_pdf(path, max_pages=100)` returning content and sections. Copy Table.extract(), rows.cells and bbox while each page is alive; require rectangular matching cells, one nonempty unique header row, and bounded complete content. Serialize each data row as `【PDF 第 1 页 · 表 p1-t1 · 数据行 2】\n页面上下文：…\n服务等级：紧急\n响应时间（分钟）：15`.
- [x] Change production/knowledge_parser.py to export `extract_document(path,max_pages)` and preserve `extract_bounded` as its string adapter. Change rag/ingest.py extract_text's PDF branch to share that module. Use knowledge.py existing sections JSONB for original cells, not a new migration.

## Task 2: Atomic chunks and evidence

- [x] Run the long-table, oversized-row and excerpt tests and retain their RED output.
- [x] In rag/ingest.py chunk_markdown, reject oversized table rows rather than split them; on long plain PDF paragraphs, repeat only the page marker and account for its length within max_chars.
- [x] In rag/retriever.py add optional source metadata to Citation. For marked PDF blocks use complete content as excerpt; parse only the known prefix and propagate revision_id. Keep ordinary Markdown excerpt behavior unchanged.
- [x] In production/knowledge.py pipeline_config add extraction/chunking version keys without changing existing algorithm weights or weakening acceptance thresholds.
- [x] Expose page, table and row in frontend CitationList.tsx/types.ts; preserve old citations without metadata. Verify with Vitest and strict TypeScript build.

## Task 3: Real dependency acceptance and audit

- [x] Add a knowledge_context test in production/test_knowledge_snapshots.py importing actual synthetic PDFs. Exercise enqueue_upload/process_job/job_detail/execute_publish/build_retriever with real PostgreSQL and Qdrant. Assert exact urgent response cells and vector/PG count, employee scope, structured citations and deactivation.
- [x] Capture controlled model input and validate that its answer references the correct returned page/table/row; classify this as Mock model acceptance.
- [x] Run the complete backend suite with explicit TEST_DATABASE_URL/TEST_REDIS_URL/TEST_QDRANT_URL after migrating the disposable database; save JUnit and readable log. Run Ruff, frontend tests and production build.
- [x] Write docs/project-audit.md and docs/improvement-backlog.md with confirmed scenarios, files, test evidence, impact, priorities and future acceptance. Write AGENTS.md with durable evidence rules and verified test commands. Do not commit or push the user's preexisting work.
- [x] Independently review all changed boundaries, final evidence and supported/unsupported claims. Record unverified real PDF/model quality and remaining business gaps instead of claiming production readiness.

## Completion record — 2026-10-02

Inline implementation and independent review completed. Confirmed review cases (rotation, mixed borderless/ruled tables, tiled scans, normal prose, numbered steps, legacy-source laundering) each received regression coverage and fixes. Added P0 human-record truth in graph/UI with RED→GREEN evidence. Final acceptance: 287 backend tests, zero skips; 27 frontend tests; 18 demo/production-mock browser cases; TypeScript/build, Ruff and lock checks pass. Source, test and report evidence is listed in docs/project-audit.md and artifacts/audit-implementation-evidence.json. Real incident PDF, real model quality, Linux/production-live acceptance and later business tasks remain explicitly unverified/pending. No commit, push or deployment.
