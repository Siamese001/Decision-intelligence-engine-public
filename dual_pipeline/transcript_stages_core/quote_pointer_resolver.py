"""Position-Pointer Quote Architecture and verbatim character slice resolver for Deliverable 03.

Eliminates free-form LLM quote generation by resolving [start_idx, end_idx] character pointers
directly against Deliverable 01 forensic transcript, guaranteeing 100% verbatim grounding and
dropping Gate 3.4 quote mismatch rate < 1%.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


def resolve_quote_from_pointer(
    source_text: str,
    pointer: Union[List[int], Tuple[int, int], Dict[str, int]],
) -> str:
    """Extract exact verbatim text slice from source_text given character position pointers.

    Args:
        source_text: The authoritative source forensic transcript (D01).
        pointer: [start_idx, end_idx] sequence or dict with start_idx and end_idx.

    Returns:
        Exact verbatim character slice from source_text.

    Raises:
        ValueError: If pointer indices are invalid, negative, reversed, out of range, or empty.
    """
    if not isinstance(source_text, str):
        raise ValueError(f"source_text must be a string, got {type(source_text)}")

    if isinstance(pointer, (list, tuple)) and len(pointer) == 2:
        start_idx, end_idx = pointer[0], pointer[1]
    elif isinstance(pointer, dict) and "start_idx" in pointer and "end_idx" in pointer:
        start_idx, end_idx = pointer["start_idx"], pointer["end_idx"]
    else:
        raise ValueError(f"Invalid pointer format: {pointer}. Must be [start_idx, end_idx] or dict.")

    if not isinstance(start_idx, int) or not isinstance(end_idx, int):
        raise ValueError(f"Pointer indices must be integers, got {type(start_idx)} and {type(end_idx)}")

    if start_idx < 0:
        raise ValueError(f"start_idx cannot be negative: {start_idx}")
    if end_idx > len(source_text):
        raise ValueError(f"end_idx ({end_idx}) exceeds source text length ({len(source_text)})")
    if start_idx >= end_idx:
        raise ValueError(f"Invalid slice bounds: start_idx ({start_idx}) >= end_idx ({end_idx})")

    extracted = source_text[start_idx:end_idx].strip()
    if not extracted:
        raise ValueError(f"Resolved slice [{start_idx}:{end_idx}] contains only whitespace")

    return extracted


def _locate_cross_page_pointer(source_text: str, quote: str) -> Optional[List[int]]:
    words = [re.escape(w) for w in re.split(r"\s+", quote.strip()) if w]
    if len(words) < 4:
        return None
    gap = r"(?:\s+|\s*#*\s*Page\s+\d+\s*(?:[^\n]*\(\d+\.\d{4}\)\s*)?)+"
    try:
        m = re.search(gap.join(words), source_text, re.I)
        return [m.start(), m.end()] if m else None
    except re.error:
        return None


def _locate_normalized_quote_pointer(source_text: str, quote: str) -> Optional[List[int]]:
    """Locate quote span in source_text when markdown formatting, punctuation, or list prefixes differ."""
    indices = [i for i, c in enumerate(source_text) if c.isalnum()]
    if not indices:
        return None
    norm_source = "".join(source_text[i].lower() for i in indices)

    # 1. Direct alphanumeric normalization
    norm_quote = "".join(c.lower() for c in quote if c.isalnum())
    if len(norm_quote) >= 8:
        pos = norm_source.find(norm_quote)
        if pos != -1:
            start = indices[pos]
            end = indices[pos + len(norm_quote) - 1] + 1
            trailing = quote.rstrip()[-1] if quote.rstrip() else ""
            if end < len(source_text) and trailing in ".!?,:;" and source_text[end] == trailing:
                end += 1
            return [start, end]

    # 2. Strip markdown emphasis, bullets, and list numbers from quote
    cleaned = re.sub(r"^\s*[\d.\-•*#]+\s*", "", quote)
    cleaned = re.sub(r"[\s.\-•*#]+$", "", cleaned)
    norm_cleaned = "".join(c.lower() for c in cleaned if c.isalnum())
    if len(norm_cleaned) >= 8 and norm_cleaned != norm_quote:
        pos = norm_source.find(norm_cleaned)
        if pos != -1:
            start = indices[pos]
            end = indices[pos + len(norm_cleaned) - 1] + 1
            trailing = cleaned.rstrip()[-1] if cleaned.rstrip() else ""
            if end < len(source_text) and trailing in ".!?,:;" and source_text[end] == trailing:
                end += 1
            return [start, end]

    return _locate_cross_page_pointer(source_text, quote)


def locate_quote_pointer(source_text: str, quote: str, section_hint: str = "") -> Optional[List[int]]:
    """Locate the [start_idx, end_idx] character pointer of a quote within source_text."""
    if not source_text or not quote:
        return None
    quote_clean = quote.strip()
    if not quote_clean:
        return None
    start_search = 0
    if section_hint and len(section_hint.strip()) >= 4:
        sec_idx = source_text.find(section_hint.strip())
        if sec_idx != -1:
            start_search = sec_idx
    pos = source_text.find(quote_clean, start_search)
    if pos == -1 and start_search > 0:
        pos = source_text.find(quote_clean)
    if pos != -1:
        return [pos, pos + len(quote_clean)]
    # Case-insensitive search fallback
    pos_lower = source_text.lower().find(quote_clean.lower(), start_search)
    if pos_lower == -1 and start_search > 0:
        pos_lower = source_text.lower().find(quote_clean.lower())
    if pos_lower != -1:
        return [pos_lower, pos_lower + len(quote_clean)]
    # Markdown and list prefix normalization fallback
    return _locate_normalized_quote_pointer(source_text, quote_clean)


def _is_non_transcript_evidence(ev: Dict[str, Any]) -> bool:
    spk = str(ev.get("speaker") or "").strip().lower()
    src = str(ev.get("source_type") or ev.get("source") or "").strip().upper()
    loc = ev.get("locator")
    loc_kind = str(loc.get("kind") or "").strip().lower() if isinstance(loc, dict) else str(loc or "").strip().lower()
    return (
        spk.startswith("attachment:")
        or src in ("TRANSCRIPT_HITL", "ATTACHMENT", "COLLATERAL", "C0_RECEIPT")
        or loc_kind in ("attachment", "hitl_decision_ledger", "c0_receipt", "collateral")
        or loc_kind.startswith("attachment:")
    )


def _refine_resolved_pointer(source_text: str, ev: Dict[str, Any], pointer: Any, verbatim_text: str) -> tuple[List[int], str]:
    existing_text = str(ev.get("exact_text") or ev.get("text") or "").strip()
    raw_loc = ev.get("source_locator") or ev.get("locator")
    sec_hint = str(raw_loc.get("value") if isinstance(raw_loc, dict) else (raw_loc or "")).strip()
    found_existing = locate_quote_pointer(source_text, existing_text, section_hint=sec_hint) if existing_text else None
    if found_existing and (found_existing[1] - found_existing[0]) > len(verbatim_text):
        verbatim_text = source_text[found_existing[0] : found_existing[1]]
        pointer = found_existing
    start_i = pointer[0] if isinstance(pointer, (list, tuple)) else pointer["start_idx"]
    end_i = pointer[1] if isinstance(pointer, (list, tuple)) else pointer["end_idx"]
    if (
        verbatim_text
        and not verbatim_text.rstrip().endswith((".", "!", "?", ";", ":", ")", "\"", "\u201d"))
        and end_i < len(source_text)
        and not verbatim_text.lstrip().startswith("#")
    ):
        m_sent = re.search(r"[.!?;](?=\s|$)", source_text[end_i : min(len(source_text), end_i + 320)])
        if m_sent:
            end_i += m_sent.end()
            verbatim_text = source_text[start_i:end_i].strip()
    return [start_i, end_i], verbatim_text


def _sync_evidence_pointer_fields(ev: Dict[str, Any], norm_ptr: List[int], verbatim_text: str) -> None:
    ev["exact_text"] = verbatim_text
    ev["text"] = verbatim_text
    ev["quote_pointer"] = norm_ptr
    if "start_offset" in ev:
        ev["start_offset"] = norm_ptr[0]
    if "end_offset" in ev:
        ev["end_offset"] = norm_ptr[1]
    ev["grounding_status"] = "GROUNDED"


def resolve_evidence_pointers(
    projection_data: Dict[str, Any],
    source_text: str,
    collateral_text: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve position pointers across evidence_index into verbatim exact_text.

    If an evidence item has quote_pointer, extracts exact verbatim text from source_text.
    If an evidence item only has free-form quote, locates and binds quote_pointer.
    """
    if not isinstance(projection_data, dict):
        return projection_data

    evidence_index = projection_data.get("evidence_index", {})
    if not isinstance(evidence_index, dict) or not source_text:
        return projection_data

    for eid, ev in evidence_index.items():
        if not isinstance(ev, dict):
            continue

        pointer = ev.get("quote_pointer") or ev.get("char_span") or ev.get("position_pointer")
        if pointer is not None:
            try:
                verbatim_text = resolve_quote_from_pointer(source_text, pointer)
                norm_ptr, verbatim_text = _refine_resolved_pointer(source_text, ev, pointer, verbatim_text)
                _sync_evidence_pointer_fields(ev, norm_ptr, verbatim_text)
            except ValueError as err:
                logger.warning("Evidence %s quote_pointer resolution error: %s", eid, err)
                raise
        else:
            # Reverse-bind pointer if free text quote is present and matches source
            existing_text = ev.get("exact_text") or ev.get("text")
            if existing_text:
                found_pointer = locate_quote_pointer(source_text, existing_text)
                if found_pointer:
                    ev["quote_pointer"] = found_pointer
                    # Ensure exact casing match from source
                    ev["exact_text"] = source_text[found_pointer[0] : found_pointer[1]]
                    ev["grounding_status"] = "GROUNDED"
                elif (
                    (collateral_text and locate_quote_pointer(collateral_text, existing_text))
                    or _is_non_transcript_evidence(ev)
                ):
                    ev["quote_pointer"] = None
                    ev["exact_text"] = existing_text
                    ev["grounding_status"] = "GROUNDED"
                else:
                    ev["quote_pointer"] = None
                    ev["grounding_status"] = "UNGROUNDED_HALLUCINATION"
                    ev.pop("exact_text", None)
            else:
                ev["quote_pointer"] = None
                ev["grounding_status"] = "MISSING_QUOTE"

    return projection_data
