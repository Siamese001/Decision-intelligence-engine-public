"""
Data models for speaker disambiguation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AttendeeContext:
    name: str
    person_id: str
    role: str
    company: str
    domain_topics: List[str] = field(default_factory=list)


@dataclass
class RoomMicConfig:
    mic_label: str
    display_label: str
    in_room_attendees: List[AttendeeContext]


@dataclass
class TurnAttribution:
    turn_index: int
    timestamp: str
    original_speaker: str
    assigned_speaker: str
    person_id: Optional[str]
    confidence: float
    second_candidate: Optional[str]
    second_confidence: float
    margin_delta: float
    attribution_mode: str  # "DIRECT" | "SHARED_ROOM_MIC"
    rationale: str
    text_snippet: str


@dataclass
class DisambiguationResult:
    resolved_transcript: str
    attribution_manifest: Dict[str, Any]
    turns: List[TurnAttribution]
    total_room_turns: int
    directly_attributed_turns: int
    shared_room_preserved_turns: int
    speaker_distribution: Dict[str, int]
    affiliation_attributed_turns: int = 0


def compute_company_scores(
    scores: Dict[str, float],
    name_to_att: Dict[str, Any],
) -> Tuple[str, float, float]:
    """Aggregates company level scores and calculates margin."""
    comp_scores: Dict[str, float] = {}
    for name, sc in scores.items():
        att_obj = name_to_att.get(name)
        c = (att_obj.company or "").strip() if att_obj else ""
        if c and c.lower() not in ("unknown", "none"):
            comp_scores[c] = max(comp_scores.get(c, 0.0), sc)
    sorted_comps = sorted(comp_scores.items(), key=lambda x: x[1], reverse=True)
    top_name, top_sc = sorted_comps[0] if sorted_comps else ("", 0.0)
    sec_sc = sorted_comps[1][1] if len(sorted_comps) > 1 else 0.0
    return top_name, top_sc, top_sc - sec_sc


def classify_offline_candidate(
    margin_threshold: float,
    top_name: str,
    top_score: float,
    second_name: Optional[str],
    margin: float,
    top_att: Any,
    top_comp_name: str,
    top_comp_sc: float,
    comp_margin: float,
    name_to_person: Dict[str, str],
    name_to_att: Dict[str, Any],
    room_config: RoomMicConfig,
) -> Tuple[str, str, Optional[str], str]:
    """Classifies offline turn attribution mode into DIRECT, AFFILIATION, or SHARED_ROOM_MIC."""
    if margin >= margin_threshold and top_score >= 0.50:
        assigned = f"{top_name} ({top_att.company})" if top_att and top_att.company else top_name
        rationale = f"High confidence lexical signal matching {top_name}'s domain topics (margin={margin:.2f})."
        return "DIRECT", assigned, name_to_person.get(top_name), rationale

    second_att = name_to_att.get(second_name) if second_name else None
    same_comp = bool(second_att and (second_att.company or "").strip().lower() == top_comp_name.lower())
    if top_comp_name and top_comp_sc >= 0.35 and (comp_margin >= margin_threshold or same_comp):
        low = top_comp_name.lower()
        clean = "PartnerAdvisory" if "partner_advisory" in low else ("NEA" if any(x in low for x in ("nea", "apex_global", "apex")) else top_comp_name)
        assigned = f"{clean} Speaker"
        rationale = f"Decisive organizational affiliation to {clean} (comp_margin={comp_margin:.2f})."
        return "AFFILIATION", assigned, f"org:{clean.lower()}", rationale

    rationale = f"Ambiguous signal or conversational crosstalk (margin={margin:.2f} < threshold {margin_threshold})."
    return "SHARED_ROOM_MIC", room_config.display_label, "collective:room-mic", rationale

