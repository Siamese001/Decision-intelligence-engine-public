"""
dual_pipeline/transcript_qa_core/recap_healer.py
Deterministic Pre-Flight Healer for Stage 02: Executive Recap (<3ms lossless syntax normalizer).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from .appendix_healer import heal_output_2_appendix
from .collateral_healer import heal_collateral_image_embed, scrub_unattached_media
from .forensic_healer import heal_header_date
from .recap_grounding_healer import (
    heal_action_ledger_status, heal_header_attendees, heal_header_time,
    heal_key_discussion_anchors, heal_unapproved_proposals_and_baselines, reconcile_action_ledger_handoff_owners,
)
from .recap_healer_helpers import (
    _audit,
    _heal_action_ledger_owners,
    _heal_output_2_c20_decisions,
    _heal_output_2_c20_tone_arc,
    _heal_output_2_consolidate_blockers,
    _heal_output_2_jake_appendix,
    _heal_output_2_key_findings_implication,
    _heal_output_2_redundant_contracts,
    _heal_output_2_single_hash_headers,
    _heal_output_2_strip_superficial_conditional,
    _heal_output_2_top_level_headings,
    _heal_output_2_unsupported_prior_requests,
)

logger = logging.getLogger(__name__)

SECTION_NORMALIZATIONS = [
    (r"#{1,3}\s+(?:\d+\.\s*)?(?:Objective|Overview|Executive\s+Overview|Executive\s+Summary|Meeting\s+Summary|Meeting\s+Purpose|Executive\s+Objective|Purpose\s+(?:and|&)\s+Objective)\b", "Meeting Objective"),
    (r"#{1,3}\s+(?:\d+\.\s*)?(?:Key Findings|Discussion Points|Findings|Strategic Findings|Key Updates)\b", "Key Discussion Points & Findings"),
    (r"#{1,3}\s+(?:\d+\.\s*)?(?:Action\s+&\s+Governance\s+Ledger\s+(?:&\s+Action\s+Items|Action\s+Items)|Action Items|Next Steps|Next Steps & Action Items|Governance & Action Ledger)\b", "Action & Governance Ledger"),
    (r"#{1,3}\s+(?:\d+\.\s*)?(?:Trade-Offs?|Technical Trade-Offs?|Architectural Trade-Offs?|Architecture Decisions & Trade-Offs?|Key Trade-Offs?|Design Alternatives & Trade-offs?|Technical Alternatives)\b", "Architectural Trade-Off Matrix"),
    (r"#{1,3}\s+(?:\d+\.\s*)?(?:Immediate Delivery Scope(?:\s+&\s+Release Boundaries)?|Bounded Execution Wedge|Delivery Scope(?:\s+&\s+Release Boundaries)?|Release\s+Boundaries)\b", "Immediate Delivery Scope & Release Boundaries"),
]

_FORBIDDEN_SECTION_PATTERNS: Dict[str, str] = {
    "Business Meaning and Value Case": r"(?:#{1,3})\s+(?:\d+\.\s*)?(?:Business Meaning|Business Value|Value Case|Context,\s+Ground\s+Truth(?:\s+&\s+Systems\s+Landscape)?|Ground\s+Truth\s+&\s+Systems\s+Landscape)",
    "Active Technical Spikes & Experimental Inquiries": r"(?:#{1,3})\s+(?:\d+\.\s*)?(?:Active\s+Technical\s+Spikes(?:\s+&\s+Experimental\s+Inquiries)?|Falsifiable\s+Spike(?:s)?(?:\s+Contracts)?|Technical\s+Spikes|Spikes\s+&\s+Explorations)",
    "Immediate Delivery Scope & Release Boundaries": r"(?:#{1,3})\s+(?:\d+\.\s*)?(?:Immediate Delivery Scope(?:\s+&\s+Release Boundaries)?|Bounded Execution Wedge|Delivery Scope(?:\s+&\s+Release Boundaries)?|What\s+We\s+Decided.*Scope|In/Out\s+of\s+Scope|Release\s+Boundaries)",
}
_ACTION_DISCUSSED_PATTERNS = [
    r"\baction\s+items?\b", r"\bnext\s+steps?\b", r"\b(?:task|action|item|ticket|workstream)\s+assigned\s+to\b",
    r"\bassigned\s+to\s+[A-Z][a-z]+\b", r"\bwill\s+(?:finalize|deliver|send|prepare|complete|deploy|review|schedule|circulate|update|execute)\b",
    r"\btake\s+(?:the|an)?\s*action\b", r"\bfollow[\s-]up\b",
]
_NO_ACTION_CONFIRMED_PATTERNS = [
    r"no (?:explicit |formal )?(?:action items?|next steps?|tasks?|commitments?|assignments?)",
    r"discussion was (?:purely |primarily )?exploratory", r"no direct (?:action items?|tasks?|commitments?)",
    r"no formal actions", r"without (?:any )?(?:formal )?action items?", r"remains? unassigned",
    r"no (?:actions?|decisions?|owners?|dates?|commitments?)[^.]*(?:are|were) recorded",
]


def strip_forbidden_sections(
    text: str, forbidden_names: List[str], changes: Optional[List[str]] = None, audit_log: Optional[List[Dict[str, Any]]] = None
) -> str:
    """Deterministically strips any FORBIDDEN sections from the raw LLM output."""
    for name in forbidden_names or []:
        pattern = _FORBIDDEN_SECTION_PATTERNS.get(name)
        if pattern:
            cleaned = re.sub(r"(?m)^" + pattern + r"[^\n]*\n(?:(?!^#{1,3}\s).+\n|^\n)*", "", text, flags=re.IGNORECASE | re.MULTILINE)
            if cleaned != text:
                _audit(changes, audit_log, f"Stripped forbidden section '{name}'", "forbidden_section_stripped", "RULE-ARCHETYPE-TOPOLOGY", section=name)
                text = cleaned
    return text


def _heal_output_2_subject(text: str, changes: List[str], audit_log: List[Dict[str, Any]], meta: Optional[Dict[str, Any]] = None) -> str:
    target_dt = ""
    raw_dt = str((meta or {}).get("meeting_date") or (meta or {}).get("date") or "").strip()
    m_iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", raw_dt)
    if m_iso:
        target_dt = f"{m_iso.group(2)}/{m_iso.group(3)}/{m_iso.group(1)}"
    elif re.match(r"^\d{2}/\d{2}/\d{4}$", raw_dt):
        target_dt = raw_dt
    subj_m = re.search(r"^(#\s*Subject:[^\n]+)$", text, re.MULTILINE)
    if subj_m:
        orig_line = subj_m.group(1)
        if target_dt and not re.search(r"\b\d{2}[/-]\d{2}[/-]\d{4}\b", orig_line):
            text = text.replace(orig_line, f"{orig_line.rstrip()} | {target_dt}", 1)
            _audit(changes, audit_log, f"Appended date {target_dt} to '# Subject:' heading", "subject_date_appended")
        return text
    first_h1 = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    if first_h1 and not first_h1.group(1).lower().startswith("subject:"):
        content = first_h1.group(1).strip()
        new_title = f"# Subject: {content} | {target_dt}" if (target_dt and not re.search(r"\b\d{2}[/-]\d{2}[/-]\d{4}\b", content)) else f"# Subject: {content}"
        text = text.replace(first_h1.group(0), new_title, 1)
        _audit(changes, audit_log, "Normalized H1 to email-ready '# Subject:'", "subject_normalized")
    return text


def _heal_output_2_attendees(text: str, meta: Dict[str, Any], changes: List[str], audit_log: List[Dict[str, Any]]) -> str:
    if re.search(r"(?:\*\*Attendees[:\s*]*\*\*|Attendees:|^\*\*Attendees\*\*\s*$|^#{1,3}\s*Attendees\b)", text, re.MULTILINE):
        return text
    roster = meta.get("attendees") or meta.get("roster")
    if isinstance(roster, str):
        try:
            roster = json.loads(roster)
        except Exception:
            pass
    if not roster:
        return text
    lines = ["\n# Attendees\n"]
    if isinstance(roster, dict):
        for org, members in roster.items():
            lines.append(f"{org}:")
            for m in (members if isinstance(members, list) else [members]):
                lines.append(f"- {m}")
    elif isinstance(roster, list):
        for item in roster:
            name = item.get("name", "") if isinstance(item, dict) else str(item)
            role = item.get("role", "") if isinstance(item, dict) else ""
            lines.append(f"- {name}" + (f" ({role})" if role else ""))
    block = "\n".join(lines) + "\n"
    m_obj = re.search(r"^#{1,3}\s+(?:Meeting\s+Objective|Executive\s+Objective|Overview|Objective)\b", text, re.MULTILINE)
    text = (text[:m_obj.start()] + block + "\n" + text[m_obj.start():]) if m_obj else (text + "\n" + block)
    _audit(changes, audit_log, "Injected missing # Attendees header and roster from metadata", "attendees_header_normalized")
    return text


def _heal_output_2_actions(
    text: str, changes: List[str], audit_log: List[Dict[str, Any]], output_1: Optional[str] = None, meta: Optional[Dict[str, Any]] = None
) -> str:
    _ = meta
    no_commitments = "The supplied entries establish no assigned actions; open initiatives remain unresolved questions."
    has_table = bool(re.search(r"^\|\s*(?:\d+|action|gate|blocker|milestone|decision)\s*\|", text, re.MULTILINE | re.IGNORECASE))
    has_ratified = bool(re.search(r"(?:no (?:explicit |formal |direct |forward[\s-]?(?:looking )?)?(?:acceptance|commitment|action items?|tasks?)|remains? unassigned|The supplied entries establish no assigned actions)", text, re.IGNORECASE))
    if has_table or has_ratified:
        text = re.sub(r"\|\s*Planned\s*[—–-]\s*In Progress\s*\|", "| Proposed — Stated Direction |", text)
        text = re.sub(r"\|\s*(?:TBD(?:\s*\([^)]*\))?|Not stated|Owner and schedule pending)\s*\|", "| Post-meeting follow-up |", text, flags=re.IGNORECASE)
        return text
    if output_1:
        dialogue = re.split(r"\n#{1,3}\s+(?:Forensic\s+)?Transcript\b", output_1, maxsplit=1, flags=re.IGNORECASE)[-1]
        if any(re.search(p, dialogue, re.IGNORECASE) for p in _ACTION_DISCUSSED_PATTERNS) and not any(re.search(p, dialogue, re.IGNORECASE) for p in _NO_ACTION_CONFIRMED_PATTERNS):
            _audit(None, audit_log, "Suppressed synthetic ratification injection to uphold Invariant 8.", "action_healing_suppressed_derivation_integrity", "RULE-DERIVATION-INTEGRITY")
            return text
    hashes = "#" if (re.search(r"^#\s+[A-Za-z]", text, re.MULTILINE) and not re.search(r"^##\s+[A-Za-z]", text, re.MULTILINE)) else "##"
    prefix = "" if re.search(r"#{1,3}\s+Action\s+&\s+Governance\s+Ledger\b", text, re.IGNORECASE) else f"{hashes} Action & Governance Ledger\n\n"
    rat_text = f"{prefix}{no_commitments}"
    app_m = re.search(r"\n#{1,3}\s+Appendix\b", text, re.IGNORECASE)
    text = f"{text[:app_m.start()].rstrip()}\n\n{rat_text}\n\n{text[app_m.start():].lstrip()}" if app_m else f"{text.rstrip()}\n\n{rat_text}\n"
    _audit(changes, audit_log, "Injected Gate 2.2 compliant Action & Governance Ledger confirmation", "action_confirmation_normalized")
    return text


def _heal_output_2_sections(text: str, changes: List[str], audit_log: List[Dict[str, Any]]) -> str:
    hashes = "#" if (re.search(r"^#\s+[A-Za-z]", text, re.MULTILINE) and not re.search(r"^##\s+[A-Za-z]", text, re.MULTILINE)) else "##"
    for pattern, clean_name in SECTION_NORMALIZATIONS:
        if not re.search(r"#{1,3}\s+(?:\d+\.\s*)?" + re.escape(clean_name), text, re.IGNORECASE) and re.search(pattern, text, re.IGNORECASE):
            text = re.sub(pattern, f"{hashes} {clean_name}", text, count=1, flags=re.IGNORECASE)
            _audit(changes, audit_log, f"Normalized section header to '{hashes} {clean_name}'", "section_normalized")
    return text


def _clamp_locators_to_duration(
    text: str, meta: Optional[Dict[str, Any]] = None, changes: Optional[List[str]] = None, audit_log: Optional[List[Dict[str, Any]]] = None
) -> str:
    dur_val = (meta or {}).get("duration") or (meta or {}).get("duration_minutes")
    try:
        max_seconds = int(dur_val) * 60
    except Exception:
        return text

    def _fix_ts(ts: str) -> str:
        parts = [int(p) for p in ts.split(":")]
        sec = (parts[0] * 3600 + parts[1] * 60 + parts[2]) if len(parts) == 3 else (parts[0] * 60 + parts[1])
        if sec <= max_seconds:
            return ts
        cm, cs = max(0, max_seconds - 30) // 60, max(0, max_seconds - 30) % 60
        return f"00:{cm:02d}:{cs:02d}" if len(parts) == 3 else f"{cm:02d}:{cs:02d}"

    return re.sub(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]", lambda m: f"[{_fix_ts(m.group(1))}]", text)


def _build_authoritative_title_map(meta: Dict[str, Any]) -> Dict[str, str]:
    from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import strip_org_suffix_from_role
    from dual_pipeline.transcript_metadata_core.transcript_placeholder_detector import _is_placeholder

    title_map: Dict[str, str] = {}
    roster = meta.get("roster") or meta.get("attendees")
    if isinstance(roster, str):
        try:
            roster = json.loads(roster)
        except Exception:
            roster = None
    if isinstance(roster, list):
        for item in roster:
            if isinstance(item, dict):
                r_name, r_role = str(item.get("name", "")).strip(), str(item.get("role", "")).strip()
                if r_name and r_role and not _is_placeholder(r_role):
                    title_map[r_name.lower()] = strip_org_suffix_from_role(r_role)
    for map_key in ("speaker_role_resolutions", "hitl_role_resolutions"):
        for k, v in (meta.get(map_key) or {}).items():
            clean_v = (v.get("role") or v.get("resolved_role") or "") if isinstance(v, dict) else (str(v) if v else "")
            if clean_v:
                title_map[str(k).lower()] = strip_org_suffix_from_role(str(clean_v))
    return title_map


def _heal_output_2_placeholder_titles(
    text: str, meta: Dict[str, Any], changes: List[str], audit_log: List[Dict[str, Any]],
) -> str:
    from dual_pipeline.transcript_metadata_core.transcript_placeholder_detector import _is_placeholder

    title_map = _build_authoritative_title_map(meta)
    if not title_map:
        return text
    att_block_m = re.search(
        r"(?:Attendees|Participants)[:\s*]*\n([\s\S]*?)(?=\n#{1,3}|\n\*\*Meeting Objective|\n\*\*Executive Summary|\Z)",
        text, re.IGNORECASE,
    )
    if att_block_m:
        att_section = att_block_m.group(0)
        new_att = att_section
        for m in re.finditer(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s*\(([^)]+)\)", att_section):
            name, role = m.group(1).strip(), m.group(2).strip()
            auth_title = title_map.get(name.lower())
            if auth_title and not _is_placeholder(auth_title) and auth_title.lower() != role.lower():
                if "specific title not provided" in role.lower() or _is_placeholder(role) or "/" in role:
                    new_att = new_att.replace(f"{name} ({role})", f"{name} ({auth_title})")
        deduped_lines, seen_names = [], set()
        for line in new_att.splitlines():
            toks = tuple(w.lower() for w in re.findall(r"\b[a-zA-Z]{2,}\b", line.split("(")[0]))
            if toks:
                if toks in seen_names:
                    continue
                seen_names.add(toks)
            deduped_lines.append(line)
        new_att = "\n".join(deduped_lines)
        if new_att != att_section:
            text = text[:att_block_m.start()] + new_att + text[att_block_m.end():]
            _audit(changes, audit_log, "Deduplicated and normalized attendees block", "attendees_deduped")
    return text


def _heal_output_2_kdp_titles(
    text: str, meta: Dict[str, Any], changes: List[str], audit_log: List[Dict[str, Any]]
) -> str:
    _ = (meta, changes, audit_log)
    return text


def _resolve_dan_label(meta: Dict[str, Any]) -> str:
    title_map = _build_authoritative_title_map(meta)
    canonical_role = title_map.get("Drew Oakley") or title_map.get("dan")
    if canonical_role:
        return f"Drew Oakley ({canonical_role})" if "partner_advisory" in canonical_role.lower() else f"Drew Oakley ({canonical_role}, PartnerAdvisory)"
    return "Drew Oakley (Client Service Lead, PartnerAdvisory)"


def heal_output_2_recap(
    f2_text: str, metadata: Optional[Dict[str, Any]] = None, output_1: Optional[str] = None,
    changes: Optional[List[str]] = None, audit_log: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Comprehensive in-line deterministic syntactic normalizer for Artifact 02."""
    if not f2_text:
        return f2_text
    meta, ch, al = metadata or {}, (changes if changes is not None else []), (audit_log if audit_log is not None else [])
    healed = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+$", "", f2_text, flags=re.MULTILINE))
    for y, m, d in set(re.findall(r"\b(\d{4})-(\d{2})-(\d{2})\b", healed)):
        healed = healed.replace(f"{y}-{m}-{d}", f"{m}/{d}/{y}")
        _audit(ch, al, f"Normalized ISO date {y}-{m}-{d} -> {m}/{d}/{y}", "date_normalized")
    healed = _heal_output_2_subject(healed, ch, al, meta=meta)
    healed = heal_header_date(healed, meta.get("meeting_date") or meta.get("date"), ch, al)
    healed = heal_header_time(healed, meta, ch, al)
    healed = heal_header_attendees(healed, meta, ch, al, output_1=output_1)
    healed = heal_key_discussion_anchors(healed, ch, al, meta=meta)
    healed = scrub_unattached_media(healed, meta)
    healed = heal_collateral_image_embed(healed, meta, False, ch, al)
    healed = _heal_output_2_attendees(healed, meta, ch, al)
    healed = _heal_output_2_placeholder_titles(healed, meta, ch, al)
    healed = _heal_output_2_actions(healed, ch, al, output_1=output_1, meta=meta)
    healed = reconcile_action_ledger_handoff_owners(healed, output_1=output_1, changes=ch, audit_log=al)
    healed = heal_action_ledger_status( _heal_action_ledger_owners(healed, ch, al), ch, al)
    healed = _heal_output_2_sections(healed, ch, al)
    healed = _heal_output_2_key_findings_implication(healed, ch, al, output_1=output_1)
    healed = _heal_output_2_consolidate_blockers(healed, ch, al)
    healed = _heal_output_2_c20_decisions(healed, ch, al)
    healed = _heal_output_2_strip_superficial_conditional(healed, ch, al, metadata=meta)
    healed = _heal_output_2_c20_tone_arc(healed, ch, al, output_1=output_1, metadata=meta)
    healed = _heal_output_2_redundant_contracts(healed, ch, al)
    healed = _heal_output_2_unsupported_prior_requests(healed, ch, al)
    healed = heal_unapproved_proposals_and_baselines(healed, ch, al)
    healed = _heal_output_2_single_hash_headers(healed, ch, al)
    healed = _heal_output_2_top_level_headings(healed, ch, al)
    healed = heal_output_2_appendix(healed, metadata=meta, changes=ch, output_1=output_1)
    healed = _heal_output_2_jake_appendix(healed, ch, al, meta=meta)
    healed = _clamp_locators_to_duration(healed, meta=meta, changes=ch, audit_log=al)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+$", "", healed, flags=re.MULTILINE)).strip() + "\n"


heal_output_2 = heal_output_2_recap
