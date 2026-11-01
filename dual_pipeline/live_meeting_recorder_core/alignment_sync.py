"""Alignment synchronization and speaker cluster validation for live meeting recorder."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class AlignmentDesynchronizationError(RuntimeError):
    """Raised when segment timeline desynchronizes from acoustic turns."""


def extract_initial_speaker_map(
    initial_map: Optional[Dict[str, str]],
    kwargs: Dict[str, Any],
) -> Dict[str, str]:
    """Extract and normalize initial speaker map from explicit argument or kwargs."""
    if initial_map:
        return dict(initial_map)
    spk = kwargs.get("initial_speaker_map") or kwargs.get("speaker_map")
    return dict(spk) if spk else {}


def verify_temporal_sync(
    turns: List[Dict[str, Any]], segments: List[Dict[str, Any]], threshold_sec: float = 30.0
) -> None:
    """Verifies that segments with speech temporally intersect acoustic turns within threshold_sec."""
    if not turns or not segments:
        return
    text_segments = [s for s in segments if s.get("text") and str(s.get("text")).strip()]
    if not text_segments:
        return
    acoustic_start = min(float(t["start"]) for t in turns)
    acoustic_end = max(float(t["end"]) for t in turns)
    seg_start_first = float(text_segments[0].get("seconds", 0.0))
    seg_end_last = float(text_segments[-1].get("seconds", 0.0)) + float(
        text_segments[-1].get("duration", 0.0)
    )
    if (
        seg_start_first > acoustic_end + threshold_sec
        or seg_end_last < acoustic_start - threshold_sec
    ):
        raise AlignmentDesynchronizationError(
            f"Catastrophic timeline desynchronization: segments [{seg_start_first:.1f}s..{seg_end_last:.1f}s] "
            f"diverge from acoustic turns [{acoustic_start:.1f}s..{acoustic_end:.1f}s] by >{threshold_sec}s."
        )


def check_cluster_collapse(
    turns: List[Dict[str, Any]], aligned_segments: List[Dict[str, Any]]
) -> bool:
    """Returns True if alignment collapses distinct acoustic clusters by more than 1."""
    raw_acoustic_clusters = {t["speaker"] for t in turns}
    aligned_active_speakers = {
        s.get("speaker_resolved") or s.get("speaker")
        for s in aligned_segments
        if s.get("text") and str(s.get("text")).strip()
    }
    return (
        len(raw_acoustic_clusters) >= 3
        and len(aligned_active_speakers) < len(raw_acoustic_clusters) - 1
    )
