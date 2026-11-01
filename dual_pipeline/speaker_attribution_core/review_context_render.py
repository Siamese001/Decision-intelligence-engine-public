"""Readable Antigravity prompt rendering for speaker-review context."""
from __future__ import annotations

import re
from typing import Any

from dual_pipeline.speaker_attribution_core.review_context import SpeakerReviewContext


def _format_review_turn(turn: Any, index: int, target_pos: int) -> str:
    if turn.is_target:
        marker = "TARGET"
        speaker = f"**UNRESOLVED — {turn.speaker}**"
    else:
        marker = f"BEFORE {target_pos - index}" if index < target_pos else f"AFTER {index - target_pos}"
        speaker = turn.speaker
        if speaker.strip().lower() in {"unknown", "unattributed", "unknown speaker"} or any(
            cue in speaker.lower() for cue in ("shared", "room", "(room)")
        ):
            speaker = f"**UNRESOLVED — {speaker}**"
    clean_ts = str(turn.timestamp or "").strip("[]")
    ts_display = f"[{clean_ts}]" if clean_ts else "[Unavailable]"
    return f"{marker} {ts_display} {speaker}: {turn.text}"


def format_speaker_review_context(context: SpeakerReviewContext) -> str:
    """Render visible transcript evidence with each turn formatted as a distinct row."""
    rows = [f"Meeting/source: {context.source_id}", "Review one target speaker assignment."]
    if context.before_unavailable:
        rows.append(f"Transcript start: {context.before_unavailable} earlier turn(s) unavailable at this boundary.")
    target_pos = next(i for i, item in enumerate(context.turns) if item.is_target)
    for index, turn in enumerate(context.turns):
        rows.append(_format_review_turn(turn, index, target_pos))
    if context.after_unavailable:
        rows.append(f"Transcript end: {context.after_unavailable} later turn(s) unavailable at this boundary.")
    rows.append("Who is speaking in the TARGET turn?")
    return "\n\n".join(rows)


_TURN_MARKER_REGEX = re.compile(r"\b(TARGET|BEFORE\s+[12]|AFTER\s+[12])\b")


