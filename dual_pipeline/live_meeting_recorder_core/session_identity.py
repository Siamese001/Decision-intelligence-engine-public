"""
dual_pipeline/live_meeting_recorder_core/session_identity.py

Canonical engagement identity normalization, matching, conflict detection,
and legacy session reconciliation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional, Set


def normalize_engagement_id(engagement: Optional[str]) -> Optional[str]:
    """
    Normalizes a project or engagement name/id into canonical format: 'project:<norm>'.
    Returns None if input is None, empty, or whitespace.
    """
    if not engagement:
        return None
    cleaned = str(engagement).strip().lower()
    if not cleaned or cleaned in ("none", "null", "unknown"):
        return None
    if cleaned.startswith("project:"):
        inner = cleaned[8:].strip().replace("-", "_").replace(" ", "_")
        return f"project:{inner}" if inner else None
    inner = cleaned.replace("-", "_").replace(" ", "_")
    return f"project:{inner}"


def canonical_display_name(engagement_id: Optional[str]) -> str:
    """Returns upper-case clean display name from canonical engagement id."""
    norm = normalize_engagement_id(engagement_id)
    if not norm:
        return "UNKNOWN"
    return norm.replace("project:", "").upper()


def match_engagement(
    manifest_engagement: Optional[str], target_engagement: Optional[str]
) -> bool:
    """Checks whether a session manifest's engagement matches the target engagement."""
    norm_manifest = normalize_engagement_id(manifest_engagement)
    norm_target = normalize_engagement_id(target_engagement)
    if not norm_manifest or not norm_target:
        return False
    return norm_manifest == norm_target


def detect_engagement_conflicts(
    sessions: Iterable[Any],
) -> Optional[Set[str]]:
    """
    Detects if an active session pool contains multiple distinct, non-empty engagements.
    Returns the set of conflicting canonical engagement IDs, or None if no conflict.
    """
    distinct_engagements: Set[str] = set()
    for s in sessions:
        eng = getattr(s, "engagement_id", None)
        norm = normalize_engagement_id(eng)
        if norm:
            distinct_engagements.add(norm)
    if len(distinct_engagements) > 1:
        return distinct_engagements
    return None


def reconcile_legacy_session(
    manifest: Any,
    engagement: str,
    engagement_name: Optional[str] = None,
    registry_dir: Optional[Path] = None,
) -> Any:
    """
    Explicitly reconciles a legacy session manifest lacking canonical engagement identity.
    Persists updated manifest if registry_dir is provided.
    """
    canon_id = normalize_engagement_id(engagement)
    if not canon_id:
        raise ValueError(
            f"Cannot reconcile legacy session with invalid engagement: {engagement}"
        )
    manifest.engagement_id = canon_id
    manifest.engagement_name = canonical_display_name(engagement_name or canon_id)
    if registry_dir and hasattr(manifest, "session_id") and hasattr(manifest, "to_dict"):
        dest = Path(registry_dir) / f"{manifest.session_id}.json"
        dest.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
    return manifest
