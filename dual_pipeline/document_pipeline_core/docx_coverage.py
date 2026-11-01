"""DOCX body extraction with cell locators and exact XML-text coverage accounting."""
from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from collections import Counter
import zipfile
from typing import Any
from xml.etree import ElementTree as ET

from dual_pipeline.docx_source import (
    _M, _MAX_DOCUMENT_XML_BYTES, _MC_FALLBACK, _W, _heading_level, _is_list_item,
)


def _iter_active_nodes(element: ET.Element):
    """Iterate XML subtree while skipping <mc:Fallback> inside <mc:AlternateContent>."""
    if element.tag == _MC_FALLBACK:
        return
    yield element
    for child in element:
        if child.tag != _MC_FALLBACK:
            yield from _iter_active_nodes(child)


def _iter_active_text_nodes(element: ET.Element):
    """Yield visible Word (<w:t>) and Office Math (<m:t>) text nodes outside <mc:Fallback>."""
    for node in _iter_active_nodes(element):
        if node.tag in {f"{_W}t", f"{_M}t"}:
            yield node


def _document_body(raw_bytes: bytes) -> ET.Element:
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > _MAX_DOCUMENT_XML_BYTES:
                raise ValueError("DOCX word/document.xml exceeds extraction limit")
            root = ET.fromstring(archive.read(info))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        raise ValueError("DOCX has no valid word/document.xml") from exc
    body = root.find(f"./{_W}body")
    if body is None:
        raise ValueError("DOCX source has no document body")
    return body


def _paragraph_text(element: ET.Element) -> str:
    """Separate nested text-box paragraphs while joining runs within a paragraph."""
    pieces: list[str] = []

    def walk(node: ET.Element) -> None:
        if node.tag == _MC_FALLBACK:
            return
        if node.tag in {f"{_W}t", f"{_M}t"}:
            pieces.append(node.text or "")
            return
        boundary = node.tag in {f"{_W}p", f"{_W}tbl", f"{_W}tr", f"{_W}tc"}
        if boundary or node.tag in {f"{_W}tab", f"{_W}br", f"{_W}cr"}:
            pieces.append(" ")
        for child in node:
            walk(child)
        if boundary:
            pieces.append(" ")

    walk(element)
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", "".join(pieces))).strip()


class _BodyRenderer:
    def __init__(self) -> None:
        self.consumed: Counter[int] = Counter()
        self.units: list[dict[str, Any]] = []
        self.paragraphs: list[dict[str, Any]] = []
        self.table_count = 0
        self.row_count = 0
        self.cell_count = 0
        self.paragraph_count = 0
        self.first_text = True

    def _paragraph(self, paragraph: ET.Element, in_cell: bool) -> str:
        self.consumed.update(id(node) for node in _iter_active_text_nodes(paragraph))
        self.paragraph_count += sum(1 for n in _iter_active_nodes(paragraph) if n.tag == f"{_W}p")
        text = _paragraph_text(paragraph)
        if not text:
            return ""
        if in_cell:
            return text
        is_list = _is_list_item(paragraph)
        level = None if is_list and not self.first_text else _heading_level(
            paragraph, text, first_text=self.first_text
        )
        self.first_text = False
        formatted = "#" * level + " " + text if level is not None else ("- " + text if is_list else text)
        p_idx = len(self.paragraphs) + 1
        self.paragraphs.append({
            "locator": f"p{p_idx}",
            "text": formatted,
            "plain_text": text,
            "is_list": is_list,
            "heading_level": level,
        })
        return formatted

    def _cell(self, cell: ET.Element, locator: str) -> str:
        parts = self.blocks(cell, in_cell=True)
        text = " / ".join(parts)
        self.cell_count += 1
        self.units.append({
            "locator": {"kind": "cell", "value": locator},
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "char_count": len(text),
            "text": text,
        })
        return f"[{locator}] {text} [/{locator}]"

    def _table(self, table: ET.Element) -> list[str]:
        self.table_count += 1
        table_id = self.table_count
        lines = [f"### Table {table_id}"]
        rows = list(_children_of_kind(table, "tr"))
        for row_index, row in enumerate(rows, start=1):
            self.row_count += 1
            cells = list(_children_of_kind(row, "tc"))
            values = [self._cell(cell, f"T{table_id}.R{row_index}.C{column}")
                      for column, cell in enumerate(cells, start=1)]
            lines.append(" | ".join(values))
        return lines

    def blocks(self, element: ET.Element, in_cell: bool = False) -> list[str]:
        lines: list[str] = []
        for child in element:
            if child.tag == _MC_FALLBACK:
                continue
            if child.tag == f"{_W}p":
                paragraph = self._paragraph(child, in_cell)
                if paragraph:
                    lines.append(paragraph)
            elif child.tag == f"{_W}tbl":
                lines.extend(self._table(child))
            else:
                lines.extend(self.blocks(child, in_cell=in_cell))
        return lines


def _children_of_kind(element: ET.Element, kind: str):
    """Include content-control wrappers but never descend into nested tables or mc:Fallback."""
    for child in element:
        if child.tag == _MC_FALLBACK:
            continue
        if child.tag == f"{_W}{kind}":
            yield child
        elif child.tag != f"{_W}tbl":
            yield from _children_of_kind(child, kind)


