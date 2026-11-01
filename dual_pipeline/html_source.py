"""Deterministic, source-bound visible-text extraction for governed HTML intake."""
from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser

HTML_EXTRACTOR_NAME = "python-html-parser"
HTML_EXTRACTOR_VERSION = "visible-text-heading-table-v1"
MAX_HTML_BYTES = 4 * 1024 * 1024
MAX_RENDERED_HTML_CHARS = 500_000

_IGNORED_TAGS = frozenset(
    {
        "head",
        "script",
        "style",
        "noscript",
        "template",
        "canvas",
        "iframe",
        "object",
    }
)
_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "dl",
        "dt",
        "dd",
        "figcaption",
        "figure",
        "footer",
        "header",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "ul",
    }
)
_HEADING_LEVELS = {f"h{level}": level for level in range(1, 7)}


def normalize_html_text(value: str) -> str:
    """Normalize visible HTML text without introducing source wording."""

    value = value.replace("\u200b", "").replace("\ufeff", "").replace("\x00", "")
    value = unicodedata.normalize("NFC", value)
    return re.sub(r"\s+", " ", value).strip()


def decode_html_bytes(html_bytes: bytes) -> str:
    """Require bounded canonical UTF-8 bytes for reproducible extraction."""

    if not html_bytes:
        raise ValueError("HTML source must not be empty")
    if len(html_bytes) > MAX_HTML_BYTES:
        raise ValueError("HTML source exceeds the 4 MiB extraction limit")
    try:
        decoded = html_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("HTML source must be canonical UTF-8") from exc
    if decoded.encode("utf-8") != html_bytes:
        raise ValueError("HTML source must be canonical UTF-8 without a byte-order mark")
    return decoded


def _hidden_attributes(attrs: list[tuple[str, str | None]]) -> bool:
    values = {name.casefold(): (value or "").casefold() for name, value in attrs}
    style = re.sub(r"\s+", "", values.get("style", ""))
    return (
        "hidden" in values
        or values.get("aria-hidden") == "true"
        or "display:none" in style
        or "visibility:hidden" in style
    )


class _VisibleHtmlParser(HTMLParser):
    """Small deterministic projection of visible text, headings, and tables."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._parts: list[str] = []
        self._title_parts: list[str] = []
        self._title_depth = 0
        self._heading_level: int | None = None
        self._heading_parts: list[str] = []
        self._row_cells: list[str] | None = None
        self._cell_parts: list[str] | None = None
        self._table_row_index: int = 0
        self._open_tags: list[tuple[str, bool]] = []
        self._ignored_depth = 0

    @property
    def title(self) -> str:
        return normalize_html_text("".join(self._title_parts))

    def _append(self, value: str) -> None:
        if self._heading_level is not None:
            self._heading_parts.append(value)
        elif self._cell_parts is not None:
            self._cell_parts.append(value)
        elif self._ignored_depth == 0:
            self._parts.append(value)

    def _flush(self) -> None:
        text = normalize_html_text("".join(self._parts))
        self._parts = []
        if text:
            self.lines.append(text)

    def _close_tag(self, tag: str) -> bool:
        for index in range(len(self._open_tags) - 1, -1, -1):
            if self._open_tags[index][0] == tag:
                removed = self._open_tags[index:]
                del self._open_tags[index:]
                hidden = sum(1 for _tag, ignored in removed if ignored)
                self._ignored_depth = max(0, self._ignored_depth - hidden)
                return True
        return False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag == "title":
            self._title_depth += 1
            self._open_tags.append((tag, False))
            return
        ignored = self._ignored_depth > 0 or tag in _IGNORED_TAGS or _hidden_attributes(attrs)
        self._open_tags.append((tag, ignored))
        if ignored:
            self._ignored_depth += 1
            return
        if tag in _HEADING_LEVELS:
            self._flush()
            self._heading_level = _HEADING_LEVELS[tag]
            self._heading_parts = []
        elif tag == "table":
            self._flush()
            self._table_row_index = 0
        elif tag == "tr":
            self._flush()
            self._row_cells = []
        elif tag in {"td", "th"} and self._row_cells is not None:
            self._cell_parts = []
        elif tag == "li":
            self._flush()
            self._parts.append("- ")
        elif tag == "br" or tag in _BLOCK_TAGS:
            self._flush()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def _finish_table_row(self) -> None:
        if self._row_cells is None:
            return
        if self._cell_parts is not None:
            self._row_cells.append(normalize_html_text("".join(self._cell_parts)))
            self._cell_parts = None
        if any(self._row_cells):
            self.lines.append("| " + " | ".join(self._row_cells) + " |")
            if self._table_row_index == 0:
                self.lines.append("| " + " | ".join(["---"] * len(self._row_cells)) + " |")
            self._table_row_index += 1
        self._row_cells = None

    def _finish_heading(self) -> None:
        if self._heading_level is None:
            return
        heading = normalize_html_text("".join(self._heading_parts))
        if heading:
            self.lines.append("#" * self._heading_level + " " + heading)
        self._heading_level = None
        self._heading_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "title":
            self._title_depth = max(0, self._title_depth - 1)
            self._close_tag(tag)
            return
        if not self._close_tag(tag) or self._ignored_depth:
            return
        if tag in _HEADING_LEVELS:
            self._finish_heading()
        elif tag in {"td", "th"} and self._cell_parts is not None:
            cell = normalize_html_text("".join(self._cell_parts))
            self._cell_parts = None
            if self._row_cells is not None:
                self._row_cells.append(cell)
        elif tag == "tr":
            self._finish_table_row()
        elif tag in _BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._title_depth:
            self._title_parts.append(data)
        elif self._ignored_depth == 0:
            self._append(data)


def extract_html_document(html_bytes: bytes) -> tuple[str, str]:
    """Return the visible source title and canonical Markdown projection."""

    parser = _VisibleHtmlParser()
    parser.feed(decode_html_bytes(html_bytes))
    parser.close()
    parser._flush()
    if parser._heading_level is not None:
        heading = normalize_html_text("".join(parser._heading_parts))
        if heading:
            parser.lines.append("#" * parser._heading_level + " " + heading)
    body = "\n\n".join(line for line in parser.lines if line).strip()
    title = parser.title
    if not title:
        for line in parser.lines:
            if line.startswith("# "):
                title = normalize_html_text(line[2:])
                break
    if not title or not body:
        raise ValueError("HTML source has no visible title or body text")
    rendered = f"# {title}\n\n{body}\n"
    if len(rendered) > MAX_RENDERED_HTML_CHARS:
        raise ValueError("HTML source rendered text exceeds the 500000-character limit")
    return title, rendered


def extract_html_markdown(html_bytes: bytes) -> str:
    """Render one deterministic Markdown view from exact HTML bytes."""

    return extract_html_document(html_bytes)[1]
