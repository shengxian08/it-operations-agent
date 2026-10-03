"""Synthetic PDF boundaries and bounded atomic Markdown experiment.

No candidate parser is simulated. The atomic Markdown prototype rejects long
code fences at the same 800-character cap rather than slicing executable text.
"""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import statistics
import time

from app.rag.ingest import chunk_markdown
from app.rag.pdf import extract_pdf
import app.rag.pdf as pdf_module
from tests.pdf_samples import (
    HEADERS, ROWS, CONTEXT, mixed_tables_pdf, prose_pdf, scan_pdf,
    table_pdf, tiled_scan_pdf,
)


def atomic_markdown_spans(source, *, max_chars=800):
    fence_pattern = re.compile(r"(?ms)^```[^\n]*\n.*?^```[^\n]*(?:\n|$)")
    fences = list(fence_pattern.finditer(source))
    fence_markers = list(re.finditer(r"(?m)^```", source))
    if len(fence_markers) != 2 * len(fences):
        raise ValueError("unclosed or unsupported code fence; manual review required")
    spans = []
    boundaries = [(0, fences[0].start() if fences else len(source))]
    for index, fence in enumerate(fences):
        raw = fence.group(0).rstrip("\n")
        if len(raw) > max_chars:
            raise ValueError("oversize code fence; manual review required")
        spans.append({"type": "code", "start": fence.start(), "end": fence.start() + len(raw), "raw": raw})
        boundaries.append((fence.end(), fences[index + 1].start() if index + 1 < len(fences) else len(source)))
    for start, end in boundaries:
        for match in re.finditer(r"\S(?:.*?\S)?(?=\n[ \t]*\n|[ \t\r\n]*\Z)", source[start:end], re.DOTALL):
            raw = match.group(0)
            if re.fullmatch(r"#{1,6}\s+[^\n]+", raw):
                continue
            if len(raw) > max_chars:
                raise ValueError("oversize paragraph; manual review required")
            spans.append({"type": "paragraph", "start": start + match.start(), "end": start + match.end(), "raw": raw})
    return sorted(spans, key=lambda span: span["start"])


