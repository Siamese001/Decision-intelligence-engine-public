"""Visible document extraction, sentence segmentation, and scoring."""
from __future__ import annotations

import html
from typing import Any

from dual_pipeline.core import normalize_space, slug

from .constants import (
    _SENTENCE_END,
    _STOP_WORDS,
    _WORD,
    MAX_QUOTE_CHARACTERS,
)
from .models import (
    Heading,
    Section,
    SourceSpanError,
    VisibleDocument,
    _VisibleStructureExtractor,
)


def decode_response(raw: bytes, content_type: str | None) -> str:
    """Decode a capture deterministically, respecting an explicit charset when present."""
    charset = None
    if content_type and "charset=" in content_type.casefold():
        charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip()
    for encoding in (charset, "utf-8", "utf-8-sig"):
        if not encoding:
            continue
        try:
            return raw.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _located_headings(visible_text: str, records: list[dict[str, Any]]) -> tuple[Heading, ...]:
    cursor = 0
    stack: list[Heading] = []
    headings: list[Heading] = []
    for record in records:
        text = record["text"]
        start = visible_text.find(text, cursor)
        if start < 0:
            raise SourceSpanError(
                "captured heading text cannot be located in canonical visible text"
            )
        end = start + len(text)
        cursor = end
        level = record["level"]
        while stack and stack[-1].level >= level:
            stack.pop()
        anchor = record.get("anchor") or slug(text, fallback="section")
        heading = Heading(
            level=level,
            text=text,
            anchor=anchor,
            visible_start_offset=start,
            visible_end_offset=end,
            path=tuple([*stack]),
        )
        headings.append(heading)
        stack.append(heading)
    return tuple(headings)


def extract_visible_document(raw: bytes, content_type: str | None) -> VisibleDocument:
    """Extract capture-compatible canonical visible text and structural headings."""
    decoded = decode_response(raw, content_type)
    if content_type and "html" in content_type.casefold():
        parser = _VisibleStructureExtractor()
        parser.feed(decoded)
        parser.close()
        visible_text = parser.visible_text()
        return VisibleDocument(
            visible_text, _located_headings(visible_text, parser.heading_records())
        )
    return VisibleDocument(normalize_space(html.unescape(decoded)), ())


def sections_for_evidence(
    document: VisibleDocument, evidence: dict[str, Any]
) -> tuple[list[Section], bool, bool]:
    """Resolve a historic locator to current structural headings."""
    heading_text = evidence.get("heading")
    if not isinstance(heading_text, str) or not normalize_space(heading_text):
        raise SourceSpanError("evidence record has no usable heading")
    expected = normalize_space(heading_text).casefold()
    matched_indices = [
        index
        for index, heading in enumerate(document.headings)
        if normalize_space(heading.text).casefold() == expected
    ]
    historical_heading_match = bool(matched_indices)
    anchor_hint = evidence.get("anchor_hint")
    anchor_match = False
    if not matched_indices:
        matched_indices = [
            index
            for index, heading in enumerate(document.headings)
            if _anchor_hint_matches(anchor_hint, heading)
        ]
        anchor_match = bool(matched_indices)
    if not matched_indices:
        if not document.headings:
            evidence_id = evidence.get("evidence_id")
            raise SourceSpanError(
                f"fresh capture has no structural headings for evidence {evidence_id}"
            )
        matched_indices = list(range(len(document.headings)))
    sections: list[Section] = []
    for index in matched_indices:
        heading = document.headings[index]
        end = len(document.visible_text)
        for later in document.headings[index + 1 :]:
            if later.level <= heading.level:
                end = later.visible_start_offset
                break
        if end <= heading.visible_start_offset:
            raise SourceSpanError("heading-scoped section has no visible text")
        sections.append(Section(heading, heading.visible_start_offset, end))
    return sections, historical_heading_match, anchor_match


def _terms(*values: object) -> set[str]:
    terms: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        for term in _WORD.findall(value.casefold()):
            if len(term) > 1 and term not in _STOP_WORDS:
                terms.add(term)
    return terms


def _sentence_spans(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """Return nonempty sentence-like spans inside an already canonical text range."""
    section_text = text[start:end]
    sentences: list[tuple[int, int]] = []
    cursor = 0
    for match in _SENTENCE_END.finditer(section_text):
        sentence_end = match.end()
        left = cursor
        while left < sentence_end and section_text[left].isspace():
            left += 1
        right = sentence_end
        while right > left and section_text[right - 1].isspace():
            right -= 1
        if right > left:
            sentences.append((start + left, start + right))
        cursor = sentence_end
    left = cursor
    while left < len(section_text) and section_text[left].isspace():
        left += 1
    if left < len(section_text):
        sentences.append((start + left, end))
    if not sentences and end > start:
        sentences.append((start, end))
    return sentences


def _candidate_spans(
    document: VisibleDocument, sections: list[Section], preview: str | None
) -> list[tuple[int, int, Section, bool]]:
    """Generate bounded sentence windows plus exact historic-preview locations."""
    candidates: dict[tuple[int, int], tuple[Section, bool]] = {}
    for section in sections:
        sentences = _sentence_spans(
            document.visible_text, section.visible_start_offset, section.visible_end_offset
        )
        for count in range(1, min(3, len(sentences)) + 1):
            for index in range(0, len(sentences) - count + 1):
                start = sentences[index][0]
                end = sentences[index + count - 1][1]
                if end - start <= MAX_QUOTE_CHARACTERS:
                    candidates[(start, end)] = (section, False)
        if preview:
            preview_start = document.visible_text.find(
                preview, section.visible_start_offset, section.visible_end_offset
            )
            while preview_start >= 0:
                preview_end = preview_start + len(preview)
                candidates[(preview_start, preview_end)] = (section, True)
                preview_start = document.visible_text.find(
                    preview, preview_end, section.visible_end_offset
                )
    return [
        (start, end, section, exact_preview)
        for (start, end), (section, exact_preview) in candidates.items()
    ]


def _heading_path(heading: Heading) -> list[dict[str, object]]:
    return [
        {"level": item.level, "text": item.text, "anchor": item.anchor}
        for item in (*heading.path, heading)
    ]


def _score_span(
    quote: str,
    fact: dict[str, Any],
    preview: str | None,
    historical_heading: str,
    selected_heading: str,
    exact_preview: bool,
) -> tuple[int, list[str], list[str]]:
    fact_terms = _terms(
        fact.get("assertion_text"),
        fact.get("embedding_text"),
        fact.get("subject"),
        fact.get("predicate"),
        fact.get("object"),
    )
    quote_terms = _terms(quote)
    matched = sorted(fact_terms & quote_terms)
    fact_coverage = (len(matched) * 10_000) // max(1, len(fact_terms))
    preview_terms = _terms(preview)
    preview_coverage = (len(preview_terms & quote_terms) * 1_000) // max(1, len(preview_terms))
    locator_terms = _terms(historical_heading)
    selected_heading_terms = _terms(selected_heading)
    locator_matched = sorted(locator_terms & (quote_terms | selected_heading_terms))
    locator_coverage = (len(locator_matched) * 500) // max(1, len(locator_terms))
    exact_bonus = 100_000 if exact_preview else 0
    return (
        exact_bonus + fact_coverage + preview_coverage + locator_coverage + len(matched),
        matched,
        locator_matched,
    )


def _anchor_hint_matches(anchor_hint: object, heading: Heading) -> bool:
    if not isinstance(anchor_hint, str) or not anchor_hint.strip():
        return False
    normalized_hint = slug(anchor_hint, fallback="section")
    return normalized_hint == slug(heading.anchor, fallback="section")