def validate_visible_speaker_context(value: object) -> str:
    """Require a complete visible context block before Antigravity offers speaker choices."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Speaker assignment is blocked because visible transcript context is missing.")
    raw_str = value.strip()
    required = ("TARGET", "UNRESOLVED", "BEFORE 1", "BEFORE 2", "AFTER 1", "AFTER 2")
    has_start_boundary = "Transcript start:" in raw_str
    has_end_boundary = "Transcript end:" in raw_str
    missing = [token for token in required if token not in raw_str]
    if has_start_boundary:
        missing = [token for token in missing if token not in {"BEFORE 1", "BEFORE 2"}]
    if has_end_boundary:
        missing = [token for token in missing if token not in {"AFTER 1", "AFTER 2"}]
    if missing:
        raise ValueError(f"Speaker assignment context is incomplete or unmarked: {', '.join(missing)}.")

    # Validate that turn markers do not share lines and are separated into distinct rows
    lines = [line.strip() for line in raw_str.splitlines() if line.strip()]
    marker_line_indices: dict[str, int] = {}
    for line_idx, line in enumerate(lines):
        found_markers = _TURN_MARKER_REGEX.findall(line)
        if len(found_markers) > 1:
            raise ValueError(
                f"Speaker assignment context formatting error: multiple turn markers {found_markers} "
                "collapsed onto the same line. Each turn marker must occupy its own separate row."
            )
        for m in found_markers:
            norm_m = re.sub(r"\s+", " ", m)
            marker_line_indices[norm_m] = line_idx

    # Require distinct lines for present turn markers
    expected_markers = [m for m in ("BEFORE 2", "BEFORE 1", "TARGET", "AFTER 1", "AFTER 2") if m in raw_str]
    for i in range(len(expected_markers) - 1):
        m1, m2 = expected_markers[i], expected_markers[i + 1]
        idx1, idx2 = marker_line_indices.get(m1), marker_line_indices.get(m2)
        if idx1 is not None and idx2 is not None and idx1 == idx2:
            raise ValueError(
                f"Speaker assignment context formatting error: markers '{m1}' and '{m2}' share the same row. "
                "Each turn marker must be rendered in its own separate row."
            )

    return raw_str


def parse_speaker_review_turns(transcript: str, source_id: str) -> list[dict[str, object]]:
    """Extract speaker turns with continuation lines from a source transcript."""
    from dual_pipeline.speaker_disambiguation_core.disambiguator import SpeakerDisambiguator

    parsed = SpeakerDisambiguator(model="offline").parse_turns(transcript)
    lines = transcript.splitlines()
    turns: list[dict[str, object]] = []
    for index, turn in enumerate(parsed):
        start = int(turn["line_index"])
        end = int(parsed[index + 1]["line_index"]) if index + 1 < len(parsed) else len(lines)
        continuation = []
        for line in lines[start + 1:end]:
            if line.lstrip().startswith(("#", "---")):
                break
            continuation.append(line)
        while continuation and not continuation[-1].strip():
            continuation.pop()
        text = str(turn["text"])
        if continuation:
            text += "\n" + "\n".join(continuation)
        turns.append({
            "turn_id": str(start),
            "source_position": index,
            "speaker": turn["speaker"],
            "timestamp": turn.get("timestamp") or "Unavailable",
            "text": text,
            "source_id": source_id,
        })
    return turns


def _find_speaker_review_bounds(
    parsed: list[dict[str, object]],
    start_timestamp: str | None,
    end_timestamp: str | None,
) -> tuple[int, int]:
    """Find the start and end bounds based on timestamps."""
    normalize_time = lambda value: str(value or "").strip("[]* ")
    first_matches = [
        i for i, turn in enumerate(parsed)
        if start_timestamp and normalize_time(turn["timestamp"]) == normalize_time(start_timestamp)
    ]
    last_matches = [
        i for i, turn in enumerate(parsed)
        if end_timestamp and normalize_time(turn["timestamp"]) == normalize_time(end_timestamp)
    ]
    if (start_timestamp and not first_matches) or (end_timestamp and not last_matches):
        raise ValueError("Speaker assignment is blocked because the referenced transcript boundary is missing.")
    first = first_matches[0] if first_matches else 0
    last = last_matches[-1] if last_matches else len(parsed) - 1
    if last < first:
        raise ValueError("Speaker assignment is blocked because the transcript range is reversed.")
    return first, last


def build_speaker_review_question(
    transcript: str,
    target_speaker: str,
    question: str,
    *,
    source_id: str = "",
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
) -> str:
    """Attach exact context for each affected turn in one speaker-cluster decision."""
    import hashlib
    from dual_pipeline.speaker_attribution_core.review_context import build_speaker_review_context

    normalized = parse_speaker_review_turns(transcript, source_id or "pending")
    if not normalized:
        raise ValueError("Speaker assignment is blocked because transcript turns could not be parsed.")
    source_id = source_id or f"transcript:{hashlib.sha256(transcript.encode('utf-8')).hexdigest()}"
    for turn in normalized:
        turn["source_id"] = source_id
    parsed = [{"line_index": int(turn["turn_id"]), "timestamp": turn["timestamp"], "speaker": turn["speaker"]} for turn in normalized]

    first, last = _find_speaker_review_bounds(parsed, start_timestamp, end_timestamp)

    matching = [
        (index, turn) for index, turn in enumerate(parsed[first:last + 1], start=first)
        if turn["speaker"].strip().casefold() == target_speaker.strip().casefold()
    ]
    if not matching:
        raise ValueError(f"Speaker assignment is blocked because no source turns match {target_speaker!r}.")
    contexts = []
    for _index, target in matching:
        context = build_speaker_review_context(
            normalized, str(target["line_index"]), source_id,
            expected_source_id=source_id,
        )
        contexts.append(format_speaker_review_context(context))
    return "\n\n--- Separate source turn ---\n\n".join(contexts) + "\n\n" + question


def _collect_rendered_review_contexts(
    normalized: list[dict[str, Any]],
    target_speaker: str,
    eff_source_id: str,
    start_timestamp: str | None,
    end_timestamp: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    from dual_pipeline.speaker_attribution_core.review_context import build_speaker_review_context

    for turn in normalized:
        turn["source_id"] = eff_source_id
    parsed = [{"line_index": int(turn["turn_id"]), "timestamp": turn["timestamp"], "speaker": turn["speaker"]} for turn in normalized]
    first, last = _find_speaker_review_bounds(parsed, start_timestamp, end_timestamp)
    matching = [turn for turn in parsed[first:last + 1] if turn["speaker"].strip().casefold() == target_speaker.strip().casefold()]
    review_contexts: list[dict[str, Any]] = []
    rendered_blocks: list[str] = []
    for target in matching:
        ctx = build_speaker_review_context(normalized, str(target["line_index"]), eff_source_id, expected_source_id=eff_source_id)
        rendered_blocks.append(validate_visible_speaker_context(format_speaker_review_context(ctx)))
        review_contexts.append(ctx.to_dict())
    return review_contexts, rendered_blocks


def build_hitl_review_payload(
    transcript: str,
    target_speaker: str,
    question: str,
    candidates: list[Any],
    *,
    source_id: str = "",
    start_timestamp: str | None = None,
    end_timestamp: str | None = None,
    fail_closed: bool = False,
) -> dict[str, Any]:
    """Builds structured 5-turn (BEFORE 2..AFTER 2) review_contexts, options, prompt, and context_digest for HITLRequiredError."""
    import hashlib

    options = [
        {
            "label": c.to_option_string() if hasattr(c, "to_option_string") else str(c),
            "attendee_name": getattr(c, "attendee_name", str(c)),
            "confidence": getattr(c, "confidence", 0.0),
            "confidence_tier": getattr(c, "confidence_tier", "LOW"),
        }
        for c in candidates
    ]
    review_contexts: list[dict[str, Any]] = []
    prompt = question
    context_digest = ""
    try:
        normalized = parse_speaker_review_turns(transcript, source_id or "pending")
        if normalized:
            eff_source_id = source_id or f"transcript:{hashlib.sha256(transcript.encode('utf-8')).hexdigest()}"
            review_contexts, rendered_blocks = _collect_rendered_review_contexts(
                normalized, target_speaker, eff_source_id, start_timestamp, end_timestamp
            )
            if review_contexts:
                context_digest = str(review_contexts[0].get("source_digest") or "")
                prompt = "\n\n--- Separate source turn ---\n\n".join(rendered_blocks) + "\n\n" + question
        if fail_closed and (not review_contexts or not context_digest):
            raise ValueError(
                f"FAIL_CLOSED_REVIEW_CONTEXT: Could not construct 5-turn visible speaker review context "
                f"and context_digest for target speaker {target_speaker!r}."
            )
    except Exception:
        if fail_closed:
            raise
    return {
        "options": options,
        "prompt": prompt,
        "review_contexts": review_contexts,
        "context_digest": context_digest,
    }
