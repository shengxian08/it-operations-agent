"""Synthetic PDFs with explicit cell truth, not customer incident evidence."""
from pathlib import Path

import pymupdf


HEADERS = ["服务等级", "响应时间（分钟）", "适用范围"]
ROWS = [["普通", "240", "一般问题"], ["紧急", "15", "业务中断"]]
CONTEXT = "服务响应规范。适用条件：工作日。注：响应时间不等于解决时间。"


def table_pdf(path: Path, *, rows=None, merged=False, borderless=False) -> Path:
    rows = ROWS if rows is None else rows
    x = [45, 175, 340, 550]
    y = [110 + 27 * index for index in range(len(rows) + 2)]
    with pymupdf.open() as document:
        page = document.new_page(width=595, height=max(842, y[-1] + 90))
        page.insert_text((45, 55), CONTEXT, fontname="china-s", fontsize=10)
        if not borderless:
            for index, column in enumerate(x):
                start = y[1] if merged and index == 1 else y[0]
                page.draw_line((column, start), (column, y[-1]))
            for line in y:
                page.draw_line((x[0], line), (x[-1], line))
        for row_index, cells in enumerate([HEADERS, *rows]):
            for column_index, cell in enumerate(cells):
                page.insert_text((x[column_index] + 5, y[row_index] + 18), cell,
                                 fontname="china-s", fontsize=9)
        document.save(path, deflate=True)
    return path


def scan_pdf(path: Path, *, text_footer=True, mixed=False) -> Path:
    with pymupdf.open() as image_document:
        image_page = image_document.new_page(width=500, height=650)
        image_page.insert_text((30, 55), "紧急响应时间：15分钟", fontname="china-s")
        image_bytes = image_page.get_pixmap().tobytes("png")
    with pymupdf.open() as document:
        if mixed:
            page = document.new_page()
            page.insert_text((45, 55), "普通文字页。", fontname="china-s")
        page = document.new_page(width=595, height=842)
        page.insert_image(pymupdf.Rect(40, 65, 555, 780), stream=image_bytes)
        if text_footer:
            page.insert_text((45, 815), "第2页", fontname="china-s", fontsize=9)
        document.save(path, deflate=True)
    return path


def mixed_tables_pdf(path: Path) -> Path:
    table_pdf(path)
    with pymupdf.open(path) as document:
        page = document[0]
        for index, row in enumerate([["Team", "Time", "Unit"], ["Low", "240", "min"], ["Critical", "999", "min"]]):
            for column, value in zip([45, 175, 340], row, strict=True):
                page.insert_text((column + 5, 350 + index * 27), value, fontsize=9)
        document.saveIncr()
    return path


def prose_pdf(path: Path) -> Path:
    with pymupdf.open() as document:
        page = document.new_page()
        for index, verb in enumerate(["reviews", "verifies", "records", "closes", "audits"]):
            page.insert_text((45, 90 + index * 18), f"The team {verb} each request before a change is approved.", fontsize=11)
        document.save(path)
    return path


def tiled_scan_pdf(path: Path) -> Path:
    with pymupdf.open() as image:
        page = image.new_page(width=200, height=300)
        page.insert_text((10, 60), "Critical response: 15 min", fontsize=10)
        raster = page.get_pixmap().tobytes("png")
    with pymupdf.open() as document:
        page = document.new_page(width=595, height=842)
        for rectangle in [(40, 40, 288, 408), (307, 40, 555, 408), (40, 428, 288, 796), (307, 428, 555, 796)]:
            page.insert_image(pymupdf.Rect(rectangle), stream=raster)
        page.insert_text((45, 824), "Page 1", fontsize=9)
        document.save(path, deflate=True)
    return path