def _classify_ooxml_part(name: str) -> tuple[str, str, str]:
    lower = name.lower()
    if lower == "word/document.xml":
        return "body", "included", "Primary document body text and tables"
    if re.match(r"^word/header\d*\.xml$", lower):
        return "header", "excluded", "Page header content"
    if re.match(r"^word/footer\d*\.xml$", lower):
        return "footer", "excluded", "Page footer content"
    if lower in {"word/footnotes.xml", "word/footnotes"}:
        return "footnotes", "review-required", "Footnotes requiring review for supplementary evidence"
    if lower in {"word/endnotes.xml", "word/endnotes"}:
        return "endnotes", "review-required", "Endnotes requiring review for supplementary evidence"
    if lower in {"word/comments.xml", "word/comments"}:
        return "comments", "review-required", "Editorial comments requiring human review"
    if "drawings/" in lower or re.search(r"drawing\d*\.xml", lower):
        return "drawing", "review-required", "Vector drawing shapes or embedded diagrams"
    if lower.startswith("word/media/"):
        return "media", "excluded", "Embedded image or media asset"
    if lower.startswith("word/embeddings/"):
        return "embedded_object", "review-required", "Embedded external object"
    if lower.endswith(".rels") or lower == "[content_types].xml":
        return "packaging", "excluded", "OOXML package relationship and content-type manifest"
    if lower in {"word/styles.xml", "word/settings.xml", "word/fonttable.xml", "word/websettings.xml"}:
        return "metadata", "excluded", "Document styling and configuration metadata"
    if "theme" in lower:
        return "theme", "excluded", "Document theme metadata"
    return "supplementary", "review-required", f"Unclassified OOXML part: {name}"


def inventory_docx_parts(raw_bytes: bytes) -> list[dict[str, Any]]:
    """Enumerate all OOXML parts in DOCX archive with explicit inclusion/exclusion dispositions."""
    inventory: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as archive:
            for name in archive.namelist():
                info = archive.getinfo(name)
                kind, disposition, notes = _classify_ooxml_part(name)
                inventory.append({
                    "part_name": name,
                    "kind": kind,
                    "disposition": disposition,
                    "size_bytes": info.file_size,
                    "notes": notes,
                })
            # Inspect document body for inline external chunks or text boxes
            try:
                root = ET.fromstring(archive.read("word/document.xml"))
                body = root.find(f"./{_W}body")
                if body is not None:
                    alt_chunks = list(body.iter(f"{_W}altChunk"))
                    if alt_chunks:
                        inventory.append({
                            "part_name": "inline:altChunk",
                            "kind": "external_chunk",
                            "disposition": "review-required",
                            "size_bytes": len(alt_chunks),
                            "notes": f"Found {len(alt_chunks)} external chunk reference(s) in body",
                        })
                    drawings = [n for n in body.iter() if n.tag.endswith("}drawing") or n.tag.endswith("}txbxContent")]
                    if drawings:
                        inventory.append({
                            "part_name": "inline:txbxContent",
                            "kind": "text_box",
                            "disposition": "review-required",
                            "size_bytes": len(drawings),
                            "notes": f"Found {len(drawings)} drawing/text-box structure(s) in body",
                        })
            except Exception:
                pass
    except (zipfile.BadZipFile, KeyError):
        pass
    return inventory


def extract_docx_with_coverage(
    raw_bytes: bytes, allow_review_required: bool = False
) -> tuple[str, dict[str, Any]]:
    """Preserve every visible body text node or reject unsupported structure.

    Physical row/cell coordinates are stable even for empty and merged cells.
    Cell paragraph boundaries are retained with separators; nested tables have
    their own table identifiers. Reports non-body OOXML parts as included,
    excluded, or review-required.
    """
    body = _document_body(raw_bytes)
    part_inventory = inventory_docx_parts(raw_bytes)
    non_body_dispositions = {
        item["part_name"]: item["disposition"]
        for item in part_inventory
        if item["disposition"] != "included"
    }

    if not allow_review_required:
        if any(node.tag == f"{_W}altChunk" for node in _iter_active_nodes(body)):
            raise ValueError("DOCX embedded external text requires a compatible extractor")
        if any(
            node.tag.endswith("}t") and node.tag not in {f"{_W}t", f"{_M}t"} and node.text
            for node in _iter_active_nodes(body)
        ):
            raise ValueError("DOCX drawing text requires a compatible extractor")

    if not any(node.text and node.text.strip() for node in _iter_active_text_nodes(body)):
        raise ValueError("DOCX source has no visible paragraph or table text")

    renderer = _BodyRenderer()
    lines = renderer.blocks(body)
    expected = {id(node) for node in _iter_active_text_nodes(body)}
    if set(renderer.consumed) != expected or any(count != 1 for count in renderer.consumed.values()):
        raise ValueError("DOCX extraction left visible XML text unaccounted for")

    text = "\n\n".join(lines).strip() + "\n"
    if not text.strip():
        raise ValueError("DOCX source has no visible paragraph or table text")

    coverage = {
        "schema_version": "1.0.0",
        "scope": "word/document.xml body",
        "source_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "extracted_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "expected_text_nodes": len(expected),
        "preserved_text_nodes": len(renderer.consumed),
        "paragraph_count": renderer.paragraph_count,
        "table_count": renderer.table_count,
        "row_count": renderer.row_count,
        "cell_count": renderer.cell_count,
        "total_records": len(renderer.paragraphs) + len(renderer.units),
        "normalization": "Unicode NFC; paragraph boundaries and whitespace normalized to spaces",
        "complete": True,
        "paragraphs": renderer.paragraphs,
        "cells": renderer.units,
        "part_inventory": part_inventory,
        "non_body_dispositions": non_body_dispositions,
        "disposition_summary": {
            "included": sum(1 for p in part_inventory if p["disposition"] == "included"),
            "excluded": sum(1 for p in part_inventory if p["disposition"] == "excluded"),
            "review_required": sum(1 for p in part_inventory if p["disposition"] == "review-required"),
        },
    }
    return text, coverage

