"""Schema-bound, non-mutating guards for evidence packaging and source chunks."""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def envelope_field_limits() -> dict[str, int]:
    schema_path = (Path(__file__).resolve().parents[1] / "contracts" /
                   "dual_pipeline/schemas/evidence-envelope-intake.schema.json")
    properties = json.loads(schema_path.read_text(encoding="utf-8"))["properties"]
    statement = properties["speakers"]["items"]["properties"]["statements"]["items"]
    return {
        "statement": statement["properties"]["text"]["maxLength"],
        "action": properties["actions"]["items"]["properties"]["action"]["maxLength"],
        "title": properties["meeting"]["properties"]["title"]["maxLength"],
    }


def _check_length(value: Any, limit: int, path: str) -> None:
    if isinstance(value, str) and len(value) > limit:
        raise ValueError(
            f"Evidence field {path} contains {len(value)} characters; maximum is {limit}. "
            "Preserve the source and regenerate bounded, separately grounded fields; "
            "silent truncation is prohibited."
        )


def _validate_speakers(speakers: list[dict[str, Any]], limit: int) -> None:
    for speaker_index, speaker in enumerate(speakers):
        for statement_index, statement in enumerate(speaker.get("statements", [])):
            _check_length(statement.get("text"), limit,
                          f"speakers[{speaker_index}].statements[{statement_index}].text")


def _validate_evidence(index: dict[str, Any], limit: int) -> None:
    for statement_id, statement in index.items():
        value = next((statement.get(key) for key in (
            "exact_text", "verbatim_quote", "quote", "text", "claim_text"
        ) if statement.get(key)), "")
        _check_length(value, limit, f"evidence_index[{statement_id}]")


def _validate_actions(actions: list[dict[str, Any]], limit: int) -> None:
    for index, action in enumerate(actions):
        value = action.get("action_text") or action.get("description") or action.get("action")
        _check_length(value, limit, f"actions[{index}]")


def validate_projection_field_lengths(payload: dict[str, Any] | str) -> None:
    """Reject oversized generated envelope/projection fields without modifying them.

    Accept the raw JSON object string as well as an already parsed object, so the
    executor and direct judge entry points can share the same pre-provider guard.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("Generated projection must be a valid JSON object") from exc
    if not isinstance(payload, dict):
        raise ValueError("Generated projection must be a JSON object")
    limits = envelope_field_limits()
    try:
        meeting = payload.get("meeting") or {}
        _check_length(payload.get("title") or meeting.get("title") or payload.get("meeting_title"),
                      limits["title"], "title")
        _validate_speakers(payload.get("speakers", []), limits["statement"])
        _validate_evidence(payload.get("evidence_index", {}), limits["statement"])
        _validate_actions(payload.get("actions", []), limits["action"])
        _validate_actions(payload.get("action_updates", []), limits["action"])
        mutations = payload.get("graph_mutations") or {}
        _validate_actions(mutations.get("action_updates", []), limits["action"])
    except (AttributeError, TypeError) as exc:
        raise ValueError("Generated evidence and actions must use object/list schema shapes") from exc


def validate_source_preflight(text: str, metadata: dict[str, Any]) -> dict[str, Any]:
    """Check known source metadata early; the field cap is not a document-size cap."""
    limits = envelope_field_limits()
    _check_length(metadata.get("title") or metadata.get("meeting_name"),
                  limits["title"], "source.title")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Source content must contain visible text before synthesis")
    return {"source_char_count": len(text), "statement_max_chars": limits["statement"],
            "raw_source_chunk_count": (len(text) + limits["statement"] - 1) // limits["statement"]}


def source_text_statements(source_text: str) -> list[dict[str, Any]]:
    """Losslessly segment raw fallback evidence with Unicode character offsets.

    Offsets are zero-based and end-exclusive in the exact preserved source export.
    This helper is for verbatim raw evidence, never for splitting generated claims.
    """
    if not source_text:
        return []
    limit = envelope_field_limits()["statement"]
    source_digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    statements = []
    for start in range(0, len(source_text), limit):
        end = min(start + limit, len(source_text))
        chunk = source_text[start:end]
        statements.append({
            "statement_id": f"source-span-{source_digest[:16]}-{start}-{end}",
            "kind": "technical", "text": chunk, "gemini_confidence": None,
            "locator": {"kind": "uri_fragment", "value": f"char:{start}:{end}",
                        "quote_digest": hashlib.sha256(chunk.encode("utf-8")).hexdigest()},
        })
    return statements


def validate_source_span_locator(item: dict[str, Any], locator: dict[str, Any],
                                 source_text: str) -> bool:
    """Verify our exact offset locator; return False for other locator schemes."""
    if locator.get("kind") != "uri_fragment" or not str(locator.get("value", "")).startswith("char:"):
        return False
    match = re.fullmatch(r"char:(\d+):(\d+)", locator["value"])
    if match is None:
        raise ValueError("Invalid source character-offset locator")
    start, end = map(int, match.groups())
    if not 0 <= start < end <= len(source_text):
        raise ValueError("Source character-offset locator is outside the preserved export")
    quoted = source_text[start:end]
    value = item.get("text") or item.get("exact_text") or item.get("action")
    if value != quoted or locator.get("quote_digest") != hashlib.sha256(quoted.encode("utf-8")).hexdigest():
        raise ValueError("Source character-offset locator does not match its exact evidence")
    return True


def validate_docx_cell_locator(item: dict[str, Any], locator: dict[str, Any],
                               source_text: str) -> None:
    """Bind a cell locator to its exact exported span and the quoted evidence.

    Generated action descriptions can be paraphrases: their locator binds the
    unambiguous source span, while semantic support remains the judge's job. Any
    supplied digest must match the quoted text or that full source-cell span.
    """
    value = str(locator.get("value", "")).strip()
    if re.fullmatch(r"T[1-9]\d*\.R[1-9]\d*\.C[1-9]\d*", value) is None:
        raise ValueError("Invalid DOCX cell locator")
    opening, closing = f"[{value}]", f"[/{value}]"
    if source_text.count(opening) != 1 or source_text.count(closing) != 1:
        raise ValueError("DOCX cell locator is absent or ambiguous in the preserved export")
    start = source_text.index(opening) + len(opening)
    end = source_text.index(closing)
    if end < start:
        raise ValueError("DOCX cell locator has inverted boundaries")
    cell_text = source_text[start:end].strip()
    if not cell_text:
        raise ValueError("DOCX cell evidence must contain visible source text")
    quote = item.get("text") or item.get("exact_text") or item.get("action") or ""
    if not isinstance(quote, str):
        raise ValueError("DOCX cell evidence must be a string")
    normalized_quote = " ".join(quote.split())
    normalized_cell = " ".join(cell_text.split())
    quote_is_bound = bool(normalized_quote) and normalized_quote in normalized_cell
    quote_digest = locator.get("quote_digest")
    expected_digest = hashlib.sha256(quote.encode("utf-8")).hexdigest()
    if "action" in item and quote_digest is None:
        return
    if "action" in item and quote_digest == hashlib.sha256(cell_text.encode("utf-8")).hexdigest():
        return
    if not quote_is_bound or (quote_digest is not None and quote_digest != expected_digest):
        raise ValueError("DOCX cell locator does not bind the quoted evidence to that cell")
