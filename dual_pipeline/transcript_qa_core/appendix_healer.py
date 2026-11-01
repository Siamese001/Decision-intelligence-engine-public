"""
Appendix Healer Module for Output 02 Executive Recaps.

Ensures executive recaps for meetings >= 15 minutes contain the mandatory
'# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown' section,
generating rich, Copilot-style thematic narratives grounded in spoken dialogue or
stakeholder roles rather than generic boilerplate.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import (
    clean_attendee_entry, is_blacklisted_attendee, is_metadata_header_label,
    normalize_company_name, split_outside_parens, strip_org_suffix_from_role,
)
from .appendix_topics import (
    _DISTINCT_SILENT_TOPICS,
    _INTRO_TEMPLATES,
    _SPEAKER_FALLBACK_POOLS,
    speaker_header_present as _speaker_header_present,
    try_append_missing_appendix_speakers,
)

_SILENT_ATTENDEE_TEMPLATES = _DISTINCT_SILENT_TOPICS

logger = logging.getLogger(__name__)


def _entries_from_roster_list(roster: List[Any], seen: set, entries: List[Tuple[str, str, str]]) -> None:
    for item in roster:
        if isinstance(item, dict):
            name, role, org = item.get("name", "").strip(), item.get("role", "Participant"), item.get("org", item.get("company", "Organization Unverified"))
        elif isinstance(item, str) and item.strip():
            name, role, org = clean_attendee_entry(item.strip())
        else:
            continue
        if name and not is_blacklisted_attendee(name) and name not in seen:
            seen.add(name)
            entries.append((name, role, org))


def _entries_from_roster_dict(roster: Dict[str, Any], seen: set, entries: List[Tuple[str, str, str]]) -> None:
    for org, members in roster.items():
        if not is_metadata_header_label(org):
            for m in (members if isinstance(members, list) else [members]):
                _add_entry_if_valid(str(m), org, seen, entries)


def _add_entry_if_valid(raw_entry: str, org: str, seen: set, entries: List[Tuple[str, str, str]]) -> None:
    name, role, org_inf = clean_attendee_entry(raw_entry.strip())
    if name and not is_blacklisted_attendee(name) and not is_metadata_header_label(name) and name not in seen:
        seen.add(name)
        entries.append((name, role, org or org_inf))


def _parse_text_entry_line(line_s: str, current_org: str, seen: set, entries: List[Tuple[str, str, str]]) -> str:
    comp_m = re.match(r"^(?:[*-]\s*)?\*{0,2}([A-Za-z0-9\s/&—–\(\)]+?)(?:\*{0,2}:\*{0,2}|:\*{0,2})\s*$", line_s)
    if comp_m and not is_metadata_header_label(comp_m.group(1).strip()):
        return normalize_company_name(comp_m.group(1).strip())
    inline_m = re.match(r"^[*-]\s+\*{0,2}([A-Za-z0-9\s/&—–\(\)]+?)\*{0,2}:\s+(.+)$", line_s)
    if inline_m:
        org_cand = inline_m.group(1).strip()
        new_org = normalize_company_name(org_cand) if not is_metadata_header_label(org_cand) else current_org
        for part in split_outside_parens(inline_m.group(2)):
            _add_entry_if_valid(part, new_org, seen, entries)
        return new_org
    sub_m = re.match(r"^(?:[-*]|\s+[-*])\s+(.+)$", line_s)
    if sub_m:
        _add_entry_if_valid(sub_m.group(1), current_org, seen, entries)
    return current_org


def _entries_from_text_block(text: str, seen: set, entries: List[Tuple[str, str, str]]) -> None:
    m_att = re.search(r"(?:^|\n)(?:#{1,3}\s+Attendees[:\s]*|\*\*Attendees:?\*\*|Attendees:)\s*\n?([\s\S]*?)(?=\n\s*(?:---|___|\*\*\*|#{1,3}\s+(?!Attendees)\w|\Z))", text, re.IGNORECASE)
    if not m_att:
        return
    current_org = "Organization Unverified"
    for line in m_att.group(1).strip().splitlines():
        line_s = line.strip()
        if line_s and not line_s.startswith(("#", "|", "---")):
            current_org = _parse_text_entry_line(line_s, current_org, seen, entries)


def _enrich_attendees_from_output_1(output_1: str, seen: set, entries: List[Tuple[str, str, str]]) -> None:
    pass


def _normalize_unverified_attendee_orgs(entries: List[Tuple[str, str, str]]) -> None:
    for i, (name, role, org) in enumerate(entries):
        if not org or org == "Organization Unverified":
            entries[i] = (name, role, "Organization Unverified")


def _extract_attendee_details(
    text: str, metadata: Optional[Dict[str, Any]] = None, output_1: Optional[str] = None
) -> List[Tuple[str, str, str]]:
    """Extracts structured (name, role, org) tuples for each attendee, deduplicated."""
    meta = metadata or {}
    roster = meta.get("roster") or meta.get("attendees")
    entries: List[Tuple[str, str, str]] = []
    seen: set = set()

    if isinstance(roster, list):
        _entries_from_roster_list(roster, seen, entries)
    elif isinstance(roster, dict):
        _entries_from_roster_dict(roster, seen, entries)

    _entries_from_text_block(text, seen, entries)

    if output_1:
        _enrich_attendees_from_output_1(output_1, seen, entries)

    _normalize_unverified_attendee_orgs(entries)

    # Reconcile attendee roles with authoritative resolutions
    auth_roles = {}
    for d in (meta.get("speaker_role_resolutions"), meta.get("hitl_role_resolutions")):
        for k, v in (d or {}).items():
            r = (v.get("role") or v.get("resolved_role") or "") if isinstance(v, dict) else str(v or "")
            if r:
                auth_roles[k.lower()] = strip_org_suffix_from_role(r.strip())

    if auth_roles:
        entries = [
            (n, auth_roles.get(n.lower()) or auth_roles.get(re.sub(r"\(.*?\)", "", n).strip().lower()) or strip_org_suffix_from_role(r.split(" / ")[0]), o)
            for n, r, o in entries
        ]

    return entries


def _extract_speaker_dialogue(output_1: str, speaker_name: str) -> List[str]:
    """Extract spoken dialogue turns for a specific speaker from forensic transcript."""
    if not output_1:
        return []
    turns: List[str] = []
    first_name = speaker_name.split()[0].lower()
    full_name_clean = re.sub(r"[*_#:`]", "", speaker_name).strip().lower()

    pattern = re.compile(
        r"(?:^|\n)\s*[*]{0,2}(?:\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s*)?[*]{0,2}([A-Za-z0-9\s\.\,\'\-]{1,50}?)(?:[*]{2}\s*\[\d{1,2}:\d{2}(?::\d{2})?\]\s*:|(?::[*]{2}|[*]{2}:|:|\s*[—–][^*\n]*[*]{0,2}))\s*([\s\S]*?)(?=(?:\n\s*[*]{0,2}(?:\[?\d{1,2}:\d{2}|[A-Za-z0-9\s\.\,\'\-]{1,50}(?:[*]{2}\s*\[\d{1,2}:\d{2}(?::\d{2})?\]\s*:|(?::[*]{2}|[*]{2}:|:|\s*[—–]))))|\n#{1,3}\s+|\Z)",
        re.MULTILINE,
    )

    for m in pattern.finditer(output_1):
        spk = re.sub(r"[*_#:`]", "", m.group(1)).strip().lower()
        if spk == full_name_clean or (len(first_name) >= 3 and first_name in spk.split()):
            content = m.group(2).strip()
            clean_content = re.sub(r"\*\(In-Meeting Chat[^\)]*\)\*", "", content, flags=re.IGNORECASE)
            clean_content = re.sub(r"\*\(In-Meeting Chat[^\n]*", "", clean_content, flags=re.IGNORECASE)
            clean_content = re.sub(r"\[Natural pause:[^\]]*\]", "", clean_content, flags=re.IGNORECASE)
            clean_content = re.sub(r"\[Pause:[^\]]*\]", "", clean_content, flags=re.IGNORECASE)
            clean_content = re.sub(r"\n\s*\[\d{1,2}:\d{2}(?::\d{2})?\]\s*", " ", clean_content)
            clean_content = " ".join(clean_content.split())
            if len(clean_content.split()) >= 1:
                turns.append(clean_content)

    return turns


def _clean_dialogue_excerpt(turn: str, max_chars: int = 180) -> str:
    cleaned = re.sub(r"\*\(In-Meeting Chat[^\)]*\)\*", "", turn, flags=re.IGNORECASE)
    cleaned = re.sub(r"\*\(In-Meeting Chat[^\n]*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\[Natural pause:[^\]]*\]", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\[Pause:[^\]]*\]", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\[\d{1,2}:\d{2}(?::\d{2})?\]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > max_chars:
        return cleaned[:max_chars].rsplit(" ", 1)[0] + "..."
    return cleaned


def _is_banter_or_greeting(turn: str) -> bool:
    """Filter out pre-meeting banter, clothing/weather remarks, and trivial greetings."""
    words = turn.strip().split()
    if len(words) < 8:
        return True
    tl = turn.lower()
    banter_phrases = (
        "wearing jacket", "pocket square", "pocket doesn", "raining in miami", "just gnarly",
        "huge party", "fundraising season", "i pledge", "record the session",
        "turn off your video", "turn it on now", "please don't send me back",
        "take care. bye. hello", "about time we'll see", "how are you doing today",
        "how many people make it back on time", "minnesota is great", "lunch",
        "loud music", "event back there", "partying", "weather", "still getting some",
        "those are wednesday", "good afternoon", "good morning", "hi, how are you",
        "good to see you", "can't make it", "couldn't make it", "won't be able to make it",
    )
    return any(bp in tl for bp in banter_phrases) and len(words) < 25


_VERBS = ("addressed", "analyzed", "discussed", "highlighted", "examined", "detailed")


def _categorize_dialogue_turn(turn: str, name: str, idx: int = 0) -> Tuple[str, str]:
    """Map a substantive spoken turn to a domain category and narrative lead-in."""
    tl = turn.lower()
    v = _VERBS[idx % len(_VERBS)]
    exc = _clean_dialogue_excerpt(turn, max_chars=160)
    if any(k in tl for k in ("salesforce", "commercial", "deals", "targets", "pursuit", "funding", "credits", "co-sell")):
        return ("Commercial Strategy & Joint Pipeline", f"{name} {v} joint sales pipeline and commercial coordination: \"{exc}\"")
    if any(k in tl for k in ("voucher", "vouchers", "sow", "statement of work", "amendment")):
        return ("Commercial Contracting & Funding Alignment", f"{name} {v} deployment voucher funding and SOW structure: \"{exc}\"")
    if any(k in tl for k in ("enablement", "academy", "training", "curriculum", "modules", "courseware")):
        return ("User Enablement & Educational Curriculum", f"{name} {v} user enablement and training delivery: \"{exc}\"")
    if any(k in tl for k in ("firewall", "security", "compliance", "sox", "audit", "fedramp", "pci", "hipaa", "zero trust")):
        return ("Security & Compliance Controls", f"{name} {v} security controls and compliance boundaries: \"{exc}\"")
    if any(k in tl for k in ("pattern", "patterns", "catalog", "bespoke", "extensible")):
        return ("Reusable Deployment Patterns", f"{name} {v} reusable deployment patterns and modular design: \"{exc}\"")
    if any(k in tl for k in ("terraform", "state", "pipeline", "github actions", "infra as code", "iac")):
        return ("Infrastructure as Code & Pipeline Architecture", f"{name} {v} automation workflows and infrastructure provisioning: \"{exc}\"")
    if any(k in tl for k in ("operating model", "federated", "centralized", "hub and spoke", "adoption", "workshop")):
        return ("Operating Model & Team Alignment", f"{name} {v} cross-functional collaboration and operating cadence: \"{exc}\"")
    if any(k in tl for k in ("request", "handoff", "ticket", "domain", "friction", "provision", "self-service")):
        return ("Developer Experience & Request Flow", f"{name} {v} intake workflows and cross-team handoffs: \"{exc}\"")
    if any(k in tl for k in ("buccaneer", "servicenow", "backstage", "portal", "entry point", "demo", "cmdb")):
        return ("Platform Integration & Service Catalog", f"{name} {v} platform integration and portal touchpoints: \"{exc}\"")
    if any(k in tl for k in ("risk", "register", "mitigation", "contingency", "blocker")):
        return ("Risk Management & Contingency Planning", f"{name} {v} delivery risks and mitigation priorities: \"{exc}\"")
    if any(k in tl for k in ("program", "alignment", "roadmap", "milestone", "portfolio")):
        return ("Program Governance & Strategic Scope", f"{name} {v} program milestones and strategic alignment: \"{exc}\"")
    if "?" in turn:
        return ("Technical Inquiry & Scope Clarification", f"{name} {v} scope questions and dependency clarifications: \"{exc}\"")
    return ("Execution & Delivery Perspectives", f"{name} {v} account execution and delivery priorities: \"{exc}\"")


def _single_turn_intro_bullets(name: str, role: str, org: str, dialogue: List[str], intro_idx: int = 0) -> List[str]:
    """Generates non-hallucinatory, distinct bullets for participants with only introduction turns."""
    cr, co = role or "Participant", org or "Organization Unverified"
    ft = cr.split()[0] if cr else "Technical"
    exc = _clean_dialogue_excerpt(dialogue[0], max_chars=120) if dialogue else "Attendance confirmed."
    kw = dict(first_tok=ft, name=name, clean_role=cr, clean_org=co, exc=exc)
    p = _INTRO_TEMPLATES[intro_idx % len(_INTRO_TEMPLATES)]
    return [p[0].format(**kw), p[1].format(**kw)]


def _dialogue_topics(name: str, dialogue: List[str], role: str = "", org: str = "", intro_idx: int = 0, speaker_idx: int = 0) -> List[str]:
    """Generates substantive Copilot-depth bullet points from extracted spoken dialogue turns."""
    substantive = [t for t in dialogue if not _is_banter_or_greeting(t)] if len(dialogue) > 1 else []
    if not substantive:
        return _single_turn_intro_bullets(name, role, org, dialogue, intro_idx=intro_idx)

    bullets, seen_cats = [], set()
    for idx, t in enumerate(substantive):
        cat, bullet = _categorize_dialogue_turn(t, name, idx=idx + speaker_idx)
        if cat not in seen_cats:
            seen_cats.add(cat)
            bullets.append(f"- **{cat}**: {bullet}")
        elif len(bullets) < 2 and idx >= 1:
            alt = "Delivery Execution & Verification" if cat != "Delivery Execution & Verification" else "Program Governance & Strategic Scope"
            seen_cats.add(alt)
            bullets.append(f"- **{alt}**: {name} {_VERBS[(idx + speaker_idx + 1) % len(_VERBS)]} operational readiness and delivery checkpoints: \"{_clean_dialogue_excerpt(t, max_chars=160)}\"")
        if len(bullets) >= 4:
            break
    return bullets


def _synthesize_speaker_section(name: str, role: str, org: str, dialogue: List[str], intro_idx: int = 0, speaker_idx: int = 0, meeting_ctx: str = "") -> Optional[str]:
    """Synthesizes rich, multi-thematic Copilot-style bullet breakdowns for an attendee with dialogue."""
    if not dialogue:
        return None
    role_clean = (role or "Participant")[:-len(f", {org}")] if (role or "Participant").endswith(f", {org}") else (role or "Participant")
    org_clean = org or "Organization Unverified"
    role_suffix = f", {role_clean}" if role_clean and role_clean != "Participant" else ""
    header = f"# {name} — {role_clean}" if not org or org == "Organization Unverified" else f"# {name} — {org}{role_suffix}"
    topics = _dialogue_topics(name, dialogue, role=role_clean, org=org_clean, intro_idx=intro_idx, speaker_idx=speaker_idx) or _single_turn_intro_bullets(name, role_clean, org_clean, dialogue, intro_idx=intro_idx)
    return f"{header}\n" + "\n".join(topics)


def _is_valid_existing_appendix(
    text: str,
    duration_minutes: Optional[int] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> bool:
    from dual_pipeline.transcript_qa_core.appendix_standard import is_valid_gold_standard_appendix

    shares = meta.get("speaker_shares") if meta else None
    primary = meta.get("primary_speakers") if meta else None
    return is_valid_gold_standard_appendix(
        text,
        duration_minutes=duration_minutes,
        speaker_shares=shares,
        primary_speakers=primary,
    )


def _resolve_healer_duration(text: str, meta: Dict[str, Any]) -> int:
    from dual_pipeline.transcript_duration_helper import resolve_meeting_duration

    dur = resolve_meeting_duration(meta or {}, text)
    if dur is not None and dur > 0:
        return int(dur)
    has_deep = bool(
        re.search(r"#{1,3}\s+Strategic Synthesis", text)
        or re.search(r"#{1,3}\s+(?:Immediate Delivery Scope|Bounded Execution Wedge)", text)
    )
    return 30 if has_deep else 0


def _has_prohibited_appendix_patterns(text: str) -> bool:
    if not re.search(r"(?:^|\n)#{1,4}\s+Appendix\b", text, re.IGNORECASE):
        return True
    if re.search(
        r"\*\(In-Meeting Chat|\[Natural pause:|Operational Engagement & Discussion Priorities"
        r"|Assets per turbine and the uncertain work-order relationship|As recap analysis"
        r"|Assets and work-order quantity|Collection footprint and turbine estimate"
        r"|Time-qualified completion status|\*\*Analysis:\*\*|Asset quantity and unresolved"
        r"|Active collection footprint and tentative|Completed work orders and reporting"
        r"|Core Discussion Context & Initial Focus|Operational Focus & Priorities"
        r"|Technical Reasoning & Systems Exploration|Delivery Alignment & Next Steps"
        r"|Forensic Attribution Boundary|Technical Analysis & Methodology"
        r"|Delivery Safeguards & Operational Focus|Alignment, Tradeoffs & Next Steps"
        r"|established key context for|mechanisms and technical considerations|execution confidence and safeguards"
        r"|established core operational context for|With a single attributed spoken turn recorded"
        r"|The acoustic transcript registers one focused spoken contribution"
        r"|tracking core agenda items and technical walk-throughs"
        r"|Outlining initial focus areas.*noted: \"You're actually wearing jackets"
        r"|Primary audio telemetry logged zero spoken utterances"
        r"|Deployment Vouchers & SOW Mechanics",
        text,
        re.IGNORECASE,
    ):
        return True
    if len(text.split()) > 5000:
        return True
    return False


def _has_missing_appendix_attendees(text: str, meta: Dict[str, Any], output_1: Optional[str]) -> bool:
    for name, _role, _org in _extract_attendee_details(text, meta):
        if any(term in name.lower() for term in ("endpoint", "vc", "room", "nea-mole")):
            continue
        if output_1 and not _extract_speaker_dialogue(output_1, name):
            continue
        if not _speaker_header_present(text, name):
            return True
    return False


def _is_existing_appendix_invalid(
    text: str,
    meta: Dict[str, Any],
    output_1: Optional[str] = None,
) -> bool:
    """Validate whether existing appendix meets structural, persona, and C21 requirements."""
    if _has_prohibited_appendix_patterns(text):
        return True
    if _has_missing_appendix_attendees(text, meta, output_1):
        return True

    from dual_pipeline.prompts.contracts.output2_contract import validate_c21_depth_floors_and_anchors

    c21_ok, _ = validate_c21_depth_floors_and_anchors(text, meta=meta)
    return not c21_ok


def _synthesize_silent_speaker_sections(silent_attendees: List[Tuple[str, str, str]]) -> List[str]:
    sections: List[str] = []
    for idx, (name, role, org) in enumerate(silent_attendees):
        role_clean = role or "Participant"
        org_clean = org or "Organization Unverified"
        t = _DISTINCT_SILENT_TOPICS[idx % len(_DISTINCT_SILENT_TOPICS)]
        topics = [
            t[0].format(name=name, clean_role=role_clean, clean_org=org_clean),
            t[1].format(name=name, clean_role=role_clean, clean_org=org_clean),
        ]
        header = f"# {name} — {org_clean}, {role_clean}" if (role_clean and role_clean != "Participant") else f"# {name} — {org_clean}"
        sections.append(f"{header}\n" + "\n".join(topics))
    return sections


def _partition_appendix_entries(
    entries: List[Tuple[str, str, str]], output_1: Optional[str], meeting_ctx: str
) -> Tuple[List[str], List[Tuple[str, str, str]]]:
    speaker_sections: List[str] = []
    silent_attendees: List[Tuple[str, str, str]] = []
    intro_count = 0
    for idx, (name, role, org) in enumerate(entries):
        if any(term in name.lower() for term in ("endpoint", "vc", "room", "nea-mole")):
            silent_attendees.append((name, role, org))
            continue
        dialogue = _extract_speaker_dialogue(output_1 or "", name)
        if not dialogue:
            silent_attendees.append((name, role, org))
            continue
        sec = _synthesize_speaker_section(name, role, org, dialogue, intro_idx=intro_count, speaker_idx=idx, meeting_ctx=meeting_ctx)
        if len(dialogue) <= 1 or not [t for t in dialogue if not _is_banter_or_greeting(t)]:
            intro_count += 1
        if sec:
            speaker_sections.append(sec)
    return speaker_sections, silent_attendees


def heal_output_2_appendix(
    text: str,
    metadata: Optional[Dict[str, Any]] = None,
    changes: Optional[List[str]] = None,
    output_1: Optional[str] = None,
) -> str:
    """Scaffolds or rehards Appendix for meetings >= 15m, generating SSOT Gold Standard thematic narratives."""
    if not text:
        return text
    meta, changes_list = metadata or {}, changes if changes is not None else []
    dur_min = _resolve_healer_duration(text, meta)
    if not _is_existing_appendix_invalid(text, meta, output_1=output_1) and _is_valid_existing_appendix(text, duration_minutes=dur_min, meta=meta):
        return text
    entries, meeting_ctx = _extract_attendee_details(text, meta, output_1=output_1), (output_1 or "") if output_1 else text
    if not _has_prohibited_appendix_patterns(text):
        cand = try_append_missing_appendix_speakers(text, entries, output_1, meeting_ctx, _extract_speaker_dialogue, _synthesize_speaker_section)
        if cand and not _is_existing_appendix_invalid(cand, meta, output_1=output_1) and _is_valid_existing_appendix(cand, duration_minutes=dur_min, meta=meta):
            if cand != text:
                changes_list.append("Normalized Appendix headers and appended any missing roster speaker subsections")
            return cand
    speaker_sections, silent_attendees = _partition_appendix_entries(entries, output_1, meeting_ctx)
    if not speaker_sections and silent_attendees:
        speaker_sections.extend(_synthesize_silent_speaker_sections(silent_attendees))
        silent_attendees.clear()
    non_spk_block = (
        "\n\n# Non-Speaking Session Participants\n- The following participants were present on the call roster but had no attributed speaking turns in the transcript: "
        + ", ".join(f"{n} ({r}, {o})" for n, r, o in silent_attendees) + "."
    ) if silent_attendees else ""
    appendix_block = "\n\n# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n" + "\n\n".join(speaker_sections) + non_spk_block + "\n"
    app_match = re.search(r"(#{1,3}\s+(?:Appendix|Detailed Stakeholder Perspectives)[^\n]*)([\s\S]*?)(?=\Z)", text, re.IGNORECASE)
    if app_match:
        healed = text[: app_match.start()].rstrip() + appendix_block
        if not _has_prohibited_appendix_patterns(text) and _is_existing_appendix_invalid(healed, meta, output_1=output_1):
            return text
        changes_list.append(f"Replaced invalid appendix with SSOT Gold Standard thematic narratives for {len(speaker_sections)} speakers")
        return healed
    changes_list.append(f"Scaffolded missing Appendix with SSOT Gold Standard thematic narratives for {len(speaker_sections)} speakers")
    return text.rstrip() + appendix_block

