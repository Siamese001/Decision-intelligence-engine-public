"""
dual_pipeline/transcript_qa_core/gate1_attribution.py

Attribution and speaker hygiene validation for Gate 1.
Ensures rigorous speaker attribution, room mic hygiene, and participant grounding.
Re-exports ALR and cryptographic receipt verification from gate1_alr.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional



from .gate1_alr import (
    _check_generic_turn_ratio,
    _check_hitl_receipt_binding,
    _contains_speaker_attribution_grounding,
    _extract_alr_table_rows,
    _has_placeholder_role,
    check_alr_row_count_minimum,
    check_ambiguity_lock_register_and_speaker_hitl,
    verify_alr_cryptographic_receipt,
)
from .gate1_helpers import (
    C19_AFFILIATION_MARKERS,
    _extract_all_turns,
    _extract_roster_names,
    _is_generic_speaker,
    _name_from_item,
)

logger = logging.getLogger(__name__)

__all__ = [
    "check_attribution_hygiene",
    "check_alr_row_count_minimum",
    "check_ambiguity_lock_register_and_speaker_hitl",
    "verify_alr_cryptographic_receipt",
    "_check_generic_turn_ratio",
    "_check_hitl_receipt_binding",
    "_contains_speaker_attribution_grounding",
    "_extract_alr_table_rows",
    "_has_placeholder_role",
]


def _check_generic_speakers(
    f1_text: str,
    bypass: bool,
    roster_names: Optional[List[str]] = None,
) -> tuple[List[str], List[str]]:
    """Detect unresolved generic speaker identifiers and enforce 15% turn ceiling & 95% named-word coverage."""
    from .gate1_helpers import compute_released_text_coverage
    cov = compute_released_text_coverage(f1_text)
    total_turns = cov["total_displayed_turns"]
    generic_turn_count = cov["generic_turns"]
    named_word_cov = cov["named_word_coverage_pct"]

    turns = _extract_all_turns(f1_text)
    unresolved_set = {spk for _, spk, _ in turns if _is_generic_speaker(spk)}
    unresolved = sorted(unresolved_set)
    failures: List[str] = []

    if total_turns > 0:
        ratio = generic_turn_count / total_turns
        r_names = roster_names or []
        if total_turns >= 10 and ratio > 0.50:
            failures.append(
                f"UNATTRIBUTED_GENERIC_SPEAKERS_IN_OUTPUT_01: {generic_turn_count}/{total_turns} turns "
                f"({int(ratio*100)}%) are generic placeholders. Attribution failure exceeds 50% hard ceiling; bypass is disallowed."
            )
        elif not bypass:
            if len(r_names) >= 2:
                if ratio > 0.15:
                    failures.append(
                        f"UNATTRIBUTED_GENERIC_SPEAKERS_IN_OUTPUT_01: Generic un-attributed speaker turns exceed 15% "
                        f"({generic_turn_count}/{total_turns}, {int(ratio*100)}%) in Output 1 for multi-attendee meeting. "
                        "Rigorous speaker attribution HITL is required before release."
                    )
            elif generic_turn_count == total_turns or len(unresolved) > 0:
                failures.append(
                    f"UNATTRIBUTED_GENERIC_SPEAKERS_IN_OUTPUT_01: Unresolved generic speakers detected in Output 1: {unresolved}. "
                    "Rigorous speaker attribution HITL is required before release."
                )
        if len(r_names) >= 2 and total_turns >= 10 and named_word_cov < 95.0:
            failures.append(
                f"INSUFFICIENT_NAMED_WORD_COVERAGE: Released-text named-word coverage is {named_word_cov:.2f}% "
                f"(< 95.0% required). Generic dialogue words: {cov['generic_words']}/{cov['released_dialogue_words']}."
            )
    return unresolved, failures


def _check_turn_contradictions(
    ts: str,
    spk_clean: str,
    text: str,
    roster_names: List[str],
) -> Optional[str]:
    """Check if speaker label contradicts self-identification in dialogue."""
    text_lower = text.lower()
    for r_name in roster_names:
        r_first = r_name.lower().split()[0]
        r_full = r_name.lower()
        if (
            r_full != spk_clean.lower()
            and r_first not in spk_clean.lower()
            and (
                f"i'm {r_full}" in text_lower
                or f"i am {r_full}" in text_lower
                or f"this is {r_full}" in text_lower
            )
        ):
            return f"Turn [{ts}] labeled '{spk_clean}' but speaker states 'I am {r_name}'"
    return None


def _check_speaker_turns_and_contradictions(
    f1_text: str,
    roster_names: List[str],
    bypass: bool,
) -> tuple[Dict[str, int], List[str]]:
    """Extract speaker counts and identify dialogue contradictions."""
    turns = _extract_all_turns(f1_text)
    spk_counts: Dict[str, int] = {}
    contradictions: List[str] = []

    for ts, spk_clean, text in turns:
        spk_counts[spk_clean] = spk_counts.get(spk_clean, 0) + 1
        if not bypass:
            contra = _check_turn_contradictions(ts, spk_clean, text, roster_names)
            if contra:
                contradictions.append(contra)

    failures = [f"SPEAKER_DIALOGUE_CONTRADICTION: {c}" for c in contradictions[:3]]
    return spk_counts, failures


def _check_single_speaker_collapse(
    spk_counts: Dict[str, int],
    roster_names: List[str],
    bypass: bool,
) -> List[str]:
    """Flag single-speaker collapse in multi-attendee meetings."""
    if bypass or len(roster_names) < 2:
        return []
    total_turns = sum(spk_counts.values())
    if total_turns < 4:
        return []
    max_spk, max_cnt = max(spk_counts.items(), key=lambda x: x[1]) if spk_counts else ("", 0)
    ratio = max_cnt / total_turns
    if (total_turns >= 10 and ratio >= 0.85) or (total_turns < 10 and ratio >= 1.0):
        return [
            f"SINGLE_SPEAKER_COLLAPSE_DETECTED: Speaker '{max_spk}' accounts for {max_cnt}/{total_turns} ({int(ratio*100)}%) "
            f"of dialogue turns in a meeting with {len(roster_names)} attendees ({', '.join(roster_names)}). "
            "Forensic multi-speaker attribution required (max 85% single-speaker limit across >= 10 turns)."
        ]
    return []



def _check_partner_organization_attribution(
    spk_counts: Dict[str, int],
    meta: Dict[str, Any],
    bypass: bool,
) -> List[str]:
    """Ensures external partner organizations on roster have at least one dialogue turn attributed."""
    if bypass:
        return []
    roster = meta.get("roster") or meta.get("attendees")
    if not isinstance(roster, list) or len(roster) < 2:
        return []

    org_to_members: Dict[str, List[str]] = {}
    for a in roster:
        if isinstance(a, dict) and a.get("name") and a.get("org"):
            org = str(a["org"]).strip().lower()
            name = str(a["name"]).strip()
            org_to_members.setdefault(org, []).append(name)

    if len(org_to_members) < 2:
        return []

    failures: List[str] = []
    spk_map = meta.get("speaker_map") if isinstance(meta.get("speaker_map"), dict) else {}
    hitl_roles = meta.get("hitl_role_resolutions") if isinstance(meta.get("hitl_role_resolutions"), dict) else {}
    pre_roles = meta.get("pre_enrichment_roles") if isinstance(meta.get("pre_enrichment_roles"), dict) else {}
    alias_candidates = {str(v).strip() for v in spk_map.values() if v} | {str(k).strip() for k in hitl_roles} | {str(k).strip() for k in pre_roles}

    for org, members in org_to_members.items():
        expanded_members = list(members)
        for m in members:
            m_parts = m.lower().split()
            if m_parts:
                m_last = m_parts[-1]
                m_first = m_parts[0]
                for cand in alias_candidates:
                    c_parts = cand.lower().split()
                    if c_parts and (c_parts[-1] == m_last or c_parts[0].startswith(m_first) or m_first.startswith(c_parts[0])):
                        expanded_members.append(cand)
        has_turn = any(
            any(m.lower() in s.lower() or s.lower() in m.lower() for s in spk_counts)
            for m in expanded_members
        )
        if not has_turn:
            failures.append(
                f"PARTNER_ORGANIZATION_MISSING: Organization '{org}' has {len(members)} confirmed attendee(s) "
                f"on roster ({', '.join(members)}) but 0 dialogue turns attributed in Output 1."
            )
    return failures


def _extract_partner_advisory_attendees_from_roster(meta: Optional[Dict[str, Any]]) -> set[str]:
    partner_advisory_attendees: set[str] = set()
    roster = (meta or {}).get("roster") or (meta or {}).get("attendees") or []
    for item in roster:
        if isinstance(item, dict):
            name = (item.get("name") or "").strip().lower()
            org = (item.get("organization") or item.get("org") or "").strip().lower()
            if name and "partner_advisory" in org:
                partner_advisory_attendees.add(name)
        elif isinstance(item, str) and "partner_advisory" in item.lower():
            partner_advisory_attendees.add(item.strip().lower().split("(")[0].strip())
    return partner_advisory_attendees


def _check_participant_corporate_affiliations(
    f1_text: str,
    bypass: bool,
    meta: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Check for misattributed corporate affiliations dynamically from metadata roster."""
    if bypass:
        return []
    partner_advisory_attendees = _extract_partner_advisory_attendees_from_roster(meta)
    if not partner_advisory_attendees:
        return []
    failures: List[str] = []

    part_match = re.search(r"\*\*Participants:\*\*(.*?)(?:---|\n##|\Z)", f1_text, re.DOTALL)
    if part_match:
        lines = part_match.group(1).splitlines()
        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue
            header_match = re.match(r"^-\s*\*\*([^*]+)\*\*:\s*(.*)", line_str)
            if header_match:
                org_header = header_match.group(1).lower()
                people = header_match.group(2).lower()
                is_client_or_unknown = any(m in org_header for m in ("client", "unknown", "apex_global", "apex"))
                if is_client_or_unknown and ("partner_advisory" not in org_header):
                    if any(s_name in people for s_name in partner_advisory_attendees):
                        failures.append(
                            f"MISATTRIBUTED_CORPORATE_AFFILIATION: PartnerAdvisory attendee in Output 1 participants block misattributed to client/unknown: '{line_str}'."
                        )
            else:
                entries = [e.strip() for e in re.split(r"[;,]", line_str) if e.strip()]
                for entry in entries:
                    entry_low = entry.lower()
                    if any(s_name in entry_low for s_name in partner_advisory_attendees):
                        cleaned = re.sub(r"\b(?:lead\s+)?client\s+partner\b|\bclient\s+service\s+lead\b", "", entry_low)
                        if "partner_advisory" not in entry_low and any(term in cleaned for term in ("client", "unknown", "apex_global", "apex")):
                            failures.append(
                                f"MISATTRIBUTED_CORPORATE_AFFILIATION: PartnerAdvisory attendee in Output 1 participants block misattributed to client/unknown: '{entry}'."
                            )
    return failures


