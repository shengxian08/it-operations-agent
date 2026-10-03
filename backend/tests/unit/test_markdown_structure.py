"""Source/semantic invariants for Markdown, independent of a rendered answer."""
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from app.agent.graph import _answer_prompt
from app.production.knowledge import article_sections
from app.rag.ingest import chunk_markdown
from app.rag.retriever import _to_hit


def structure(chunk):
    value = getattr(chunk, "structure", None)
    assert isinstance(value, dict), "Markdown chunks must preserve their actual source structure"
    return value


def test_fenced_command_with_blank_line_stays_one_complete_source_span():
    source = "# 指南\n\n## E-42\n\n```powershell\nGet-Service\n\nGet-Process\n```\n"
    chunks = chunk_markdown(source, source_path="guide.md")
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.content == "```powershell\nGet-Service\n\nGet-Process\n```"
    assert source[chunk.char_start:chunk.char_end] == chunk.content
    assert structure(chunk)["section_path"] == ["指南", "E-42"]
    assert structure(chunk)["block_type"] == "code"


def test_section_path_retains_model_and_code_only_present_in_heading():
    source = "# 会议指南\n\n## RoomBox B200 E-42\n\n核对显示屏输入源。\n"
    [chunk] = chunk_markdown(source, source_path="room.md")
    assert chunk.content == "核对显示屏输入源。"
    assert structure(chunk)["section_path"] == ["会议指南", "RoomBox B200 E-42"]
    assert "B200" not in chunk.content and "E-42" not in chunk.content
    for heading in structure(chunk)["headings"]:
        assert heading["title"] in source[heading["char_start"]:heading["char_end"]]


def test_heading_inside_fence_never_becomes_title_or_section():
    source = "```sh\n# fake title\n\n## fake section\n```\n\n# Actual\n\n## Real\n\nbody\n"
    chunks = chunk_markdown(source, source_path="actual.md")
    assert all(chunk.source_title == "Actual" for chunk in chunks)
    assert structure(chunks[0])["section_path"] == []
    assert structure(chunks[-1])["section_path"] == ["Actual", "Real"]


def test_sibling_and_setext_headings_have_real_hierarchical_paths():
    source = "Guide\n=====\n\nSection A\n---------\n\nfirst\n\n### Detail\n\nsecond\n\n## Section B\n\nthird"
    chunks = chunk_markdown(source, source_path="setext.md")
    assert [structure(c)["section_path"] for c in chunks] == [
        ["Guide", "Section A"], ["Guide", "Section A", "Detail"], ["Guide", "Section B"],
    ]
    assert all(c.source_title == "Guide" for c in chunks)


@pytest.mark.parametrize("opener,closer", [("```", "```"), ("~~~~", "~~~~~"), ("````", "`````")])
def test_fence_delimiters_do_not_cut_literal_blank_lines(opener, closer):
    body = f"{opener}config\n# comment\n\nkey = value\n{closer}"
    source = "# Config\n\n" + body + "\n"
    [chunk] = chunk_markdown(source, source_path="config.md")
    assert chunk.content == body
    assert structure(chunk)["contains_code"] is True


def test_indented_config_keeps_indentation_and_blank_line():
    body = "    [network]\n    enabled = true\n\n    [proxy]\n    enabled = false"
    source = "# Config\n\n" + body + "\n"
    [chunk] = chunk_markdown(source, source_path="config.md")
    assert chunk.content == body
    assert structure(chunk)["block_type"] == "code"


def test_crlf_source_ranges_and_command_whitespace_are_exact():
    body = "```powershell\r\n  Get-Service\r\n\r\n  Get-Process\r\n```"
    source = "# Guide\r\n\r\n## Steps\r\n\r\n" + body + "\r\n"
    [chunk] = chunk_markdown(source, source_path="crlf.md")
    assert chunk.content == body
    assert source[chunk.char_start:chunk.char_end] == body
    assert structure(chunk)["section_path"] == ["Guide", "Steps"]


