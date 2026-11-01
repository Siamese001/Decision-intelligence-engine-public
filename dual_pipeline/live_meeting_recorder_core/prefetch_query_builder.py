"""
dual_pipeline/live_meeting_recorder_core/prefetch_query_builder.py

Prefetch query builders and domain synonym normalizers for live meeting sessions.
Builds prioritized graph query specifications for attendee, decision, risk, and action context.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .copilot_predicates import (
    PREFETCH_ACTION_PREDICATES,
    PREFETCH_ATTENDEE_PREDICATES,
    PREFETCH_DECISION_PREDICATES,
    PREFETCH_RISK_PREDICATES,
    PREFETCH_TOPIC_PREDICATES,
)

__all__ = [
    "DOMAIN_ACRONYM_MAP",
    "_PROJECT_SYNONYMS",
    "build_prefetch_query_specs",
    "normalize_project_terms",
]

DOMAIN_ACRONYM_MAP: Dict[str, Tuple[str, ...]] = {
    "scada": ("anchor_edo_rfp_scada_tracker", "pi", "historian", "ems"),
    "rocc": ("anchor_edo_rfp_rocc_rso_greensheet", "rso", "greensheet", "operator"),
    "ems": ("anchor_edo_rfp_scada_tracker", "nerc", "cip"),
    "pano": ("pano_ai", "wildfire", "cameras"),
    "substation": ("workstream_substation_onboarding",),
}

_PROJECT_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "project_nexus": ("nexus", "gcp", "platform delivery"),
    "dociq": ("dociq", "document intelligence", "documentum", "pgd"),
    "project_jarvis": ("jarvis", "capital delivery", "developer copilot"),
    "project_lens": ("lens", "sow 1", "computer vision", "drone inspection"),
    "project_beacon": ("sherpa", "cloud workflow", "migration"),
    "apex_trans": ("apex_trans", "transmission", "insights hub", "bcg", "scada", "rocc"),
    "project_atlas": ("rewire", "infrastructure", "substation"),
    "enterprise_account_planning": ("account planning", "florida", "enr"),
}


def normalize_project_terms(project_name: str) -> List[str]:
    """Normalizes project names to canonical search tokens and known domain synonyms."""
    if not project_name or project_name == "UNRESOLVED":
        return []
    raw = project_name.strip().lower()
    clean = re.sub(r"[_\-]+", " ", raw)
    terms = [clean] if clean else []
    if raw in _PROJECT_SYNONYMS:
        for syn in _PROJECT_SYNONYMS[raw]:
            if syn not in terms:
                terms.append(syn)
    return terms


def _add_meeting_topic_spec(specs: List[Dict[str, Any]], meeting_name: str) -> None:
    clean_meeting = re.sub(r"[_\-]+", " ", (meeting_name or "").strip())
    if (
        clean_meeting
        and len(clean_meeting) > 3
        and clean_meeting.lower() not in ("live meeting", "meeting", "call")
    ):
        meeting_tokens = re.findall(r"\b[A-Za-z0-9]+\b", clean_meeting.lower())
        expansions: List[str] = []
        for t in meeting_tokens:
            if t in DOMAIN_ACRONYM_MAP:
                expansions.extend(DOMAIN_ACRONYM_MAP[t])
        expansion_str = f" {' '.join(expansions)}" if expansions else ""
        specs.append({
            "query_type": "MEETING_TOPIC",
            "query_text": f"{clean_meeting}{expansion_str}",
            "priority_predicates": PREFETCH_TOPIC_PREDICATES,
            "max_results": 5,
        })


def _format_attendee_str(att: Dict[str, Any]) -> Optional[str]:
    if not isinstance(att, dict):
        return None
    name = str(att.get("name") or "").strip()
    role = str(att.get("role") or "").strip()
    org = str(att.get("organization") or att.get("org") or "").strip()
    if name and name.lower() not in ("speaker", "unknown", "unattributed"):
        return f"{name} {role} {org}".strip()
    return None


def _add_attendee_specs(specs: List[Dict[str, Any]], attendees: Optional[List[Dict[str, str]]]) -> None:
    if not attendees:
        return
    valid_attendees: List[str] = []
    for att in attendees:
        formatted = _format_attendee_str(att)
        if formatted:
            valid_attendees.append(formatted)
            specs.append({
                "query_type": "ATTENDEE",
                "query_text": formatted,
                "priority_predicates": PREFETCH_ATTENDEE_PREDICATES,
                "max_results": 3,
            })
    if valid_attendees:
        specs.append({
            "query_type": "ATTENDEES",
            "query_text": " ".join(valid_attendees),
            "priority_predicates": PREFETCH_ATTENDEE_PREDICATES,
            "max_results": 5,
        })


def build_prefetch_query_specs(
    project_name: str = "",
    meeting_name: str = "",
    attendees: Optional[List[Dict[str, str]]] = None,
) -> List[Dict[str, Any]]:
    """Builds a list of prioritized query specifications for pre-fetching graph context."""
    specs: List[Dict[str, Any]] = []
    proj_terms = normalize_project_terms(project_name)
    primary_proj_term = " ".join(proj_terms) if proj_terms else "project"
    specs.append({
        "query_type": "DECISIONS",
        "query_text": f"{primary_proj_term} decision pending open question roadmap charter",
        "priority_predicates": PREFETCH_DECISION_PREDICATES,
        "max_results": 4,
    })
    specs.append({
        "query_type": "RISKS",
        "query_text": f"{primary_proj_term} risk blocker constraint failure exposure stall",
        "priority_predicates": PREFETCH_RISK_PREDICATES,
        "max_results": 4,
    })
    specs.append({
        "query_type": "ACTION_ITEMS",
        "query_text": f"{primary_proj_term} action item deliverable commitment deadline next step",
        "priority_predicates": PREFETCH_ACTION_PREDICATES,
        "max_results": 4,
    })
    _add_meeting_topic_spec(specs, meeting_name)
    _add_attendee_specs(specs, attendees)
    return specs
