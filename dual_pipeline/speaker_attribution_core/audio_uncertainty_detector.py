"""Audio transcript uncertainty detection, role consistency auditing, and candidate scoring."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold

from .models import CandidateAttribution, TurnAttributionDiscrepancy

logger = logging.getLogger(__name__)

# Role self-attribution cues in spoken dialogue
_ROLE_PATTERNS = {
    "pm_scrum_master": re.compile(
        r"\b(?:from\s+a\s+pm\s+standpoint|as\s+(?:a\s+)?pm|from\s+scrum\s+master\s+standpoint|"
        r"as\s+scrum\s+master|looking\s+at\s+the\s+roadmap|our\s+buffer\s+here|buffer\s+here|"
        r"we're\s+where\s+we\s+thought\s+we\s+were\s+going\s+to\s+be)\b",
        re.IGNORECASE,
    ),
    "lead_architect": re.compile(
        r"\b(?:from\s+an\s+architecture\s+standpoint|as\s+(?:the\s+)?lead\s+architect|"
        r"architectural\s+diligence|enterprise\s+architecture)\b",
        re.IGNORECASE,
    ),
    "product_owner": re.compile(
        r"\b(?:as\s+(?:the\s+)?product\s+owner|from\s+product\s+standpoint|submit\s+this\s+certification)\b",
        re.IGNORECASE,
    ),
}

# Role categories for attendees
_ROLE_KEYWORDS = {
    "pm_scrum_master": ["scrum master", "project manager", "pm", "delivery lead", "principal consultant"],
    "lead_architect": ["enterprise architect", "lead enterprise architect", "solution architect", "principal architect"],
    "product_owner": ["product owner", "po", "product manager"],
    "developer_engineer": ["senior engineer", "software engineer", "developer", "tech lead"],
}


def _parse_person_entry(p: Any, default_org: str = "") -> Optional[Dict[str, str]]:
    if isinstance(p, dict):
        name = (p.get("name") or p.get("person_id") or "").strip()
        org = (p.get("org") or p.get("company") or default_org).strip()
        role = str(p.get("role", "")).strip()
        return {"name": name, "org": org, "role": role} if name else None
    if isinstance(p, str) and p.strip():
        m = re.match(r"^([^(]+)(?:\(([^)]+)\))?", p.strip())
        name = m.group(1).strip() if m else p.strip()
        role = m.group(2).strip() if (m and m.group(2)) else ""
        return {"name": name, "org": default_org.strip(), "role": role} if name else None
    return None


def _parse_dict_roster(roster_dict: Dict[str, Any]) -> List[Dict[str, str]]:
    roster = []
    for org, people in roster_dict.items():
        if isinstance(people, list):
            for p in people:
                entry = _parse_person_entry(p, default_org=org)
                if entry:
                    roster.append(entry)
    return roster


def _parse_list_roster(roster_list: List[Any]) -> List[Dict[str, str]]:
    roster = []
    for p in roster_list:
        entry = _parse_person_entry(p)
        if entry:
            roster.append(entry)
    return roster


def _parse_str_roster(roster_str: str) -> List[Dict[str, str]]:
    roster = []
    for part in roster_str.split(","):
        entry = _parse_person_entry(part)
        if entry:
            roster.append(entry)
    return roster


def extract_roster_with_roles(metadata: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extracts attendee roster with associated roles and organizations."""
    roster_data = metadata.get("roster") or metadata.get("attendees")
    if isinstance(roster_data, dict):
        return _parse_dict_roster(roster_data)
    if isinstance(roster_data, list):
        return _parse_list_roster(roster_data)
    if isinstance(roster_data, str) and roster_data.strip():
        return _parse_str_roster(roster_data)
    return []


def _speaker_matches_role_category(speaker_role: str, category: str) -> bool:
    """Checks if a speaker's formal role matches a role category."""
    if not speaker_role:
        return False
    role_lower = speaker_role.lower()
    keywords = _ROLE_KEYWORDS.get(category, [])
    return any(kw in role_lower for kw in keywords)


