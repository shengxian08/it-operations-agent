"""Local, conservative extraction of text-layer PDFs and rectangular ruled tables.

No model, OCR, network, database, or application-client imports belong here.
Coordinates are copied while the source page is alive. Unknown layouts require
human review; this extractor does not certify arbitrary PDF semantics.
"""
from pathlib import Path
from typing import Any
import re

PDF_MARKER = re.compile(
    r"^【PDF 第 (?P<page>[1-9]\d*) 页(?: · 表 (?P<table>p\d+-t\d+) · 数据行 (?P<row>[1-9]\d*))?】"
)
LIST_MARKER = re.compile(r"(?:\d{1,4}[.)、]|[（(]\d{1,4}[）)]|[一二三四五六七八九十百]+[、.]|[•·●▪–-])$")
MAX_EXTRACTED_CHARACTERS = 2_000_000


def requires_pdf_reparse(source_path: str, content: str) -> bool:
    """Legacy flattened PDF content cannot inherit a new extraction identity."""
    if Path(source_path).suffix.lower() != ".pdf":
        return False
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", content) if part.strip()]
    return not paragraphs or any(PDF_MARKER.match(part) is None for part in paragraphs)


def _cell_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _copy_table(table: Any, page_number: int, index: int) -> dict[str, Any]:
    rows = [[_cell_text(cell) for cell in row] for row in table.extract()]
    cells = [[list(cell) if cell is not None else None for cell in row.cells]
             for row in table.rows]
    if (table.header.external or table.col_count < 2 or len(rows) < 2
        or any(len(row) != table.col_count for row in rows)
        or any(cell is None for row in cells for cell in row)):
        raise ValueError(f"PDF 第 {page_number} 页表格不支持：合并单元格或外部/多层表头。")
    # A merged cell can have a bbox instead of None; require one common grid.
    first = cells[0]
    for row in cells:
        if any(abs(cell[0] - first[column][0]) > 1 or abs(cell[2] - first[column][2]) > 1
               or abs(cell[1] - row[0][1]) > 1 or abs(cell[3] - row[0][3]) > 1
               for column, cell in enumerate(row)):
            raise ValueError(f"PDF 第 {page_number} 页表格不支持：单元格不是规则矩形网格。")
    headers = rows[0]
    if any(not header for header in headers) or len(set(headers)) != len(headers):
        raise ValueError(f"PDF 第 {page_number} 页表格不支持：空白或重复表头。")
    return {"id": f"p{page_number}-t{index}", "page_number": page_number,
            "bbox": list(table.bbox), "headers": headers, "rows": rows[1:], "cells": cells}


