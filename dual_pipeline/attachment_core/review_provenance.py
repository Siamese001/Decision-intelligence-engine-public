"""
dual_pipeline.attachment_core.review_provenance

Modular helper for authenticated drop detection, source mention validation,
and collateral review provenance evaluation.
Enforces that meeting collateral defaults to BACKGROUND and requires an
independently established review proof (authentic in-session drop or verified
speaker mention) before being granted COLLATERAL_REVIEWED status.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Dict, List, Optional, Tuple

from .types import (
    AttachmentMention,
    AttachmentRecord,
    CrossModalAttachmentAlignment,
)

logger = logging.getLogger(__name__)

_TRUSTED_SOURCES = {
    "copilot_drop", "user_upload", "session_event", "chat_upload", "live_drop"
}


def parse_hms_to_seconds(hms: Optional[str]) -> Optional[float]:
    """Parses 'HH:MM:SS' or 'MM:SS' into total seconds."""
    if not hms or not isinstance(hms, str):
        return None
    try:
        parts = [int(x) for x in hms.strip().split(":")]
        if len(parts) == 3:
            return float(parts[0] * 3600 + parts[1] * 60 + parts[2])
        if len(parts) == 2:
            return float(parts[0] * 60 + parts[1])
    except (ValueError, AttributeError):
        pass
    return None


def _is_temporally_plausible(
    elapsed_secs: Optional[float],
    session_metadata: Optional[Dict[str, Any]],
) -> bool:
    if elapsed_secs is None or not session_metadata:
        return True
    dur = session_metadata.get("duration")
    if not dur:
        return True
    dur_secs = None
    if isinstance(dur, (int, float)):
        dur_secs = float(dur)
    elif isinstance(dur, str) and dur.endswith("m"):
        try:
            dur_secs = float(dur[:-1]) * 60.0
        except ValueError:
            return True
    return dur_secs is None or elapsed_secs <= (dur_secs + 300.0)


def _has_trusted_drop_event(drop_entry: Dict[str, Any]) -> bool:
    if drop_entry.get("drop_event_id") or drop_entry.get("session_event_id"):
        return True
    if drop_entry.get("uploaded_by") or drop_entry.get("user_id"):
        return True
    source = str(drop_entry.get("source") or "").lower()
    return source in _TRUSTED_SOURCES


def is_authenticated_drop(
    drop_entry: Optional[Dict[str, Any]],
    session_metadata: Optional[Dict[str, Any]] = None,
) -> bool:
    """Checks if a drop log entry represents an authentic in-session drop event."""
    if not drop_entry or not isinstance(drop_entry, dict):
        return False
    if drop_entry.get("attribution_mode") == "BACKGROUND":
        return False
    if drop_entry.get("synthetic") or drop_entry.get("is_synthetic"):
        return False

    elapsed_str = drop_entry.get("dropped_at_elapsed")
    elapsed_secs = parse_hms_to_seconds(str(elapsed_str)) if elapsed_str else None

    if not _is_temporally_plausible(elapsed_secs, session_metadata):
        return False
    if _has_trusted_drop_event(drop_entry):
        return True
    if drop_entry.get("attribution_mode") == "COLLATERAL_REVIEWED" or drop_entry.get("sha256"):
        return elapsed_secs is not None and elapsed_secs >= 0.0
    return elapsed_secs is not None and elapsed_secs > 0.0


def has_authenticated_mention(
    att_id: str,
    filename: str,
    alignment: Optional[CrossModalAttachmentAlignment],
) -> bool:
    """Checks if an attachment has an authenticated, non-weak speaker mention in the transcript."""
    if not alignment or not getattr(alignment, "mentions", None):
        return False

    fname_lower = filename.lower()
    for mention in alignment.mentions:
        m_id = getattr(mention, "attachment_id", "")
        m_fn = getattr(mention, "filename", "").lower()
        if m_id != att_id and m_fn != fname_lower:
            continue
        if getattr(mention, "is_weak_alignment", False):
            continue
        if getattr(mention, "is_speaker_authenticated", False):
            return True

    return False


@dataclass
class ReviewProvenanceResult:
    is_reviewed: bool
    attribution_mode: str
    provenance_reason: str
    dropped_at_elapsed: Optional[str] = None


def evaluate_attachment_review_provenance(
    att: AttachmentRecord,
    drop_entry: Optional[Dict[str, Any]] = None,
    alignment: Optional[CrossModalAttachmentAlignment] = None,
    session_metadata: Optional[Dict[str, Any]] = None,
) -> ReviewProvenanceResult:
    """Evaluates whether an attachment should be COLLATERAL_REVIEWED or BACKGROUND."""
    if drop_entry and is_authenticated_drop(drop_entry, session_metadata):
        return ReviewProvenanceResult(
            is_reviewed=True,
            attribution_mode="COLLATERAL_REVIEWED",
            provenance_reason="authenticated_drop",
            dropped_at_elapsed=drop_entry.get("dropped_at_elapsed"),
        )

    if has_authenticated_mention(att.attachment_id, att.filename, alignment):
        return ReviewProvenanceResult(
            is_reviewed=True,
            attribution_mode="COLLATERAL_REVIEWED",
            provenance_reason="authenticated_mention",
            dropped_at_elapsed=att.dropped_at_elapsed,
        )

    return ReviewProvenanceResult(
        is_reviewed=False,
        attribution_mode="BACKGROUND",
        provenance_reason="unsupported_staging_only",
        dropped_at_elapsed=None,
    )