def _check_named_speaker_in_turns(
    spk_counts: Dict[str, int],
    roster_names: List[str],
    bypass_speaker: bool,
) -> List[str]:
    """Validate at least one named roster attendee appears in dialogue turn attributions."""
    if bypass_speaker or len(roster_names) < 2 or sum(spk_counts.values()) < 5:
        return []
    roster_in_turns = [
        r for r in roster_names
        if any(r.lower() in s.lower() or r.lower().split()[0] in s.lower() for s in spk_counts)
    ]
    if not roster_in_turns:
        return [
            f"NO_NAMED_SPEAKERS_ATTRIBUTED: Zero roster attendees ({', '.join(roster_names)}) "
            "were attributed to dialogue turns in Output 1."
        ]
    return []


def _is_role_near_timestamp(f1_text: str, roster_role: str) -> bool:
    """Check if a roster role appears within 100 chars before/200 chars after a transcript timestamp."""
    if not roster_role:
        return False
    ts_matches = [m.start() for m in re.finditer(r"\[\d{1,2}:\d{2}", f1_text)]
    if not ts_matches:
        return False
    role_lower = roster_role.lower()
    for pos in ts_matches:
        start = max(0, pos - 100)
        end = min(len(f1_text), pos + 200)
        window = f1_text[start:end].lower()
        if role_lower in window:
            return True
    return False