def run(output_directory, fixtures_directory=None):
    output_directory.mkdir(parents=True, exist_ok=True)
    fixtures = fixtures_directory or output_directory / "synthetic-pdfs"
    fixtures.mkdir(exist_ok=True)
    definitions = [
        ("ruled-table", lambda path: table_pdf(path), "accepted"),
        ("prose", prose_pdf, "accepted"),
        ("merged", lambda path: table_pdf(path, merged=True), "rejected"),
        ("borderless", lambda path: table_pdf(path, borderless=True), "rejected"),
        ("scan-with-footer", scan_pdf, "rejected"),
        ("tiled-scan", tiled_scan_pdf, "rejected"),
        ("mixed-supported-and-borderless", mixed_tables_pdf, "rejected"),
    ]
    records = []
    for ident, create, expected in definitions:
        path = fixtures / f"{ident}.pdf"
        reused = path.exists()
        if not reused:
            create(path)
        times, result, error = [], None, None
        for _ in range(3):
            start = time.perf_counter()
            try:
                result = extract_pdf(path)
            except ValueError as exc:
                error = str(exc)
            times.append(time.perf_counter() - start)
        actual = "rejected" if error else "accepted"
        if actual != expected:
            raise ValueError(f"unexpected synthetic boundary: {ident} {actual}")
        record = {"id": ident, "synthetic": True, "source_path": str(path),
                  "existing_fixture_reused": reused,
                  "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                  "expected": expected, "actual": actual, "error": error,
                  "seconds": times, "median_seconds": statistics.median(times)}
        if result:
            record["output"] = result
            chunks = chunk_markdown(result["content"], source_path=str(path), version="synthetic-v1")
            record["chunk_count"] = len(chunks)
            if ident == "ruled-table":
                table = result["sections"][0]["tables"][0]
                x, y = [45, 175, 340, 550], [110, 137, 164, 191]
                boxes = [[[x[column], y[row], x[column + 1], y[row + 1]] for column in range(3)] for row in range(3)]
                record["truth"] = {"headers": HEADERS, "rows": ROWS, "context": CONTEXT,
                                   "cell_bboxes_pdf_points": boxes, "bbox_tolerance_points": 1}
                record["checks"] = {"headers_exact": table["headers"] == HEADERS,
                                    "cells_exact": table["rows"] == ROWS,
                                    "all_cell_bboxes_present": all(cell and len(cell) == 4 for row in table["cells"] for cell in row),
                                    "cell_bboxes_match_drawn_grid": all(abs(a - b) <= 1
                                        for expected_row, actual_row in zip(boxes, table["cells"], strict=True)
                                        for expected_cell, actual_cell in zip(expected_row, actual_row, strict=True)
                                        for a, b in zip(expected_cell, actual_cell, strict=True)),
                                    "page_one": table["page_number"] == 1,
                                    "units_and_condition_and_note_in_chunks": all("分钟" in chunk.content and "工作日" in chunk.content
                                        and "响应时间不等于解决时间" in chunk.content for chunk in chunks)}
                if not all(record["checks"].values()):
                    raise ValueError("cell/context provenance check failed")
        records.append(record)
    short = "# 诊断命令\n\n## 只读代理\n\n```powershell\nnetsh winhttp show proxy\n\n# 将原样输出交给支持\n```\n\n禁止自行重置受管代理。"
    long_command = 'Invoke-Diagnostic -ReadOnly -Metadata "' + "资产网络证书版本" * 120 + '"'
    oversized = "# 诊断命令\n\n## 只读长参数\n\n```powershell\n" + long_command + "\n```\n"
    markdown = []
    for ident, source in (("short-fence-with-blank-line", short), ("oversized-command", oversized)):
        path = output_directory / f"{ident}.md"
        path.write_text(source, encoding="utf-8")
        baseline = chunk_markdown(source, source_path=str(path), version="synthetic-v1")
        try:
            candidate = atomic_markdown_spans(source)
            error = None
        except ValueError as exc:
            candidate, error = [], str(exc)
        markdown.append({"id": ident, "synthetic": True, "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                         "same_max_chars": 800, "baseline_chunk_count": len(baseline),
                         "baseline_chunks": [chunk.content for chunk in baseline],
                         "baseline_whole_fence_present": any(chunk.content.startswith("```") and chunk.content.endswith("```") for chunk in baseline),
                         "baseline_long_command_whole": any(long_command in chunk.content for chunk in baseline) if ident == "oversized-command" else None,
                         "atomic_candidate": candidate, "atomic_candidate_error": error,
                         "atomic_raw_spans_exact": all(source[span["start"]:span["end"]] == span["raw"] for span in candidate),
                         "candidate_adopted_in_application": False})
    result = {"schema": 1, "synthetic": True, "real_customer_pdf": False,
              "environment": {"python": platform.python_version(), "platform": platform.platform(),
                              "pdf_module": str(Path(pdf_module.__file__).resolve()),
                              "pdf_module_sha256": hashlib.sha256(Path(pdf_module.__file__).read_bytes()).hexdigest()},
              "parser": {"name": "PyMuPDF", "version": importlib.metadata.version("pymupdf"),
                         "pipeline": "ruled-cells-normalized-page-context-v2", "ocr": False},
              "pdf_cases": records, "markdown_cases": markdown,
              "candidate_parsers_executed": [], "full_rag_answers_evaluated": False}
    if platform.system() == "Linux":
        import resource
        result["environment"]["process_peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        result["environment"]["cgroup"] = {name: (Path("/sys/fs/cgroup") / name).read_text().strip()
            for name in ("cpu.max", "memory.max", "memory.peak", "memory.events")
            if (Path("/sys/fs/cgroup") / name).exists()}
    (output_directory / "parsing.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"pdf_boundaries": [{"id": row["id"], "actual": row["actual"]} for row in records],
                      "markdown": [{key: row[key] for key in ("id", "baseline_chunk_count", "baseline_whole_fence_present", "baseline_long_command_whole", "atomic_candidate_error")} for row in markdown]}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--fixtures-directory", type=Path)
    args = parser.parse_args()
    run(args.output_directory, args.fixtures_directory)
