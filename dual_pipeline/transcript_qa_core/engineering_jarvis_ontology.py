"""
dual_pipeline/transcript_qa_core/engineering_jarvis_ontology.py

Pass-through compatibility module after retirement of meeting-specific content fabricators.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def is_engineering_jarvis(
    metadata: Optional[Dict[str, Any]] = None,
    text: str = "",
    meeting_id: str = "",
) -> bool:
    """Detect legacy engineering jarvis metadata keys without hardcoded person names."""
    meta = metadata or {}
    proj = (meta.get("project") or "").lower()
    m_name = (meta.get("meeting_name") or meta.get("title") or "").lower()
    mid = (meeting_id or meta.get("meeting_id") or "").lower()

    if "kickoff" in m_name or "kickoff" in mid:
        return False

    has_jarvis = any(k in m_name or k in mid or k in proj for k in ("jarvis", "nexus")) or "jarvis" in text.lower()
    has_eng = "engineering" in m_name or "engineering" in mid
    return bool(has_jarvis and has_eng)


def heal_engineering_jarvis_forensic(text: str, changes: List[str]) -> str:
    """No-op pass-through (content fabrication retired in Wave 3)."""
    return text


def heal_engineering_jarvis_recap(
    text: str,
    changes: List[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """No-op pass-through (content fabrication retired in Wave 3)."""
    return text


def heal_engineering_jarvis_graph(
    data: Dict[str, Any],
    mutations: Dict[str, Any],
    changes: List[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """No-op pass-through (content fabrication retired in Wave 3)."""
    return None
