"""Deterministic, source-bound DOCX text extraction for governed intake."""
from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from xml.etree import ElementTree as ET

_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_W = f"{{{_WORD_NS}}}"
_M = f"{{{_MATH_NS}}}"
_MC_FALLBACK = f"{{{_MC_NS}}}Fallback"
_MAX_DOCUMENT_XML_BYTES = 8 * 1024 * 1024


def _text(element: ET.Element) -> str:
    """Return the visible Word and Math text with deterministic whitespace, skipping mc:Fallback."""
    pieces: list[str] = []

    def walk(node: ET.Element) -> None:
        if node.tag == _MC_FALLBACK:
            return
        if node.tag in {f"{_W}t", f"{_M}t"} and node.text:
            pieces.append(node.text)
        elif node.tag in {f"{_W}tab", f"{_W}br", f"{_W}cr"}:
            pieces.append(" ")
        for child in node:
            walk(child)

    walk(element)
    rendered = "".join(pieces).replace("\u200b", "").replace("\ufeff", "")
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", rendered)).strip()


def _style_id(paragraph: ET.Element) -> str:
    style = paragraph.find(f"./{_W}pPr/{_W}pStyle")
    return "" if style is None else style.get(f"{_W}val", "")


def _heading_level(paragraph: ET.Element, text: str, *, first_text: bool) -> int | None:
    """Use Word heading styles, with a conservative fallback for plain DOCX."""

    style = _style_id(paragraph).replace(" ", "").lower()
    match = re.fullmatch(r"heading([1-6])", style)
    if match:
        level = int(match.group(1))
        # The first visible paragraph is the source title.  Preserve Word
        # heading hierarchy beneath that canonical title.
        return level if first_text else min(level + 1, 6)
    if first_text:
        return 1
    # Some proposal templates use unstyled short labels.  Only promote labels
    # that cannot plausibly be a sentence or list item.
    label = text.rstrip(":").strip()
    words = re.findall(r"[A-Za-z][A-Za-z0-9'’/-]*", label)
    if (
        len(label) <= 72
        and not text.endswith((".", "?", "!", ";", ","))
        and 0 < len(words) <= 5
        and all(
            word.lower() in {"and", "or", "of", "for", "to", "the"}
            or word[0].isupper()
            for word in words
        )
    ):
        return 2
    return None


def _is_list_item(paragraph: ET.Element) -> bool:
    style = _style_id(paragraph).lower()
    return paragraph.find(f"./{_W}pPr/{_W}numPr") is not None or "list" in style


def _parse_cell_with_spans(cell: ET.Element, col_idx: int, prev_row_cells: list[str]) -> list[str]:
    cell_text = _text(cell)
    tc_pr = cell.find(f"./{_W}tcPr")
    if tc_pr is None:
        return [cell_text]
    v_merge = tc_pr.find(f"./{_W}vMerge")
    if v_merge is not None and v_merge.get(f"{_W}val") in (None, "continue") and not cell_text and col_idx < len(prev_row_cells):
        cell_text = prev_row_cells[col_idx]
    grid_span_el = tc_pr.find(f"./{_W}gridSpan")
    try:
        span = max(1, int(grid_span_el.get(f"{_W}val", "1"))) if grid_span_el is not None else 1
    except ValueError:
        span = 1
    return [cell_text] + [""] * (span - 1)


def _extract_row_cells(row: ET.Element, prev_row_cells: list[str]) -> list[str]:
    cells: list[str] = []
    for cell in row.findall(f"./{_W}tc"):
        cells.extend(_parse_cell_with_spans(cell, len(cells), prev_row_cells))
    return cells


def _table_lines(table: ET.Element) -> list[str]:
    rows: list[list[str]] = []
    prev_row_cells: list[str] = []
    for row in table.findall(f"./{_W}tr"):
        cells = _extract_row_cells(row, prev_row_cells)
        if any(cells):
            rows.append(cells)
            prev_row_cells = list(cells)
    if not rows:
        return []
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    if len(normalized) == 1:
        return [" | ".join(normalized[0])]
    header = normalized[0]
    return [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
        *["| " + " | ".join(row) + " |" for row in normalized[1:]],
    ]


def extract_docx_core_metadata(docx_bytes: bytes) -> dict[str, str]:
    """Extract author, title, and timestamps from docProps/core.xml when present."""
    if not docx_bytes:
        return {}
    meta: dict[str, str] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as archive:
            if "docProps/core.xml" not in archive.namelist():
                return {}
            core_xml = archive.read("docProps/core.xml")
        root = ET.fromstring(core_xml)
        for elem in root.iter():
            local_name = elem.tag.split("}", 1)[-1] if "}" in elem.tag else elem.tag
            if local_name in {"title", "creator", "lastModifiedBy", "created", "modified", "subject"} and elem.text:
                val = elem.text.strip()
                if val:
                    meta[local_name] = val
    except Exception:
        return {}
    return meta


def _format_docx_paragraph(child: ET.Element, first_text: bool) -> str | None:
    text = _text(child)
    if not text:
        return None
    list_item = _is_list_item(child)
    level = None if list_item and not first_text else _heading_level(child, text, first_text=first_text)
    if level is not None:
        return "#" * level + " " + text.rstrip(":").strip()
    return ("- " + text) if list_item else text


def _walk_docx_blocks(container: ET.Element, lines: list[str], state: list[bool]) -> None:
    for child in container:
        if child.tag == _MC_FALLBACK:
            continue
        if child.tag == f"{_W}p":
            formatted = _format_docx_paragraph(child, state[0])
            if formatted is not None:
                lines.append(formatted)
                state[0] = False
        elif child.tag == f"{_W}tbl":
            lines.extend(_table_lines(child))
        else:
            _walk_docx_blocks(child, lines, state)


def extract_docx_markdown(docx_bytes: bytes) -> str:
    """Convert one DOCX byte stream to canonical retrieval Markdown.

    The source package retains the original DOCX bytes separately.  This
    function is deliberately dependency-free and only emits visible paragraph
    and table content in document order.
    """

    if not docx_bytes:
        raise ValueError("DOCX source must not be empty")
    try:
        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as archive:
            try:
                info = archive.getinfo("word/document.xml")
            except KeyError as exc:
                raise ValueError("DOCX source has no word/document.xml") from exc
            if info.file_size > _MAX_DOCUMENT_XML_BYTES:
                raise ValueError("DOCX word/document.xml exceeds extraction limit")
            document_xml = archive.read(info)
    except zipfile.BadZipFile as exc:
        raise ValueError("source is not a valid DOCX zip package") from exc
    try:
        root = ET.fromstring(document_xml)
    except ET.ParseError as exc:
        raise ValueError("DOCX word/document.xml is malformed") from exc
    body = root.find(f"./{_W}body")
    if body is None:
        raise ValueError("DOCX source has no document body")

    lines: list[str] = []
    _walk_docx_blocks(body, lines, [True])

    if not lines:
        raise ValueError("DOCX source has no visible paragraph or table text")
    return "\n\n".join(lines).strip() + "\n"
