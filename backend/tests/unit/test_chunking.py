from pathlib import Path

import pytest

from app.rag.ingest import (
    chunk_markdown,
    extract_text,
    parse_frontmatter,
    validate_article_structure,
)


def test_chunk_markdown_keeps_source_heading_and_chunk_order() -> None:
    markdown = "# VPN 连接\n\n第一步。\n\n第二步。"

    chunks = chunk_markdown(
        markdown,
        source_path="vpn-connection.md",
        max_chars=12,
        overlap_chars=0,
    )

    assert [chunk.chunk_index for chunk in chunks] == [0, 1]
    assert [chunk.content for chunk in chunks] == ["第一步。", "第二步。"]
    assert all(chunk.source_title == "VPN 连接" for chunk in chunks)
    assert all(chunk.source_path == "vpn-connection.md" for chunk in chunks)
    assert all(chunk.document_id == chunks[0].document_id for chunk in chunks)
    assert all(chunk.version == chunks[0].version for chunk in chunks)
    assert [
        markdown[chunk.char_start : chunk.char_end] for chunk in chunks
    ] == ["第一步。", "第二步。"]


def test_chunk_markdown_overlaps_only_long_paragraph_windows() -> None:
    markdown = "# Window test\n\nabcdefghijklmnop"

    chunks = chunk_markdown(
        markdown,
        source_path="window.md",
        max_chars=6,
        overlap_chars=2,
    )

    assert [chunk.content for chunk in chunks] == [
        "abcdef",
        "efghij",
        "ijklmn",
        "mnop",
    ]
    assert all(
        previous.char_end - current.char_start == 2
        for previous, current in zip(chunks, chunks[1:])
    )
    assert all(
        markdown[chunk.char_start : chunk.char_end] == chunk.content
        for chunk in chunks
    )


@pytest.mark.parametrize(
    ("max_chars", "overlap_chars"),
    [(0, 0), (10, -1), (10, 10), (10, 11)],
)
def test_chunk_markdown_rejects_invalid_window_configuration(
    max_chars: int,
    overlap_chars: int,
) -> None:
    with pytest.raises(ValueError, match="max_chars|overlap_chars"):
        chunk_markdown(
            "# Test\n\ncontent",
            source_path="test.md",
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )


def test_extract_text_reads_utf8_markdown(tmp_path: Path) -> None:
    markdown_path = tmp_path / "vpn.md"
    markdown_path.write_text("# VPN 指南\n\n重新连接客户端。", encoding="utf-8")

    assert extract_text(markdown_path) == "# VPN 指南\n\n重新连接客户端。"


def test_extract_text_reads_pdf_fixture() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[1] / "fixtures" / "vpn-error-codes.pdf"
    )

    extracted = extract_text(fixture_path)

    assert "VPN-720" in extracted
    assert "证书" in extracted
    assert "何时转人工" in extracted
    assert len(chunk_markdown(extracted, source_path=fixture_path.name)) <= 3


def test_extract_text_rejects_empty_markdown(tmp_path: Path) -> None:
    empty_path = tmp_path / "empty.md"
    empty_path.write_text(" \n\t", encoding="utf-8")

    with pytest.raises(ValueError, match="empty"):
        extract_text(empty_path)


def test_extract_text_rejects_non_utf8_markdown(tmp_path: Path) -> None:
    invalid_path = tmp_path / "invalid.md"
    invalid_path.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(ValueError, match="UTF-8"):
        extract_text(invalid_path)


def test_extract_text_rejects_unsupported_extension(tmp_path: Path) -> None:
    unsupported_path = tmp_path / "notes.txt"
    unsupported_path.write_text("not accepted", encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported"):
        extract_text(unsupported_path)


def test_all_seed_knowledge_articles_are_valid_and_chunkable() -> None:
    knowledge_dir = Path(__file__).resolve().parents[3] / "data" / "knowledge"
    articles = sorted(knowledge_dir.glob("*.md"))

    assert 30 <= len(articles) <= 50
    for article in articles:
        text = extract_text(article)
        metadata = parse_frontmatter(text)
        validate_article_structure(text, article.name)

        assert metadata["version"]
        assert metadata["access_level"] in {"employee", "support", "admin"}
        assert chunk_markdown(
            text,
            source_path=article.name,
            version=metadata["version"],
            access_level=metadata["access_level"],
        )


def test_seed_knowledge_covers_each_required_it_category() -> None:
    knowledge_dir = Path(__file__).resolve().parents[3] / "data" / "knowledge"
    article_names = {path.name for path in knowledge_dir.glob("*.md")}
    categories = {
        "vpn": {
            "vpn-connection.md",
            "vpn-error-codes.md",
            "vpn-certificate.md",
            "vpn-slow-connection.md",
        },
        "account": {
            "account-access.md",
            "account-password-reset.md",
            "account-lockout.md",
            "account-mfa.md",
        },
        "access": {
            "shared-folder-access.md",
            "project-system-access.md",
            "temporary-admin-access.md",
            "access-review.md",
        },
        "device": {
            "printer-queue.md",
            "monitor-display.md",
            "docking-station.md",
            "laptop-battery.md",
        },
        "network": {
            "wifi-disconnect.md",
            "dns-resolution.md",
            "wired-network.md",
            "proxy-settings.md",
        },
        "software": {
            "software-installation.md",
            "office-repair.md",
            "browser-cache.md",
            "video-meeting-audio.md",
        },
        "security": {
            "phishing-report.md",
            "suspicious-popup.md",
            "lost-device.md",
            "usb-device-policy.md",
        },
    }

    assert all(
        len(required_files) >= 4 for required_files in categories.values()
    )
    assert all(
        required_files <= article_names for required_files in categories.values()
    )