def detect_audio_role_discrepancies(
    transcript: str,
    metadata: Dict[str, Any],
) -> List[TurnAttributionDiscrepancy]:
    """Scans audio transcript dialogue turns for role self-attributions conflicting with current speaker."""
    roster = extract_roster_with_roles(metadata)
    if not roster:
        return []

    roster_by_name = {r["name"].lower(): r for r in roster}
    discrepancies: List[TurnAttributionDiscrepancy] = []

    # Parse dialogue turns with timestamps: ### [HH:MM:SS]\n\n**Speaker:** Text or **[MM:SS]** Speaker: Text
    turn_pattern = re.compile(
        r"(?:###\s*\[(?P<time_h>\d{1,2}:\d{2}(?::\d{2})?)\]\s*\n\s*)?"
        r"(?:\*\*(?P<spk_bold>[^*]+)\*\*:?|(?:\*\*\[(?P<time_b>\d{1,2}:\d{2}(?::\d{2})?)\]\*\*\s+)?(?P<spk_plain>[A-Z][a-zA-Z\s.,-]+):)\s*"
        r"(?P<text>[^\n]+(?:\n(?!\s*(?:###|\*\*|\b[A-Z][a-zA-Z\s.,-]+:))[^\n]+)*)",
        re.MULTILINE,
    )

    detected_clusters: Dict[str, Dict[str, Any]] = {}

    for idx, match in enumerate(turn_pattern.finditer(transcript), 1):
        spk = match.group("spk_bold") or match.group("spk_plain") or ""
        spk = spk.strip().rstrip(":")
        text = (match.group("text") or "").strip()
        ts = match.group("time_h") or match.group("time_b") or f"turn_{idx}"

        spk_info = roster_by_name.get(spk.lower(), {})
        spk_role = spk_info.get("role", "")

        for category, pat in _ROLE_PATTERNS.items():
            if pat.search(text) and not _speaker_matches_role_category(spk_role, category):
                # Discrepancy detected: speaker says "from a PM standpoint" but is an Architect/Engineer
                cluster_key = f"{spk}_{category}"
                if cluster_key not in detected_clusters:
                    detected_clusters[cluster_key] = {
                        "speaker": spk,
                        "category": category,
                        "turns": [],
                        "timestamps": [],
                        "sample_text": text,
                    }
                detected_clusters[cluster_key]["turns"].append(idx)
                detected_clusters[cluster_key]["timestamps"].append(ts)

    for _cluster_key, cinfo in detected_clusters.items():
        category = cinfo["category"]
        curr_spk = cinfo["speaker"]
        sample = cinfo["sample_text"]
        timestamps = cinfo["timestamps"]

        # Score roster members matching this role
        matching_members = [
            r for r in roster
            if _speaker_matches_role_category(r.get("role", ""), category)
        ]

        candidates: List[CandidateAttribution] = []
        for m in matching_members:
            name = m["name"]
            role = m["role"]
            org = m["org"]
            conf = 0.45
            evidence = f"Formal role '{role}' matches dialogue role self-attribution '{category}'"
            candidates.append(CandidateAttribution(
                attendee_name=name,
                organization=org,
                role=role,
                confidence=conf,
                confidence_tier="MEDIUM",
                evidence_rationale=evidence,
            ))

        # Add current speaker as low-confidence alternative
        curr_info = roster_by_name.get(curr_spk.lower(), {})
        candidates.append(CandidateAttribution(
            attendee_name=curr_spk,
            organization=curr_info.get("org", ""),
            role=curr_info.get("role", ""),
            confidence=0.10,
            confidence_tier="LOW",
            evidence_rationale=f"Original single-channel attribution; conflicting role '{curr_info.get('role', '')}'",
        ))

        # Sort candidates descending by confidence
        candidates.sort(key=lambda c: c.confidence, reverse=True)
        if candidates:
            candidates[0].is_recommended = True

        margin = 0.0
        if len(candidates) >= 2:
            margin = candidates[0].confidence - candidates[1].confidence
        elif candidates:
            margin = candidates[0].confidence

        start_ts = timestamps[0] if timestamps else "00:00:00"
        end_ts = timestamps[-1] if timestamps else start_ts

        discrepancies.append(TurnAttributionDiscrepancy(
            cluster_id=f"audio_role_discrepancy_{curr_spk.lower().replace(' ', '_')}_{start_ts.replace(':', '')}",
            current_speaker=curr_spk,
            detected_role=category,
            start_timestamp=start_ts,
            end_timestamp=end_ts,
            turn_indices=cinfo["turns"],
            sample_text=sample,
            candidates=candidates,
            margin=margin,
            is_ambiguous=(margin < get_hitl_ambiguity_margin_threshold()),
        ))

    return discrepancies