def test_repeated_text_ranges_identify_the_correct_section():
    source = "# Guide\n\n## A\n\nSame text.\n\n## B\n\nSame text.\n"
    first, second = chunk_markdown(source, source_path="repeat.md")
    assert first.content == second.content == "Same text."
    assert first.char_start != second.char_start
    assert structure(first)["section_path"] == ["Guide", "A"]
    assert structure(second)["section_path"] == ["Guide", "B"]
    assert all(source[c.char_start:c.char_end] == c.content for c in [first, second])


def test_frontmatter_cannot_add_fake_section_context():
    source = "---\nversion: 1\n# metadata fake\naccess_level: admin\n---\n\n# Guide\n\n## Real\n\nbody"
    [chunk] = chunk_markdown(source, source_path="metadata.md")
    assert chunk.content == "body"
    assert chunk.source_title == "Guide"
    assert structure(chunk)["section_path"] == ["Guide", "Real"]
    assert "metadata" not in json.dumps(structure(chunk))


@pytest.mark.parametrize("body", [
    "```sh\n" + "x" * 801 + "\n```",
    "    " + "x" * 801,
    "execute `" + "x" * 801 + "` safely",
    "1. " + "x" * 801 + "\n\n2. next",
    "> " + "x" * 801,
    "| Item | Command |\n| --- | --- |\n| a | " + "x" * 801 + " |",
])
def test_oversized_semantic_unit_requires_manual_edit_instead_of_fragmenting(body):
    with pytest.raises(ValueError, match="上限|800|limit"):
        chunk_markdown("# Guide\n\n" + body, source_path="oversize.md")


@pytest.mark.parametrize("body", ["```sh\necho safe", "~~~~config\nkey=true\n", "````sh\necho safe\n```\n"])
def test_unclosed_fence_is_explicit_failure(body):
    with pytest.raises(ValueError, match="闭合|closed"):
        chunk_markdown("# Guide\n\n" + body, source_path="unclosed.md")


def test_inline_command_and_pipe_table_are_complete_blocks():
    source = "# Guide\n\n## Steps\n\nRun `Get-Service` first.\n\n| Level | Minutes |\n| --- | --- |\n| Urgent | 15 |\n"
    chunks = chunk_markdown(source, source_path="mixed.md")
    assert len(chunks) == 2
    assert structure(chunks[0])["contains_code"] is True
    assert structure(chunks[1])["block_type"] == "table"
    assert "| --- | --- |" in chunks[1].content
    assert all(source[c.char_start:c.char_end] == c.content for c in chunks)


def test_list_with_embedded_fence_is_one_atomic_source_unit():
    source = "# Guide\n\n1. Inspect:\n\n   ```sh\n   # not a heading\n\n   echo safe\n   ```\n\n2. Report.\n"
    [chunk] = chunk_markdown(source, source_path="nested.md")
    assert structure(chunk)["block_type"] == "list"
    assert structure(chunk)["contains_code"] is True
    assert structure(chunk)["section_path"] == ["Guide"]
    assert "\n\n   echo safe" in chunk.content and "2. Report." in chunk.content


def test_reference_definitions_remain_real_source_spans():
    source = "# Guide\n\nSee [policy][internal].\n\n[internal]: https://example.invalid/policy \"Policy\"\n"
    chunks = chunk_markdown(source, source_path="reference.md")
    assert any("[internal]:" in c.content for c in chunks)
    assert all(source[c.char_start:c.char_end] == c.content for c in chunks)
    assert all(structure(c)["parser_version"] for c in chunks)


def test_preview_uses_the_same_real_structure_and_keeps_code_complete():
    source = "# Guide\n\n## Real\n\n```sh\n# fake\n\necho safe\n```\n"
    sections = article_sections(source)
    assert len(sections) == 1
    assert sections[0]["heading"] == "Real"
    assert sections[0]["blocks"][0]["content"] == "```sh\n# fake\n\necho safe\n```"
    assert sections[0]["section_path"] == ["Guide", "Real"]


