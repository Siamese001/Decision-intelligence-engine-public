"""
dual_pipeline/live_meeting_recorder_core/c19_firewall.py

Single Source of Truth (SSOT) for Rule C-19 Career Leverage & Privacy Firewall.
Enforces fail-closed protection against:
1. Career leverage, personal advancement, compensation trajectory, and political power-mapping.
2. Quarantined shadow-graph nodes and internal advisory enclaves (namespace: 'INTERNAL_ADVISORY').
3. Leaks of confidential career or political strategies into candidate retrievals or prompts.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.c19_firewall")

# Canonical Rule C-19 Forbidden Markers (exact case-insensitive substring matches)
C19_FORBIDDEN_MARKERS: frozenset[str] = frozenset(
    {
        "$1.2m",
        "executive compensation band",
        "internal_advisory",
        "networking_wedge",
        "political_rationale",
        "political power map",
        "political materiality",
        "political maneuver",
        "political wedge",
        "career_leverage",
        "career leverage",
        "career strategy",
        "career capital",
        "career positioning",
        "career advisory",
        "career notes",
        "compensation_trajectory",
        "compensation trajectory",
        "compensation negotiation",
        "compensation leverage",
        "stealth_score",
        "personal equity expansion",
        "personal promotion",
        "advancement vector",
    }
)

# Canonical Regex Patterns for boundary-accurate detection
C19_FORBIDDEN_PATTERNS: List[re.Pattern] = [
    re.compile(r"\$1\.2[mM]\b"),
    re.compile(r"\binternal[_\s]+advisory\b", re.IGNORECASE),
    re.compile(r"\bcareer[_\s]+(?:strategy|leverage|capital|positioning|advisory|notes)\b", re.IGNORECASE),
    re.compile(r"\bpolitical[_\s]+(?:power[_\s]+map|materiality|maneuver|wedge|rationale)\b", re.IGNORECASE),
    re.compile(r"\bcompensation[_\s]+(?:trajectory|negotiation|leverage)\b", re.IGNORECASE),
    re.compile(r"\bstealth[_\s]+score\b", re.IGNORECASE),
    re.compile(r"\bnetworking[_\s]+wedge\b", re.IGNORECASE),
    re.compile(r"\bpersonal[_\s]+(?:equity[_\s]+expansion|promotion)\b", re.IGNORECASE),
    re.compile(r"\badvancement[_\s]+vector\b", re.IGNORECASE),
]

NODE_C19_KEYS: Tuple[str, ...] = (
    "node_id",
    "id",
    "canonical_name",
    "display_name",
    "description",
    "aliases",
    "notes",
    "organization",
    "org",
    "properties",
    "metadata",
)

ASSERTION_C19_KEYS: Tuple[str, ...] = (
    "claim_text",
    "claim",
    "predicate",
    "subject_node_id",
    "subject",
    "object_node_id",
    "object",
    "object_literal",
    "notes",
    "reasoning",
    "political_rationale",
    "networking_wedge",
    "metadata",
)


def _match_c19_string(raw_str: str) -> bool:
    """Matches raw string against markers and pre-compiled regex patterns."""
    lower_str = raw_str.lower()
    clean_str = lower_str.replace("_", " ").replace("-", " ")
    for marker in C19_FORBIDDEN_MARKERS:
        if marker in lower_str or marker in clean_str:
            return True
    for p in C19_FORBIDDEN_PATTERNS:
        if p.search(raw_str) or p.search(clean_str):
            return True
    return False


def _has_c19_in_sequence(val: Any) -> bool:
    for item in val:
        if has_c19_marker(item):
            return True
    return False


def _has_c19_in_mapping(val: Dict[Any, Any]) -> bool:
    for k, v in val.items():
        if has_c19_marker(k) or has_c19_marker(v):
            return True
    return False


def has_c19_marker(val: Any) -> bool:
    """
    SSOT verification for Rule C-19 Career Leverage & Internal Advisory firewall.
    Inspects strings, lists, dicts, and nested structures for prohibited markers.
    """
    if val is None:
        return False

    if isinstance(val, (list, tuple, set, frozenset)):
        return _has_c19_in_sequence(val)

    if isinstance(val, dict):
        return _has_c19_in_mapping(val)

    raw_str = str(val).strip()
    if not raw_str:
        return False

    return _match_c19_string(raw_str)


def _has_quarantine_flags(entity: Dict[str, Any]) -> bool:
    """Checks namespace and quarantine/stealth flags."""
    if entity.get("namespace") == "INTERNAL_ADVISORY":
        return True
    if entity.get("is_quarantined") in (1, True, "1"):
        return True
    stealth = entity.get("stealth_score")
    if stealth is not None:
        try:
            return float(stealth) > 0.0
        except (ValueError, TypeError):
            return True
    return False


def _inspect_dict_fields(d: Dict[str, Any], keys: Tuple[str, ...]) -> bool:
    """Checks designated dictionary keys for prohibited C-19 markers."""
    for k in keys:
        val = d.get(k)
        if val is not None and has_c19_marker(val):
            return True
    return False


def is_c19_quarantined_node(node_id: str, node: Any) -> bool:
    """
    Deep inspection verifying whether a graph node violates Rule C-19.
    Evaluates:
    1. Node ID for C-19 markers.
    2. Namespace == 'INTERNAL_ADVISORY' or is_quarantined == 1 or stealth_score > 0.
    3. Deep inspection across all 7 node attributes (and metadata/properties).
    """
    if not node_id or has_c19_marker(str(node_id)):
        return True

    if not isinstance(node, dict):
        return True

    if _has_quarantine_flags(node):
        return True

    return _inspect_dict_fields(node, NODE_C19_KEYS)


def is_c19_quarantined_assertion(assertion: Dict[str, Any], aid: Optional[str] = None) -> bool:
    """
    Deep inspection verifying whether an assertion claim violates Rule C-19.
    Evaluates:
    1. Assertion ID for C-19 markers.
    2. Namespace == 'INTERNAL_ADVISORY', is_quarantined == 1, or stealth_score > 0.
    3. Deep inspection of claim_text, predicate, subject, object, notes, and reasoning.
    """
    if not isinstance(assertion, dict):
        return True

    actual_aid = aid or assertion.get("assertion_id") or assertion.get("id") or ""
    if actual_aid and has_c19_marker(str(actual_aid)):
        return True

    if _has_quarantine_flags(assertion):
        return True

    return _inspect_dict_fields(assertion, ASSERTION_C19_KEYS)
