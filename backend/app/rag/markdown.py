"""Markdown structure mapped to the original source, never rendered or rewritten."""
from dataclasses import asdict, dataclass
import re
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.rules_block import fence as commonmark_fence


PARSER_VERSION = "markdown-it-py-4.2.0-structure-v1"


def requires_markdown_reparse(source_path: str, parser_version: str | None) -> bool:
    return source_path.lower().endswith(".md") and parser_version != PARSER_VERSION


@dataclass(frozen=True, slots=True)
class Heading:
    level: int
    title: str
    char_start: int
    char_end: int


@dataclass(frozen=True, slots=True)
class Block:
    block_type: str
    char_start: int
    char_end: int
    headings: tuple[Heading, ...]
    contains_code: bool

    def structure(self) -> dict[str, Any]:
        return {"parser_version": PARSER_VERSION, "block_type": self.block_type,
                "block_start": self.char_start, "block_end": self.char_end,
                "section_path": [h.title for h in self.headings],
                "headings": [asdict(h) for h in self.headings],
                "contains_code": self.contains_code}


@dataclass(frozen=True, slots=True)
class Document:
    headings: tuple[Heading, ...]
    blocks: tuple[Block, ...]

    @property
    def title(self) -> str | None:
        return next((heading.title for heading in self.headings if heading.level == 1), None)


def _source_lines(text: str) -> tuple[list[str], list[int]]:
    # CommonMark normalizes CRLF/CR for parsing, but its maps count source lines.
    # Avoid splitlines(): Unicode separators are not CommonMark line endings.
    lines = re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", text)
    if lines and lines[-1] == "":
        lines.pop()
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    return lines, offsets


def _masked_frontmatter(text: str) -> tuple[str, int]:
    match = re.match(r"\A\ufeff?---[ \t]*(?:\r\n|\r|\n).*?(?:\r\n|\r|\n)---[ \t]*(?:(?:\r\n|\r|\n)|\Z)", text, re.DOTALL)
    end = match.end() if match else 0
    masked = "".join(char if char in "\r\n" else " " for char in text[:end]) + text[end:]
    if masked.startswith("\ufeff"):
        masked = " " + masked[1:]
    return masked, end


def _fence_with_closure(state, start_line: int, end_line: int, silent: bool) -> bool:
    # The pinned CommonMark rule sets token.map[1] to body_end + 1 only when
    # it actually consumes a closer. Capture its source range, retaining its
    # own list/quote/indentation decisions instead of guessing raw markers.
    get_lines = state.getLines
    body_end = None

    def capture_lines(begin, end, indent, keep_last_lf):
        nonlocal body_end
        body_end = end
        return get_lines(begin, end, indent, keep_last_lf)

    state.getLines = capture_lines
    try:
        found = commonmark_fence(state, start_line, end_line, silent)
    finally:
        state.getLines = get_lines
    if found and not silent:
        token = state.tokens[-1]
        token.meta["source_fence_closed"] = body_end is not None and token.map[1] == body_end + 1
    return found


def parse_markdown(text: str) -> Document:
    if not text.strip():
        raise ValueError("knowledge text is empty")
    if "\x00" in text:
        raise ValueError("Markdown含空字符，无法可靠解析，请人工整理后重新上传。")
    _, offsets = _source_lines(text)
    masked, frontmatter_end = _masked_frontmatter(text)
    environment: dict[str, Any] = {}
    parser = MarkdownIt("commonmark").enable("table")
    parser.block.ruler.at("fence", _fence_with_closure)
    tokens = parser.parse(masked, environment)

    def span(mapping) -> tuple[int, int]:
        if not mapping or not (0 <= mapping[0] < mapping[1] < len(offsets)):
            raise ValueError("Markdown原文范围无法可靠定位，请人工核对。")
        start, end = offsets[mapping[0]], offsets[mapping[1]]
        end -= len(text[start:end]) - len(text[start:end].rstrip("\r\n"))
        return start, end

    for token in tokens:
        if token.type == "html_block":
            raise ValueError("Markdown中的HTML块需要人工整理为正文、代码或表格后重新上传。")
        if token.type == "fence":
            if not token.meta.get("source_fence_closed"):
                raise ValueError("Markdown代码围栏未闭合，请人工整理后重新上传。")

    root_indices = [index for index, token in enumerate(tokens) if token.level == 0 and token.map]
    entries = []
    for position, index in enumerate(root_indices):
        token = tokens[index]
        start, end = span(token.map)
        stop = root_indices[position + 1] if position + 1 < len(root_indices) else len(tokens)
        children = tokens[index:stop]
        contains_code = any(t.type in {"fence", "code_block"} or
            (t.type == "inline" and any(c.type == "code_inline" for c in t.children or [])) for t in children)
        kind = {"paragraph_open": "paragraph", "fence": "code", "code_block": "code",
                "ordered_list_open": "list", "bullet_list_open": "list", "blockquote_open": "blockquote",
                "table_open": "table", "hr": "divider", "heading_open": "heading"}.get(token.type)
        if kind is None:
            raise ValueError("Markdown结构无法可靠识别，请人工整理后重新上传。")
        title = tokens[index + 1].content if kind == "heading" else ""
        entries.append((start, end, kind, title, int(token.tag[1:]) if kind == "heading" else 0, contains_code))

    # Reference definitions do not appear as block tokens. Keep their source
    # maps as evidence, including duplicate definitions if the parser reports them.
    references = list(environment.get("references", {}).values()) + environment.get("duplicate_refs", [])
    for reference in references:
        start, end = span(reference["map"])
        if not any(a <= start and end <= b for a, b, *_ in entries):
            entries.append((start, end, "reference", "", 0, False))
    entries.sort(key=lambda item: (item[0], item[1]))

    cursor = frontmatter_end
    for start, end, *_ in entries:
        if start < cursor:
            raise ValueError("Markdown原文范围重叠，无法可靠解析，请人工核对。")
        if masked[cursor:start].strip():
            raise ValueError("Markdown存在未定位正文，无法可靠解析，请人工核对。")
        cursor = end
    if masked[cursor:].strip():
        raise ValueError("Markdown存在未定位正文，无法可靠解析，请人工核对。")

    headings, blocks = [], []
    path: list[Heading] = []
    for start, end, kind, title, level, contains_code in entries:
        if kind == "heading":
            heading = Heading(level, title.strip(), start, end)
            headings.append(heading)
            path = [h for h in path if h.level < level] + [heading]
        elif kind != "divider":
            blocks.append(Block(kind, start, end, tuple(path), contains_code))
    return Document(tuple(headings), tuple(blocks))


def markdown_sections(text: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for block in parse_markdown(text).blocks:
        path = [heading.title for heading in block.headings]
        # Equal heading labels at different offsets are different source sections.
        heading_ranges = [asdict(heading) for heading in block.headings]
        if not sections or sections[-1]["headings"] != heading_ranges:
            sections.append({"heading": path[-1] if path else "正文", "section_path": path,
                "headings": heading_ranges, "parser_version": PARSER_VERSION,
                "char_start": block.char_start, "char_end": block.char_end, "blocks": []})
        section = sections[-1]
        section["char_end"] = block.char_end
        section["content"] = text[section["char_start"]:section["char_end"]]
        section["blocks"].append({"block_type": block.block_type,
            "content": text[block.char_start:block.char_end], "char_start": block.char_start,
            "char_end": block.char_end, "contains_code": block.contains_code})
    return sections