def test_new_code_evidence_reaches_model_prompt_without_normalization_or_truncation():
    raw = "```powershell\n  Get-Service\n\n  Write-Output '" + "safe " * 60 + "'\n```"
    chunk = SimpleNamespace(document_id="doc", source_title="Guide", source_path="guide.md", chunk_index=0,
        content=raw, char_start=25, char_end=25 + len(raw), revision_id="new-revision",
        structure={"parser_version": "markdown-it-py-4.2.0-structure-v1", "block_type": "code",
                   "section_path": ["Guide", "Steps"], "contains_code": True})
    hit = _to_hit(chunk, "Get-Service", 1, 1, 1, 1)
    assert hit.citation.excerpt == raw
    prompt = _answer_prompt("How?", [hit])
    evidence = json.loads(prompt.split("<knowledge_evidence>\n", 1)[1].split("\n</knowledge_evidence>", 1)[0])
    assert evidence[0]["excerpt"] == raw
    assert evidence[0]["section_path"] == ["Guide", "Steps"]
    assert evidence[0]["char_start"] == 25 and evidence[0]["char_end"] == 25 + len(raw)


def test_extraction_checks_size_before_structural_parser(tmp_path, monkeypatch):
    from app.production.knowledge_parser import extract_document
    import app.rag.markdown as markdown_module

    source = tmp_path / "oversize.md"
    source.write_bytes(("# Guide\n\n" + "x" * 2_000_000).encode())
    calls = []
    monkeypatch.setattr(markdown_module, "markdown_sections", lambda text: calls.append(text) or [])
    with pytest.raises(ValueError, match="text limit"):
        extract_document(str(source), 200)
    assert calls == []


@pytest.mark.parametrize("marker", ["```", "~~~"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("fake_closer", ["    ", "> "])
def test_commonmark_code_content_cannot_masquerade_as_fence_closer(marker, newline, fake_closer):
    source = newline.join([marker + "sh", "echo safe", fake_closer + marker, ""])
    with pytest.raises(ValueError, match="未闭合"):
        chunk_markdown(source, source_path="fake-closer.md")


@pytest.mark.parametrize("marker", ["```", "~~~"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("template", ["> - {m}sh\n>   echo safe\n>   {m}\n", "- > {m}sh\n  > echo safe\n  > {m}\n"])
def test_actual_closer_inside_nested_containers_is_accepted(marker, newline, template):
    source = template.format(m=marker).replace("\n", newline)
    [chunk] = chunk_markdown(source, source_path="nested-closer.md")
    assert chunk.content == source[chunk.char_start:chunk.char_end]
    assert chunk.structure["contains_code"] is True


def test_fence_body_unicode_separators_do_not_change_commonmark_line_count():
    source = "```sh\necho 'a\u2028b\u0085c'\n```\n"
    [chunk] = chunk_markdown(source, source_path="unicode.md")
    assert chunk.content == source.rstrip("\n")


@pytest.mark.asyncio
@pytest.mark.parametrize("with_structure", [True, False])
async def test_hybrid_reranking_uses_source_context_and_preserves_legacy_raw(monkeypatch, with_structure):
    from app.rag.ingest import DeterministicEmbedder, chunk_search_text
    from app.rag.retriever import HybridRetriever, LexicalReranker

    [actual] = chunk_markdown("# Guide\n\n## E-42\n\nReconnect the cable.", source_path="guide.md")
    chunk = SimpleNamespace(**asdict(actual), id="test-hybrid-chunk")
    if not with_structure:
        chunk.structure = None
    texts = []
    class CapturingReranker(LexicalReranker):
        async def score(self, query, candidates):
            texts.extend(candidates)
            return await super().score(query, candidates)
    class LocalVectors:
        async def query_points(self, **kwargs):
            return SimpleNamespace(points=[SimpleNamespace(id=chunk.id, score=1.0)])
    retriever = HybridRetriever(None, LocalVectors(), DeterministicEmbedder(dimensions=32), CapturingReranker())
    async def load_chunks(allowed):
        assert "employee" in allowed
        return [chunk]
    monkeypatch.setattr(retriever, "_load_accessible_chunks", load_chunks)
    [hit] = await retriever.retrieve("E-42", user_access_level="employee")
    assert texts == [chunk_search_text(chunk)]
    assert hit.citation.excerpt == "Reconnect the cable."
    if with_structure:
        assert hit.score > 0.35
        assert hit.citation.section_path == ("Guide", "E-42")
    else:
        assert texts == ["Guide\nReconnect the cable."]
        assert hit.score == 0.0 and hit.citation.section_path == ()
