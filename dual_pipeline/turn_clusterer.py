"""
dual_pipeline/turn_clusterer.py

Conversational Turn Clustering and Acoustic Boundary Engine.
Replaces binary 2-speaker ping-pong toggling with adaptive, roster-aware
conversational turn clustering. Enforces Rule C-11 recording gap annotations
and extracts acoustic ambiguity candidates for Output 01 ALR tables.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dual_pipeline.turn_clusterer")

RECORDING_GAP_THRESHOLD_SECONDS = 180.0
DEFAULT_PAUSE_THRESHOLD_SECONDS = 1.6
MIN_TURN_DURATION_SECONDS = 0.5


def _format_seconds_to_timestamp(seconds: float) -> str:
    """Formats floating-point seconds into HH:MM:SS string."""
    m, s = divmod(max(0.0, seconds), 60.0)
    h, m = divmod(m, 60.0)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d}"


def _get_roster_size(metadata: Optional[Dict[str, Any]]) -> int:
    """Estimates participant roster size from metadata."""
    if not metadata:
        return 2
    roster = metadata.get("roster") or metadata.get("attendees")
    if isinstance(roster, list):
        return max(2, len(roster))
    if isinstance(roster, dict):
        total = sum(len(v) for v in roster.values() if isinstance(v, list))
        return max(2, total)
    return 2


def _handle_recording_gap(
    start: float, last_end: float, current_speaker_idx: int, roster_size: int
) -> Tuple[Optional[Dict[str, Any]], int]:
    """Detects gaps > 180s and creates Rule C-11 notation if found."""
    if last_end <= 0.0 or (start - last_end) < RECORDING_GAP_THRESHOLD_SECONDS:
        return None, current_speaker_idx
    gap_duration = start - last_end
    gap_start_ts = _format_seconds_to_timestamp(last_end)
    gap_end_ts = _format_seconds_to_timestamp(start)
    gap_note = f"[Recording gap: {gap_start_ts} – {gap_end_ts}]"
    logger.info("Detected recording gap (%.1fs) from %s to %s", gap_duration, gap_start_ts, gap_end_ts)
    gap_seg = {
        "speaker": "System",
        "start": last_end,
        "end": start,
        "text": gap_note,
        "is_gap_annotation": True,
    }
    return gap_seg, (current_speaker_idx % roster_size) + 1


def _is_continuation(prev_text: str, curr_text: str) -> bool:
    """Checks if current utterance is a lexical continuation of the previous utterance."""
    if not prev_text or not curr_text:
        return False
    prev_clean = prev_text.strip()
    curr_clean = curr_text.strip()
    if prev_clean.endswith((",", "-", "...", "—", ";")):
        return True
    return bool(curr_clean and curr_clean[0].islower() and curr_clean[0].isalpha())


def _update_turn_on_pause(
    start: float,
    end: float,
    last_end: float,
    current_speaker_idx: int,
    roster_size: int,
    pause_threshold: float,
    prev_text: str = "",
    curr_text: str = "",
) -> int:
    """Updates active speaker index if conversational pause exceeds threshold and is not a continuation."""
    if _is_continuation(prev_text, curr_text):
        return current_speaker_idx

    if last_end > 0.0 and (start - last_end) >= pause_threshold and (end - start) >= MIN_TURN_DURATION_SECONDS:
        return (current_speaker_idx % roster_size) + 1
    return current_speaker_idx


def _check_acoustic_ambiguity(seg: Dict[str, Any], text: str, start: float) -> Optional[Dict[str, Any]]:
    """Checks segment telemetry for low acoustic confidence markers."""
    avg_logprob = float(seg.get("avg_logprob", 0.0))
    no_speech_prob = float(seg.get("no_speech_prob", 0.0))
    compression_ratio = float(seg.get("compression_ratio", 1.0))
    if (avg_logprob < -0.9 or no_speech_prob > 0.5 or compression_ratio > 2.2) and len(text) > 4:
        return {
            "timestamp": _format_seconds_to_timestamp(start),
            "term": text[:30],
            "context": text,
            "avg_logprob": avg_logprob,
            "no_speech_prob": no_speech_prob,
            "status": "Acoustic ambiguity / low confidence ASR",
        }
    return None


def cluster_conversational_turns(
    segments: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
    pause_threshold: float = DEFAULT_PAUSE_THRESHOLD_SECONDS,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Clusters un-diarized ASR segments into coherent speaker turns.
    """
    if not segments:
        return [], []

    roster_size = _get_roster_size(metadata)
    clustered_segments: List[Dict[str, Any]] = []
    acoustic_ambiguities: List[Dict[str, Any]] = []

    current_speaker_idx = 1
    last_end = 0.0
    prev_text = ""

    for seg in segments:
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", start + 1.0))
        text = str(seg.get("text", "")).strip()

        if not text:
            continue

        gap_seg, current_speaker_idx = _handle_recording_gap(
            start, last_end, current_speaker_idx, roster_size
        )
        if gap_seg:
            clustered_segments.append(gap_seg)
        else:
            current_speaker_idx = _update_turn_on_pause(
                start, end, last_end, current_speaker_idx, roster_size, pause_threshold,
                prev_text=prev_text, curr_text=text,
            )

        speaker_label = seg.get("speaker")
        if not speaker_label or str(speaker_label).lower() in ("unknown", "speaker unknown"):
            assigned_speaker = f"Speaker {current_speaker_idx}"
        else:
            assigned_speaker = str(speaker_label)

        ambiguity = _check_acoustic_ambiguity(seg, text, start)
        if ambiguity:
            acoustic_ambiguities.append(ambiguity)

        clustered_segments.append({
            "speaker": assigned_speaker,
            "start": start,
            "end": end,
            "text": text,
        })
        last_end = end
        prev_text = text

    return clustered_segments, acoustic_ambiguities


def cluster_conversational_turns_with_patterns(
    segments: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
    pause_threshold: float = DEFAULT_PAUSE_THRESHOLD_SECONDS,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Any]:
    """
    Clusters ASR segments and applies SpeakerPatternEngine to recover speaker
    identities and conversational structures for long unattributed transcripts.
    """
    clustered, ambiguities = cluster_conversational_turns(
        segments, metadata=metadata, pause_threshold=pause_threshold
    )

    from dual_pipeline.speaker_attribution_core.pattern_engine import SpeakerPatternEngine

    known_roster: List[str] = []
    if metadata:
        r = metadata.get("roster") or metadata.get("attendees")
        if isinstance(r, list):
            known_roster = [str(x) for x in r]
        elif isinstance(r, dict):
            for v in r.values():
                if isinstance(v, list):
                    known_roster.extend(str(x) for x in v)

    engine = SpeakerPatternEngine(known_participants=known_roster)
    pattern_map = engine.analyze_and_pattern_turns(clustered)

    if metadata is not None:
        metadata["speaker_pattern_map"] = pattern_map.to_dict()

    return pattern_map.assigned_turns, ambiguities, pattern_map