def _check_meeting_scope(
    cluster_norm: str,
    target_meeting: str,
    rec_meeting: str,
    project: Optional[str],
    rec_proj: Optional[str],
) -> bool:
    if not re.match(r"^(?:speaker(?:[_\s]?[a-z0-9]+)?|unknown|speaker\s+unknown|unattributed)$", cluster_norm):
        return True
    if target_meeting and rec_meeting:
        return target_meeting in rec_meeting or rec_meeting in target_meeting
    if target_meeting or rec_meeting:
        return False
    return bool(project or rec_proj)


def _matches_cluster(
    cluster_norm: str,
    rec_cluster: str,
    rec_segment: str,
    rec_dec_id: str,
    target_meeting: str,
) -> bool:
    """Checks if record cluster / segment matches the query cluster."""
    if cluster_norm in (rec_cluster, rec_segment):
        return True
    if f"speaker {cluster_norm}" == rec_cluster or cluster_norm == f"speaker {rec_cluster}":
        return True
    return bool(target_meeting and target_meeting in rec_dec_id and cluster_norm in rec_dec_id)


def _extract_resolved_speaker_from_record(r: Dict[str, Any], meta: Dict[str, Any]) -> Optional[str]:
    direct = meta.get("assigned_speaker") or meta.get("resolved_identity")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    sel = r.get("selected_option") or r.get("selected_key")
    if not isinstance(sel, str) or not sel:
        return None
    m_to = re.search(r"\bto\s+([^(—\-]+?)(?:\s*\(|\s*[—\-]|$)", sel)
    if m_to and "map cluster" in sel.lower():
        return m_to.group(1).strip()
    return sel.replace("(Recommended) ", "").split(" (")[0].strip()


def check_ledger_for_audio_resolution(
    cluster_id: str,
    store: Any,
    project: Optional[str] = None,
    meeting_name: Optional[str] = None,
    date: Optional[str] = None,
    slug: Optional[str] = None,
) -> Optional[str]:
    """Queries hitl_decision_ledger.json for an existing human operator resolution of this discrepancy.
    Guarantees strict meeting-slug / project isolation to prevent cross-meeting cluster leakage.
    """
    try:
        records = getattr(store, "_load_records", lambda: [])()
        cluster_norm = cluster_id.strip().lower()
        target_meeting = (meeting_name or slug or "").strip().lower()

        for r in reversed(records):
            meta = r.get("metadata", {})
            rec_proj = meta.get("project")
            if project and rec_proj and str(project).lower() != str(rec_proj).lower():
                continue

            rec_meeting = str(meta.get("meeting_name") or meta.get("slug") or "").strip().lower()
            if not _check_meeting_scope(cluster_norm, target_meeting, rec_meeting, project, rec_proj):
                continue

            rec_cluster = str(meta.get("cluster") or "").strip().lower()
            rec_segment = str(r.get("segment_id") or "").strip().lower()
            rec_dec_id = str(r.get("decision_id") or "").strip().lower()

            if _matches_cluster(cluster_norm, rec_cluster, rec_segment, rec_dec_id, target_meeting):
                resolved = _extract_resolved_speaker_from_record(r, meta)
                if resolved:
                    return resolved
    except Exception as exc:
        logger.debug(f"Could not check ledger for audio resolution: {exc}")
    return None

