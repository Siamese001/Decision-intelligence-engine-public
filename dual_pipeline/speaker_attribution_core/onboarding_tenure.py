"""Onboarding attendee tenure conflict detection for speaker attribution.

Prevents recently joined or onboarding attendees from being falsely attributed
turns referencing historical engagement events, past sprint demos, or prior configuration.
"""

import re
from typing import Any, Dict, List, Optional, Set


HISTORICAL_ENGAGEMENT_PATTERNS: List[re.Pattern] = [
    re.compile(r"\b(?:our|the|last)\s+demo\b", re.IGNORECASE),
    re.compile(r"\bwe\s+set\s+up\s+notifications\b", re.IGNORECASE),
    re.compile(r"\bnotifications?\s+for\s+when\s+items\s+are\s+in\s+review\b", re.IGNORECASE),
    re.compile(r"\bfrom\s+jen\b", re.IGNORECASE),
    re.compile(r"\bjen\s+has\s+been\s+off\s+this\s+project\b", re.IGNORECASE),
    re.compile(r"\bin\s+the\s+last\s+sprint\b", re.IGNORECASE),
    re.compile(r"\bwe\s+demoed\b", re.IGNORECASE),
    re.compile(r"\bwe\s+showed\s+it\s+in\s+our\s+last\s+demo\b", re.IGNORECASE),
]


def _extract_roster_onboarding_names(roster: Any) -> Set[str]:
    names: Set[str] = set()
    attendees: List[Any] = []
    if isinstance(roster, dict):
        for group in roster.values():
            if isinstance(group, list):
                attendees.extend(group)
    elif isinstance(roster, list):
        attendees.extend(roster)

    for att in attendees:
        if isinstance(att, dict) and att.get("is_onboarding"):
            names.add(str(att.get("name", "")).strip().lower())
    return names


def extract_onboarding_names(metadata: Dict[str, Any]) -> Set[str]:
    """Extracts lowercase normalized names of all attendees marked as onboarding or recent transitions."""
    names: Set[str] = set()
    for item in metadata.get("onboarding_attendees", []):
        names.add(str(item).strip().lower())
    for item in metadata.get("recent_transitions", []):
        name = item.get("name", "") if isinstance(item, dict) else str(item)
        names.add(str(name).strip().lower())

    roster = metadata.get("roster") or metadata.get("attendees") or []
    names.update(_extract_roster_onboarding_names(roster))
    return names


def detect_onboarding_historical_conflict(
    candidate_name: str,
    full_text_lower: str,
    metadata: Dict[str, Any],
) -> Optional[str]:
    """Detects when an onboarding attendee is credited with turns referencing historical engagement work."""
    if not candidate_name or not full_text_lower or not metadata:
        return None

    onboarding_names = extract_onboarding_names(metadata)
    if candidate_name.lower().strip() not in onboarding_names:
        return None

    for pat in HISTORICAL_ENGAGEMENT_PATTERNS:
        match = pat.search(full_text_lower)
        if match:
            return match.group(0).strip()
    return None