def _check_appendix_title_grounding(f1_text: str, roster_raw: Any) -> List[str]:
    """Check for LLM-hallucinated participant titles in transcript appendix."""
    app_match = re.search(r"## Appendix.*?(?=\n##|\Z)", f1_text, re.DOTALL | re.IGNORECASE)
    if not app_match:
        return []
    app_text = app_match.group(0)
    roster_list = roster_raw if isinstance(roster_raw, list) else (
        [item for grp in roster_raw.values() for item in (grp if isinstance(grp, list) else [grp])]
        if isinstance(roster_raw, dict) else []
    )
    roster_dict = {
        _name_from_item(item).lower(): (item.get("role", "") if isinstance(item, dict) else "")
        for item in roster_list if _name_from_item(item)
    }

    advisories = []
    for line in app_text.splitlines():
        line_clean = line.strip().lstrip("-* ").strip()
        if not line_clean or line_clean.startswith("#"):
            continue
        p_name = None
        for name in roster_dict:
            if name in line_clean.lower():
                p_name = name
                break
        if not p_name:
            continue
        expected_role = roster_dict.get(p_name, "")
        if (
            expected_role
            and expected_role.lower() not in line_clean.lower()
            and not _is_role_near_timestamp(f1_text, expected_role)
        ):
            advisories.append(
                f"POTENTIAL_TITLE_HALLUCINATION: Attendee '{p_name}' has roster role '{expected_role}', "
                f"but appendix displays potentially conflicting entry: '{line_clean}'."
            )
    return advisories


