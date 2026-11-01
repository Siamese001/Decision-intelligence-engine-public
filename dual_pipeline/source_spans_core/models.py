"""Data structures and parser for visible document extraction."""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from dual_pipeline.core import normalize_space
from dual_pipeline.exceptions import DualPipelineValueError

from .constants import _HEADING_TAG, _HIDDEN_TAGS


class SourceSpanError(DualPipelineValueError):
    """Raised when a capture or a proposed span cannot be cryptographically bound."""


@dataclass(frozen=True)
class Heading:
    """A heading located in canonical visible text."""

    level: int
    text: str
    anchor: str
    visible_start_offset: int
    visible_end_offset: int
    path: tuple["Heading", ...]


@dataclass(frozen=True)
class Section:
    """A heading-scoped range in canonical visible text."""

    heading: Heading
    visible_start_offset: int
    visible_end_offset: int


@dataclass(frozen=True)
class VisibleDocument:
    """Canonical visible text and its source-order heading hierarchy."""

    visible_text: str
    headings: tuple[Heading, ...]


class _VisibleStructureExtractor(HTMLParser):
    """Match the capture extractor's visible text while retaining heading metadata."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self._parts: list[str] = []
        self._headings: list[dict[str, Any]] = []
        self._active_heading: dict[str, Any] | None = None
        self._recent_anchor: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag in _HIDDEN_TAGS:
            self._hidden_depth += 1
            return
        if self._hidden_depth:
            return
        attributes = {key.casefold(): value for key, value in attrs}
        anchor = attributes.get("id")
        if isinstance(anchor, str) and anchor.strip():
            self._recent_anchor = anchor.strip()
        match = _HEADING_TAG.fullmatch(normalized_tag)
        if match is None:
            return
        if self._active_heading is not None:
            raise SourceSpanError("nested heading elements are not supported")
        self._active_heading = {
            "level": int(match.group(1)),
            "anchor": anchor.strip() if isinstance(anchor, str) and anchor.strip() else None,
            "parts": [],
        }

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.casefold()
        if normalized_tag in _HIDDEN_TAGS:
            self._hidden_depth = max(0, self._hidden_depth - 1)
            return
        if self._hidden_depth:
            return
        match = _HEADING_TAG.fullmatch(normalized_tag)
        if match is None or self._active_heading is None:
            return
        text = normalize_space(" ".join(self._active_heading["parts"]))
        if text:
            self._headings.append(
                {
                    "level": self._active_heading["level"],
                    "text": text,
                    "anchor": self._active_heading["anchor"] or self._recent_anchor,
                }
            )
        self._active_heading = None

    def handle_data(self, data: str) -> None:
        if self._hidden_depth or not data.strip():
            return
        self._parts.append(data)
        if self._active_heading is not None:
            self._active_heading["parts"].append(data)

    def visible_text(self) -> str:
        return normalize_space(" ".join(self._parts))

    def heading_records(self) -> list[dict[str, Any]]:
        if self._active_heading is not None:
            raise SourceSpanError("unclosed heading element in captured HTML")
        return list(self._headings)
