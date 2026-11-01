"""Source-bound transcript context windows for human speaker attribution review."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence


class SpeakerReviewContextError(ValueError):
    """Raised when a complete, source-consistent speaker context cannot be built."""


@dataclass(frozen=True)
class SpeakerReviewTurn:
    """A verbatim source turn and its stable position in the transcript."""

    turn_id: str
    speaker: str
    text: str
    timestamp: str
    source_id: str
    position: int
    is_target: bool = False


@dataclass(frozen=True)
class SpeakerReviewContext:
    """The target and up to two complete neighboring turns on each side."""

    source_id: str
    source_digest: str
    target_turn_id: str
    turns: tuple[SpeakerReviewTurn, ...]
    before_unavailable: int
    after_unavailable: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_digest": self.source_digest,
            "target_turn_id": self.target_turn_id,
            "turns": [asdict(turn) for turn in self.turns],
            "before_unavailable": self.before_unavailable,
            "after_unavailable": self.after_unavailable,
        }


def _normalize_turn(turn: Mapping[str, Any], fallback_position: int, source_id: str) -> dict[str, Any]:
    try:
        position = int(turn.get("source_position", turn.get("position", fallback_position)))
    except (TypeError, ValueError) as exc:
        raise SpeakerReviewContextError("Transcript turn has an invalid source position.") from exc

    raw_turn_id = turn.get("turn_id", turn.get("id", fallback_position))
    turn_id = str(raw_turn_id).strip() if raw_turn_id is not None else ""
    text = turn.get("text")
    if not turn_id or not isinstance(text, str):
        raise SpeakerReviewContextError(f"Turn at source position {position} has no stable ID or text.")

    member_source = str(turn.get("source_id", source_id))
    if member_source != source_id:
        raise SpeakerReviewContextError("Context turns span more than one transcript source.")

    speaker = str(turn.get("speaker", "Unknown"))
    raw_timestamp = turn.get("timestamp")
    timestamp = str(raw_timestamp).strip() if raw_timestamp else "Unavailable"

    return {
        "turn_id": turn_id,
        "speaker": speaker,
        "text": text,
        "timestamp": timestamp,
        "source_id": member_source,
        "position": position,
    }


def build_speaker_review_context(
    turns: Sequence[Mapping[str, Any]],
    target_turn_id: str,
    source_id: str,
    *,
    expected_source_id: str | None = None,
    expected_source_digest: str | None = None,
    before_count: int = 2,
    after_count: int = 2,
) -> SpeakerReviewContext:
    """Build an exact source-ordered window and reject ambiguity, gaps, or stale evidence."""
    if not source_id or (expected_source_id is not None and source_id != expected_source_id):
        raise SpeakerReviewContextError("Speaker context source identity is missing or does not match the target.")
    if before_count != 2 or after_count != 2:
        raise ValueError("Speaker assignment review requires exactly two preceding and two following turns.")
    if not turns:
        raise SpeakerReviewContextError("Transcript source is empty; context cannot be built.")

    normalized = [_normalize_turn(turn, index, source_id) for index, turn in enumerate(turns)]
    if any(right["position"] != left["position"] + 1 for left, right in zip(normalized, normalized[1:])):
        raise SpeakerReviewContextError("Transcript source has an interior turn gap; context cannot be verified.")

    ids = [turn["turn_id"] for turn in normalized]
    if len(ids) != len(set(ids)):
        raise SpeakerReviewContextError("Transcript contains duplicate turn IDs; target context is ambiguous.")
    if target_turn_id not in ids:
        raise SpeakerReviewContextError(f"Target turn {target_turn_id!r} is missing from the transcript source.")

    canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    if expected_source_digest is not None and digest != expected_source_digest:
        raise SpeakerReviewContextError("Transcript source changed since the speaker review was prepared.")

    target_position = ids.index(target_turn_id)
    start_index = max(0, target_position - before_count)
    end_index = min(len(normalized), target_position + after_count + 1)

    window_turns = tuple(
        SpeakerReviewTurn(
            turn_id=item["turn_id"],
            speaker=item["speaker"],
            text=item["text"],
            timestamp=item["timestamp"],
            source_id=item["source_id"],
            position=item["position"],
            is_target=(item["turn_id"] == target_turn_id),
        )
        for item in normalized[start_index:end_index]
    )

    before_unavailable = max(0, before_count - target_position)
    after_unavailable = max(0, (target_position + after_count + 1) - len(normalized))

    return SpeakerReviewContext(
        source_id=source_id,
        source_digest=digest,
        target_turn_id=target_turn_id,
        turns=window_turns,
        before_unavailable=before_unavailable,
        after_unavailable=after_unavailable,
    )