def _outside_table_text(page: Any, tables: list[dict[str, Any]]) -> str:
    import pymupdf
    rectangles = [pymupdf.Rect(table["bbox"]) for table in tables]
    lines = []
    for block in page.get_text("dict", sort=True)["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            kept = []
            for span in line["spans"]:
                rectangle = pymupdf.Rect(span["bbox"])
                # Reject boundary-crossing text rather than silently discarding it.
                overlapping = [table for table in rectangles if (rectangle & table).get_area() > 0]
                if overlapping:
                    if not any(table.contains(rectangle) for table in overlapping):
                        raise ValueError("PDF 表格不支持：文字跨越表格边界，需核对原件。")
                    continue
                kept.append(span["text"])
            text = "".join(kept).strip()
            if text:
                lines.append(text)
    return "\n".join(lines)


def _image_coverage(page: Any) -> float:
    """Union of displayed image rectangles, not size of each tile or resource."""
    import pymupdf
    rectangles = [pymupdf.Rect(item["bbox"]) & page.rect for item in page.get_image_info()]
    rectangles = [rectangle for rectangle in rectangles if not rectangle.is_empty]
    if len(rectangles) > 256:
        raise ValueError("PDF 不支持过多图像分片；需要人工核对。")
    edges = sorted({x for rectangle in rectangles for x in (rectangle.x0, rectangle.x1)})
    area = 0.0
    for left, right in zip(edges, edges[1:]):
        intervals = sorted((rectangle.y0, rectangle.y1) for rectangle in rectangles
                           if rectangle.x0 < right and rectangle.x1 > left)
        height, end = 0.0, float("-inf")
        for bottom, top in intervals:
            height += max(0, top - max(bottom, end))
            end = max(end, top)
        area += (right - left) * height
    return area / max(page.rect.get_area(), 1)


def _has_unparsed_columns(page: Any, tables: list[dict[str, Any]]) -> bool:
    """Require a wide aligned gap across three visual rows, outside ruled cells.

    A text-table guess alone is insufficient: repeated ordinary words also form
    guessed tables. This deliberately conservative layout check is not a parser
    for borderless tables, which remain outside the supported input contract.
    """
    import pymupdf
    rectangles = [pymupdf.Rect(table["bbox"]) for table in tables]
    words = [word for word in page.get_text("words")
             if not any(rectangle.contains(pymupdf.Rect(word[:4])) for rectangle in rectangles)]
    rows: list[list] = []
    for word in sorted(words, key=lambda item: ((item[1] + item[3]) / 2, item[0])):
        center = (word[1] + word[3]) / 2
        if not rows or abs(center - (rows[-1][0][1] + rows[-1][0][3]) / 2) > 3:
            rows.append([])
        rows[-1].append(word)
    gaps = []
    for row in rows:
        ordered = sorted(row, key=lambda word: word[0])
        # A numbered/bulleted instruction has an intentional prefix gutter,
        # not a second semantic column. Keep further gaps subject to detection.
        if len(ordered) > 1 and LIST_MARKER.fullmatch(ordered[0][4]):
            ordered = ordered[1:]
        gaps.append([(left[2], right[0]) for left, right in zip(ordered, ordered[1:])
                     if right[0] - left[2] >= 24])
    for index, row_gaps in enumerate(gaps[:-2]):
        for left, right in row_gaps:
            for next_left, next_right in gaps[index + 1]:
                common_left, common_right = max(left, next_left), min(right, next_right)
                if common_right - common_left < 24:
                    continue
                if any(min(common_right, third_right) - max(common_left, third_left) >= 24
                       for third_left, third_right in gaps[index + 2]):
                    return True
    return False


def extract_pdf(path: str | Path, max_pages: int = 100) -> dict[str, Any]:
    import pymupdf
    blocks: list[str] = []
    sections: list[dict[str, Any]] = []
    characters = 0
    if max_pages <= 0:
        raise ValueError("PDF page limit must be positive")
    with pymupdf.open(path) as document:
        if document.is_encrypted:
            raise ValueError("encrypted PDFs are not supported")
        if len(document) > max_pages:
            raise ValueError("PDF page limit exceeded")
        for page in document:
            number = page.number + 1
            rotation = page.rotation
            # PyMuPDF's table coordinates/order otherwise follow page rotation,
            # while text coordinates do not. Normalize only this in-memory copy.
            page.set_rotation(0)
            # A scan with a tiny text footer must not look like a successful page.
            if _image_coverage(page) >= .25:
                raise ValueError(f"PDF 第 {number} 页不支持扫描件或大面积图像；需要人工核对/OCR。")
            if not page.get_text().strip():
                if page.get_images() or page.get_drawings():
                    raise ValueError(f"PDF 第 {number} 页不支持无文字层图像/图形。")
                continue  # Truly blank pages contribute no invented evidence.
            finder = page.find_tables(strategy="lines_strict")
            tables = [_copy_table(table, number, index)
                      for index, table in enumerate(finder.tables, start=1)]
            if _has_unparsed_columns(page, tables):
                raise ValueError(f"PDF 第 {number} 页不支持疑似无框表格或多栏版式；请提供规则表格或人工整理稿。")
            for table in tables:
                if table["bbox"][1] < 36 or table["bbox"][3] > page.cropbox.height - 36:
                    raise ValueError(f"PDF 第 {number} 页表格不支持贴近页边的连续/跨页结构。")
            context = _outside_table_text(page, tables)
            page_blocks = []
            def append_block(block: str) -> None:
                nonlocal characters
                characters += len(block) + 2
                if characters > MAX_EXTRACTED_CHARACTERS:
                    raise ValueError("extracted text limit exceeded")
                page_blocks.append(block)

            if context and not tables:
                # Group ordinary page lines as before, but retain the page identity.
                append_block(f"【PDF 第 {number} 页】\n{context}")
            for table in tables:
                for row_index, row in enumerate(table["rows"], start=1):
                    values = "\n".join(f"{header}：{value if value else '（原单元格为空）'}"
                                       for header, value in zip(table["headers"], row, strict=True))
                    provenance = f"【PDF 第 {number} 页 · 表 {table['id']} · 数据行 {row_index}】"
                    row_context = f"\n页面上下文：{context}" if context else ""
                    append_block(f"{provenance}{row_context}\n{values}")
            page_content = "\n\n".join(page_blocks)
            blocks.extend(page_blocks)
            sections.append({"heading": f"PDF 第 {number} 页", "content": page_content,
                             "context": context, "page_number": number,
                             "rotation": rotation, "tables": tables})
    content = "\n\n".join(blocks)
    if not content.strip():
        raise ValueError("document contains no extractable text")
    return {"content": content, "sections": sections}
