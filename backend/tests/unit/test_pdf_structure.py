from dataclasses import asdict
from types import SimpleNamespace

import pytest

from app.production.knowledge_parser import extract_bounded
from app.rag.ingest import chunk_markdown, extract_text
from app.rag.retriever import _to_hit
from tests.pdf_samples import CONTEXT, HEADERS, ROWS, mixed_tables_pdf, prose_pdf, scan_pdf, table_pdf, tiled_scan_pdf


@pytest.mark.parametrize("extractor", [extract_bounded, extract_text])
def test_real_pdf_cells_become_labelled_rows_in_both_ingestion_paths(tmp_path, extractor):
    path = table_pdf(tmp_path / "response.pdf")
    content = extractor(str(path), 10) if extractor is extract_bounded else extractor(path)
    assert "服务等级：紧急" in content
    assert "响应时间（分钟）：15" in content
    assert "适用范围：业务中断" in content
    assert "PDF 第 1 页" in content
    assert CONTEXT in content


def test_pdf_table_chunks_keep_whole_rows_headers_units_and_conditions(tmp_path):
    path = table_pdf(tmp_path / "long-response.pdf", rows=ROWS * 14)
    content = extract_bounded(str(path), 10)
    chunks = chunk_markdown(content, source_path=path.name)
    table_chunks = [chunk for chunk in chunks if "数据行" in chunk.content]
    assert len(table_chunks) == 28
    for chunk in table_chunks:
        assert all(header in chunk.content for header in HEADERS)
        assert CONTEXT in chunk.content
        assert len(chunk.content) <= 800
        assert content[chunk.char_start:chunk.char_end] == chunk.content
        values = "240" if "服务等级：普通" in chunk.content else "15"
        assert f"响应时间（分钟）：{values}" in chunk.content


def test_table_evidence_is_not_cut_to_240_characters():
    content = "【PDF 第 2 页 · 表 p2-t1 · 数据行 3】\n" + "工作日适用条件。" * 34
    content += "\n服务等级：紧急\n响应时间（分钟）：15\n注：响应不是解决。"
    chunk = SimpleNamespace(document_id="doc", source_title="规范", source_path="response.pdf",
                            chunk_index=3, content=content, revision_id="revision-1")
    citation = _to_hit(chunk, "紧急多久响应", .9, .9, .9, .9).citation
    assert citation.excerpt == content
    data = asdict(citation)
    assert data["page_number"] == 2
    assert data["table_id"] == "p2-t1"
    assert data["row_index"] == 3
    assert data["index_revision"] == "revision-1"


@pytest.mark.parametrize("kind", ["merged", "borderless", "scan", "mixed_scan"])
def test_unreliable_pdf_never_becomes_unlabelled_trusted_text(tmp_path, kind):
    path = tmp_path / f"{kind}.pdf"
    if "scan" in kind:
        scan_pdf(path, mixed=kind == "mixed_scan")
    else:
        table_pdf(path, merged=kind == "merged", borderless=kind == "borderless")
    with pytest.raises(ValueError, match="unsupported|不支持|扫描|表格"):
        extract_bounded(str(path), 10)


def test_oversized_pdf_table_row_is_rejected_instead_of_cutting_its_meaning():
    content = "【PDF 第 1 页 · 表 p1-t1 · 数据行 1】\n服务等级：紧急\n条件：" + "条件" * 450
    with pytest.raises(ValueError, match="table|表格"):
        chunk_markdown(content, source_path="response.pdf")


def test_atomic_table_uses_bounded_row_budget_even_with_smaller_prose_windows():
    content = "【PDF 第 1 页 · 表 p1-t1 · 数据行 1】\n服务等级：紧急\n条件：" + "工作日条件。" * 68
    chunks = chunk_markdown(content, source_path="response.pdf", max_chars=400, overlap_chars=40)
    assert len(chunks) == 1
    assert chunks[0].content == content
    assert 400 < len(chunks[0].content) <= 800


def test_long_plain_pdf_paragraph_repeats_page_reference_on_each_chunk():
    content = "【PDF 第 3 页】\n" + "普通段落。" * 200
    chunks = chunk_markdown(content, source_path="paragraph.pdf")
    assert len(chunks) > 1
    assert all(chunk.content.startswith("【PDF 第 3 页】") for chunk in chunks)


def test_pdf_parser_returns_cell_truth_for_preview(tmp_path):
    from app.production import knowledge_parser
    path = table_pdf(tmp_path / "response.pdf")
    assert hasattr(knowledge_parser, "extract_document"), "preview must retain source cells"
    document = knowledge_parser.extract_document(str(path), 10)
    table = document["sections"][0]["tables"][0]
    assert table["headers"] == HEADERS
    assert table["rows"] == ROWS
    assert table["page_number"] == 1
    assert len(table["cells"]) == 3
    assert all(len(row) == 3 for row in table["cells"])
    assert table["bbox"] == [45.0, 110.0, 550.0, 191.0]


def test_borderless_table_beside_a_ruled_table_is_not_trusted_as_context(tmp_path):
    with pytest.raises(ValueError, match="不支持.*无框|不支持.*多栏"):
        extract_bounded(str(mixed_tables_pdf(tmp_path / "mixed-tables.pdf")), 10)


def test_repeated_plain_prose_is_not_mistaken_for_a_table(tmp_path):
    content = extract_bounded(str(prose_pdf(tmp_path / "prose.pdf")), 10)
    assert "The team reviews each request before a change is approved." in content
    assert "The team audits" in content


def test_plain_numbered_steps_are_not_mistaken_for_borderless_columns(tmp_path):
    import pymupdf
    path = tmp_path / "numbered-steps.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((45, 55), "VPN connection steps", fontsize=12)
        for number, step in enumerate(["Open the approved client.", "Check the certificate date.", "Reconnect to the company network."], start=1):
            page.insert_text((45, 80 + number * 22), f"{number}.", fontsize=11)
            page.insert_text((90, 80 + number * 22), step, fontsize=11)
        document.save(path)
    content = extract_bounded(str(path), 10)
    assert "Open the approved client." in content
    assert "Check the certificate date." in content
    assert "Reconnect to the company network." in content


def test_tiled_images_with_a_footer_do_not_pass_as_text_layer_evidence(tmp_path):
    with pytest.raises(ValueError, match="扫描|图像"):
        extract_bounded(str(tiled_scan_pdf(tmp_path / "tiled-scan.pdf")), 10)


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_rotated_page_keeps_original_cell_relationships(tmp_path, rotation):
    import pymupdf
    from app.production.knowledge_parser import extract_document
    path = table_pdf(tmp_path / "rotated.pdf")
    with pymupdf.open(path) as document:
        document[0].set_rotation(rotation)
        document.saveIncr()
    parsed = extract_document(str(path), 10)
    section = parsed["sections"][0]
    assert section["rotation"] == rotation
    assert section["tables"][0]["headers"] == HEADERS
    assert section["tables"][0]["rows"] == ROWS
    assert section["context"] == CONTEXT