def _check_attendee_title_completeness(f1_text: str, meta: Dict[str, Any]) -> List[str]:
    """Gate 1.2a: Asserts attendee titles in Output 01 are non-placeholder and semantically grounded."""
    failures: List[str] = []
    bad_patterns = [
        r"\(specific title not provided\)",
        r"\(title not provided\)",
        r"\[Title Pending\]",
        r"\(specific role not provided\)",
    ]
    for pat in bad_patterns:
        if re.search(pat, f1_text, re.IGNORECASE):
            failures.append(
                f"UNRESOLVED_ATTENDEE_TITLE: Found placeholder pattern '{pat}' in Output 01 text. "
                "Verified professional titles are required before release."
            )
    return failures


def check_attribution_hygiene(
    f1_text: str,
    meta: Dict[str, Any],
) -> tuple[bool, List[str], Dict[str, bool], List[str]]:
    """Decomposed attribution hygiene audit satisfying cognitive complexity limits."""
    from dual_pipeline.transcript_stages_core.hitl_guard import assert_no_u0_hitl_bypass  # noqa: lazy-decouple
    assert_no_u0_hitl_bypass(meta)
    roster_names = _extract_roster_names(meta, f1_text)
    from dual_pipeline.speaker_attribution_core.collapse_detector import _verify_collapse_approval_receipt
    is_approved = _verify_collapse_approval_receipt(meta)
    unresolved_speakers, f_generic = _check_generic_speakers(f1_text, False, roster_names)
    spk_counts, f_contra = _check_speaker_turns_and_contradictions(f1_text, roster_names, False)
    f_collapse = _check_single_speaker_collapse(spk_counts, roster_names, is_approved)
    f_affil = _check_participant_corporate_affiliations(f1_text, False, meta)
    f_turns = _check_named_speaker_in_turns(spk_counts, roster_names, False)
    f_partner = _check_partner_organization_attribution(spk_counts, meta, is_approved)
    f_titles = _check_attendee_title_completeness(f1_text, meta)

    failures: List[str] = []
    failures.extend(f_generic)
    failures.extend(f_collapse)
    failures.extend(f_contra)
    failures.extend(f_affil)
    failures.extend(f_turns)
    failures.extend(f_partner)
    failures.extend(f_titles)

    speakers_attributed = {
        name: any(name.lower() in s.lower() or s.lower() in name.lower() for s in spk_counts)
        for name in roster_names
    }
    tot_turns = sum(spk_counts.values()) or 1
    meta["spk_counts"] = spk_counts
    meta["speaker_shares"] = {s: cnt / tot_turns for s, cnt in spk_counts.items()}

    roster_raw = meta.get("roster") or meta.get("attendees") or []
    ungrounded_titles = _check_appendix_title_grounding(f1_text, roster_raw)

    gate_passed = len(failures) == 0
    return gate_passed, unresolved_speakers, speakers_attributed, failures + ungrounded_titles
