"""Context construction and approval binding for grouped speaker-review items."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Sequence, Tuple

from .review_context import (
    SpeakerReviewContext,
    SpeakerReviewContextError,
    SpeakerReviewTurn,
    build_speaker_review_context,
)
from .review_context_render import format_speaker_review_context


def _resolve_source_id(utterances: Sequence[Dict[str, Any]]) -> str:
    sources = {str(item["source_id"]) for item in utterances if item.get("source_id")}
    if len(sources) > 1:
        raise SpeakerReviewContextError("Grouped speaker review utterances span multiple sources.")
    if sources:
        return next(iter(sources))
    material = [
        {"utterance_id": item.get("utterance_id") or item.get("turn_id") or item.get("id"),
         "speaker": item.get("speaker"),
         "text": item.get("text"), "start": item.get("start_time_sec", item.get("start"))}
        for item in utterances
    ]
    if any(not isinstance(item.get("text"), str) for item in utterances):
        raise SpeakerReviewContextError("Grouped review source identity cannot be bound without verbatim turn text.")
    digest = hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return f"utterance-sequence:{digest}"


def build_grouped_review_contexts(
    utterances: Sequence[Dict[str, Any]],
    target_ids: Sequence[str],
) -> Tuple[List[Dict[str, Any]], str]:
    """Build one complete five-turn context for every target in a grouped cluster."""
    try:
        source_id = _resolve_source_id(utterances)
        turns = []
        for position, item in enumerate(utterances):
            raw_id = item.get("utterance_id") or item.get("turn_id") or item.get("id")
            utterance_id = str(raw_id or "").strip()
            text = item.get("text")
            if not utterance_id or not isinstance(text, str):
                raise SpeakerReviewContextError("A grouped review turn is missing its stable ID or verbatim text.")
            turns.append({
                "turn_id": utterance_id,
                "source_position": position,
                "source_id": str(item.get("source_id") or source_id),
                "speaker": str(item.get("speaker", "Unknown")),
                "text": text,
                "timestamp": str(item.get("timestamp") or item.get("start_timestamp") or "Unavailable"),
            })
        contexts = [
            build_speaker_review_context(turns, str(target_id), source_id).to_dict()
            for target_id in target_ids
        ]
        return contexts, "ready"
    except (SpeakerReviewContextError, ValueError):
        return [], "unavailable"


def grouped_context_digest(contexts: Sequence[Dict[str, Any]]) -> str:
    """Hash exactly the serialized context set that the operator saw."""
    canonical = json.dumps(list(contexts), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def render_grouped_contexts(contexts: Sequence[Dict[str, Any]]) -> str:
    """Render all per-target windows for one atomic grouped review card."""
    if not contexts:
        raise SpeakerReviewContextError("Speaker review is blocked because its context windows are unavailable.")
    rendered = []
    for item in contexts:
        context = SpeakerReviewContext(
            source_id=item["source_id"], source_digest=item["source_digest"],
            target_turn_id=item["target_turn_id"],
            turns=tuple(
                SpeakerReviewTurn(**turn) for turn in item["turns"]
            ),
            before_unavailable=item["before_unavailable"],
            after_unavailable=item["after_unavailable"],
        )
        rendered.append(format_speaker_review_context(context))
    return "\n\n--- Separate target turn ---\n\n".join(rendered)
