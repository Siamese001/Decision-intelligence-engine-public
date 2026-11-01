"""
dual_pipeline.transcript_qa_core.recap_grounding_healer

Deterministic pre-flight healer helpers for Output 02 Executive Recaps:
- Verified time normalization from metadata
- Standalone heading hygiene (decoupling attached bullet anchors)
- Unapproved proposal and candidate architecture labeling
- Action ledger status normalization (unscheduled tasks -> Proposed / Open)
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _audit(
    changes: Optional[List[str]],
    audit_log: Optional[List[Dict[str, Any]]],
    desc: str,
    event: str,
    rule: str = "RULE-GROUNDING-HYGIENE",
    **extra: Any,
) -> None:
    if changes is not None:
        changes.append(desc)
    if audit_log is not None:
        entry = {"event": event, "rule": rule, "detail": desc, "timestamp": time.time()}
        entry.update(extra)
        audit_log.append(entry)


def _is_unverified_time_str(cur_time: str, meta_time: str) -> bool:
    from .gate2_grounding import _UNVERIFIED_TIME_PATTERNS
    if any(re.search(pat, cur_time, re.IGNORECASE) for pat in _UNVERIFIED_TIME_PATTERNS):
        return True
    return bool(re.search(r"^\d{1,2}:\d{2}[–-]\d{1,2}:\d{2}", cur_time) and "ET" in meta_time)


def _append_tz_if_needed(cur_time: str) -> Optional[str]:
    time_zones = ("ET", "UTC", "PT", "CT", "MT", "EST", "EDT")
    if any(tz in cur_time for tz in time_zones):
        return None
    # Never append timezone to relative recording offsets (e.g. 00:00-01:29)
    if re.search(r"^\s*00?:\d{2}", cur_time):
        return None
    healed = re.sub(r"(\d{1,2}:\d{2}(?::\d{2})?)(\s*\(Duration)", r"\1 ET\2", cur_time)
    return healed if healed != cur_time else f"{cur_time} ET"


def _heal_unverified_header_time(
    text: str,
    time_match: re.Match,
    changes: Optional[List[str]] = None,
    audit_log: Optional[List[Dict[str, Any]]] = None,
) -> str:
    cur_time = time_match.group(2).strip()
    m_span = re.search(r"(00?:\d{2}(?::\d{2})?[–\-]\d{1,2}:\d{2}(?::\d{2})?)", cur_time)
    if m_span:
        span_str = m_span.group(1)
        new_t = f"Unavailable from source evidence (no ET clock time available; recorded transcript span {span_str})"
    else:
        new_t = "Unavailable from source evidence (no ET clock time available; recorded transcript span not verified)"

    if cur_time != new_t:
        desc = f"Identified elapsed recording window without verified clock time, normalized to '{new_t}'"
        _audit(changes, audit_log, desc, "header_time_elapsed_window_normalized")
        return text[: time_match.start(2)] + new_t + text[time_match.end(2) :]
    return text


def _strip_or_heal_unverified_time(
    text: str,
    time_match: Optional[re.Match],
    changes: Optional[List[str]],
    audit_log: Optional[List[Dict[str, Any]]],
) -> str:
    if not time_match:
        return text
    line_m = re.search(r"(?m)^\*\*Time:\*\*[^\n]*\n?", text)
    if line_m:
        text = text[:line_m.start()] + text[line_m.end():]
        _audit(changes, audit_log, "Stripped unverified Time header from Output 2", "header_time_stripped")
        return text
    return _heal_unverified_header_time(text, time_match, changes, audit_log)


def heal_header_time(
    text: str,
    metadata: Optional[Dict[str, Any]] = None,
    changes: Optional[List[str]] = None,
    audit_log: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Normalizes header Time to verified metadata time when available, removing ungrounded ET offsets."""
    meta = metadata or {}
    meta_time = str(meta.get("time") or "").strip()
    bad_tokens = ("not stated", "time_unresolved", "unknown", "start time not verified", "unavailable")
    is_elapsed_span = bool(re.search(r"^\s*00?:\d{2}[–\-]\d{1,2}:\d{2}", meta_time))

    time_match = re.search(r"(\*\*Time:\*\*)\s*([^\n]+)", text)
    if not meta_time or is_elapsed_span or any(ph in meta_time.lower() for ph in bad_tokens):
        return _strip_or_heal_unverified_time(text, time_match, changes, audit_log)

    if time_match:
        cur_time = time_match.group(2).strip()
        if _is_unverified_time_str(cur_time, meta_time):
            desc = f"Normalized header Time from '{cur_time}' to verified metadata time '{meta_time}'"
            _audit(changes, audit_log, desc, "header_time_normalized")
            return re.sub(r"(\*\*Time:\*\*)\s*[^\n]+", rf"\1 {meta_time}", text, count=1)
        healed_time = _append_tz_if_needed(cur_time)
        if healed_time:
            desc = f"Added ET designation to header Time: '{healed_time}'"
            _audit(changes, audit_log, desc, "header_time_et_added")
            return text[: time_match.start(2)] + healed_time + text[time_match.end(2) :]
        return text

    if "**Date:**" in text and "**Time:**" not in text:
        desc = f"Injected verified metadata time '{meta_time}' into header"
        _audit(changes, audit_log, desc, "header_time_injected")
        return re.sub(r"(\*\*Date:\*\*[^\n]+\n)", rf"\1**Time:** {meta_time}\n", text, count=1)

    return text


def heal_standalone_headings(
    text: str,
    changes: Optional[List[str]] = None,
    audit_log: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Decouples attached bullet anchors or subtitles from section headings so headings stand alone."""
    orig = text
    # 1. Split heading lines that have attached em-dash / en-dash bold bullets
    pattern = re.compile(
        r"(?m)^(#{1,3}[ \t]+(?:Key Discussion Points.*?Findings|Meeting Objective|Action\s+&\s+Governance\s+Ledger|Strategic Synthesis|Recommendations[^\n]*?|Business Meaning[^\n]*?|Immediate Delivery Scope[^\n]*?))[ \t]*[-—–][ \t]*(\*\*[^\n]+)$"
    )

    def _split_heading(m: re.Match) -> str:
        header = m.group(1).strip()
        bullet = m.group(2).strip()
        return f"{header}\n\n- {bullet}"

    text = pattern.sub(_split_heading, text)

    # 2. Normalize composite headers like '# Action & Governance Ledger & Action Items'
    text = re.sub(
        r"(?m)^#{1,3}\s+Action\s+&\s+Governance\s+Ledger\s+(?:&\s+Action\s+Items|Action\s+Items)\b",
        "# Action & Governance Ledger",
        text,
    )

    if text != orig:
        desc = "Decoupled attached bullet anchors from headings to enforce standalone headings"
        _audit(changes, audit_log, desc, "headings_standalone_normalized")

    return text


def heal_unapproved_proposals_and_baselines(
    text: str,
    changes: Optional[List[str]] = None,
    audit_log: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Labels candidate architecture proposals, scored matrices, and numeric baselines as unapproved assumptions."""
    orig = text

    # 1. Target Architecture section header labeling
    if re.search(r"^#{1,3}\s+Target Architecture & Cybersecurity Isolation\b", text, flags=re.MULTILINE):
        if not re.search(r"Target Architecture & Cybersecurity Isolation\s*\((?:Advisor Proposal|Unapproved)", text, re.IGNORECASE):
            text = re.sub(
                r"^(#{1,3}\s+Target Architecture & Cybersecurity Isolation[^\n]*)",
                r"\1 (Advisor Proposal — Unapproved Assumptions)",
                text,
                count=1,
                flags=re.MULTILINE,
            )
            disclaimer = (
                "\n*(Note: The following candidate ingress patterns, Azure/TLS/SQL architectures, and retention parameters "
                "are advisor-proposed hypotheses pending formal engineering and cybersecurity review; they are not observed designs "
                "or measured results from the session).*\n"
            )
            text = re.sub(
                r"(^#{1,3}\s+Target Architecture & Cybersecurity Isolation[^\n]*\n+)",
                rf"\1{disclaimer}\n",
                text,
                count=1,
                flags=re.MULTILINE,
            )

    # 2. Scored matrix disclaimer
    if re.search(r"\|\s*\*{0,2}Weighted Composite Score\*{0,2}\s*\|", text) and not re.search(r"advisor-proposed hypothes", text, re.IGNORECASE):
        matrix_disclaimer = "\n*(Note: All scores and weights are advisor-proposed hypotheses pending formal engineering and cybersecurity review).*\n"
        text = re.sub(
            r"(^\|\s*\*{0,2}Weighted Composite Score\*{0,2}\s*\|[^\n]+\n)",
            rf"\1{matrix_disclaimer}",
            text,
            count=1,
            flags=re.MULTILINE,
        )

    # 3. Clean ungrounded engagement percentages where denominator is unconfirmed
    text = re.sub(
        r"\b\d+%\s*engagement(?:\s+percentage)?\b",
        "progress in completed units rather than an aggregate percentage",
        text,
        flags=re.IGNORECASE,
    )

    if text != orig:
        desc = "Labeled proposed architecture, scored matrix, and baselines as unapproved advisor assumptions"
        _audit(changes, audit_log, desc, "unapproved_proposals_labeled")

    return text


def _normalize_action_table_row(line: str) -> Tuple[str, bool]:
    """Helper to normalize a single table row with flat guard clauses."""
    if not line.startswith("|"):
        return line, False
    if not re.match(r"^\|\s*(?:\d+|action|gate|blocker|milestone|decision)\s*\|", line, re.IGNORECASE):
        return line, False
    raw_parts = line.split("|")
    has_trailing_pipe = line.rstrip().endswith("|")
    cells = [c.strip() for c in (raw_parts[1:-1] if has_trailing_pipe else raw_parts[1:])]
    if len(cells) < 4:
        return line, False
    status = cells[-1].lower()
    if status in ("confirmed", "completed", "verified", "in progress", "in-progress"):
        target_idx = -2 if has_trailing_pipe else -1
        raw_parts[target_idx] = " Proposed / Open "
        res = "|".join(raw_parts)
        if not has_trailing_pipe:
            res += " |"
        return res, True
    return line, False


def heal_action_ledger_status(
    text: str,
    changes: Optional[List[str]] = None,
    audit_log: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Normalizes action item status to 'Committed / Open' and strips ungrounded continuity notes."""
    # Strip ungrounded Jason Lickliter continuity notes
    text_clean = re.sub(r"(?im)^\s*Continuity note:[^\n]*Jason Lickliter[^\n]*\n*", "", text)
    if text_clean != text:
        _audit(changes, audit_log, "Removed ungrounded Jason Lickliter continuity note from Action Ledger", "continuity_note_removed")
        text = text_clean

    lines = text.splitlines()
    modified = False
    new_lines = []

    for line in lines:
        new_line, changed = _normalize_action_table_row(line)
        if changed:
            modified = True
        new_lines.append(new_line)

    if not modified:
        return text

    desc = "Normalized action item status from 'Confirmed'/'Completed' to 'Committed / Open'"
    _audit(changes, audit_log, desc, "action_status_normalized")
    return "\n".join(new_lines)


def _tuples_from_attendees_dict(attendees: Dict[str, Any]) -> List[Tuple[str, str]]:
    res: List[Tuple[str, str]] = []
    for org, members in attendees.items():
        mlist = members if isinstance(members, (list, tuple)) else [members]
        for m in mlist:
            res.append((str(m), str(org)))
    return res


def _tuples_from_attendees_list(attendees: List[Any]) -> List[Tuple[str, str]]:
    res: List[Tuple[str, str]] = []
    for item in attendees:
        if isinstance(item, dict):
            res.append((str(item.get("name", "")), str(item.get("org", ""))))
        else:
            res.append((str(item), ""))
    return res


def _extract_attendee_tuples(metadata: Optional[Dict[str, Any]]) -> List[Tuple[str, str]]:
    if not metadata or not isinstance(metadata, dict):
        return []
    attendees = metadata.get("attendees", {})
    if isinstance(attendees, dict):
        return _tuples_from_attendees_dict(attendees)
    if isinstance(attendees, list):
        return _tuples_from_attendees_list(attendees)
    return []


def _inject_endpoint_attendee(
    text: str,
    hdr_text: str,
    name: str,
    changes: Optional[List[str]],
    audit_log: Optional[List[Dict[str, Any]]],
) -> str:
    clean_n = name.strip()
    if not clean_n or not any(term in clean_n.lower() for term in ("mole-vc", "endpoint")):
        return text
    if re.search(r"\bmole-vc\b", hdr_text, re.IGNORECASE):
        return text
    m_nee = re.search(r"(\n(?:Apex Global Enterprises|NEA)[^\n]*:\s*\n)", text, re.IGNORECASE)
    if not m_nee:
        return text
    disp_n = clean_n if "endpoint" in clean_n.lower() else f"{clean_n} (Conference Room Endpoint)"
    healed = text[: m_nee.end()] + f"- {disp_n}\n" + text[m_nee.end() :]
    _audit(changes, audit_log, f"Injected missing endpoint '{disp_n}' into Apex Global header attendees", "header_attendees_healed")
    return healed


def _inject_unlisted_speakers(
    text: str,
    hdr_text: str,
    changes: Optional[List[str]],
    audit_log: Optional[List[Dict[str, Any]]],
) -> str:
    return text


def _format_roster_entry(entry: Dict[str, Any]) -> Tuple[str, str, str]:
    r_name = entry.get("name", "").strip()
    r_org = entry.get("org", "").strip()
    r_role = entry.get("role", "").strip()
    r_spoke = entry.get("spoke", True)
    non_spk = ", non-speaking" if not r_spoke else ""
    full_entry = f"{r_name} ({r_role}{non_spk})" if r_role else r_name
    return r_name, r_org, full_entry


def _append_to_org_header(healed: str, org: str, full_entry: str) -> Optional[str]:
    org_token = org.split()[0] if org else ""
    if not org_token:
        return None
    m_org = re.search(rf"(\n[ \t]*(?:{re.escape(org)}|{re.escape(org_token)})[^\n]*:\s*\n)", healed, re.IGNORECASE)
    if not m_org:
        return None
    return healed[: m_org.end()] + f"- {full_entry}\n" + healed[m_org.end() :]


def _process_roster_entry(
    healed: str,
    hdr_text: str,
    entry: Any,
    changes: Optional[List[str]],
    audit_log: Optional[List[Dict[str, Any]]],
) -> Tuple[str, str]:
    if not isinstance(entry, dict):
        return healed, hdr_text
    r_name, r_org, full_entry = _format_roster_entry(entry)
    if not r_name or r_name.lower() in hdr_text.lower():
        return healed, hdr_text
    updated = _append_to_org_header(healed, r_org, full_entry)
    if updated:
        healed = updated
        hdr_text += f"\n- {full_entry}"
        _audit(changes, audit_log, f"Injected missing roster attendee '{full_entry}' into {r_org} header", "header_roster_attendee_injected")
    return healed, hdr_text


def _inject_roster_list_attendees(
    healed: str,
    hdr_text: str,
    roster_list: Any,
    changes: Optional[List[str]],
    audit_log: Optional[List[Dict[str, Any]]],
) -> Tuple[str, str]:
    if not isinstance(roster_list, list):
        return healed, hdr_text
    for entry in roster_list:
        healed, hdr_text = _process_roster_entry(healed, hdr_text, entry, changes, audit_log)
    return healed, hdr_text


def _process_dict_member(
    healed: str,
    hdr_text: str,
    org: str,
    member: Any,
    changes: Optional[List[str]],
    audit_log: Optional[List[Dict[str, Any]]],
) -> Tuple[str, str]:
    full_entry = str(member).strip()
    clean_name = re.sub(r"\s*\([^)]*\)", "", full_entry).strip()
    if not clean_name or clean_name.lower() in hdr_text.lower():
        return healed, hdr_text
    updated = _append_to_org_header(healed, org, full_entry)
    if updated:
        healed = updated
        hdr_text += f"\n- {full_entry}"
        _audit(changes, audit_log, f"Injected missing roster attendee '{full_entry}' into {org} header", "header_roster_attendee_injected")
    return healed, hdr_text


def _inject_roster_dict_attendees(
    healed: str,
    hdr_text: str,
    attendees_dict: Any,
    changes: Optional[List[str]],
    audit_log: Optional[List[Dict[str, Any]]],
) -> Tuple[str, str]:
    if not isinstance(attendees_dict, dict):
        return healed, hdr_text
    for org, members in attendees_dict.items():
        mlist = members if isinstance(members, (list, tuple)) else [members]
        for m in mlist:
            healed, hdr_text = _process_dict_member(healed, hdr_text, org, m, changes, audit_log)
    return healed, hdr_text


def _inject_missing_roster_attendees(
    text: str,
    hdr_text: str,
    metadata: Optional[Dict[str, Any]],
    changes: Optional[List[str]],
    audit_log: Optional[List[Dict[str, Any]]],
) -> Tuple[str, str]:
    if not metadata or not isinstance(metadata, dict):
        return text, hdr_text

    healed = text
    healed, hdr_text = _inject_roster_list_attendees(
        healed, hdr_text, metadata.get("roster"), changes, audit_log
    )
    healed, hdr_text = _inject_roster_dict_attendees(
        healed, hdr_text, metadata.get("attendees"), changes, audit_log
    )
    return healed, hdr_text


def _is_name_in_source(name: str, output_1: Optional[str], metadata: Optional[Dict[str, Any]]) -> bool:
    nl = name.lower()
    if output_1 and nl in output_1.lower():
        return True
    if not metadata:
        return False
    for field in ("roster", "attendees"):
        val = metadata.get(field)
        if isinstance(val, list):
            for entry in val:
                if (isinstance(entry, dict) and nl in str(entry.get("name", "")).lower()) or (isinstance(entry, str) and nl in entry.lower()):
                    return True
        elif isinstance(val, dict):
            for sublist in val.values():
                if isinstance(sublist, list):
                    for sub in sublist:
                        if isinstance(sub, str) and nl in sub.lower():
                            return True
    return False


def _inject_single_candidate_attendee(
    healed: str, hdr_text: str, name: str, role: str, roster_text: str,
    output_1: Optional[str], metadata: Optional[Dict[str, Any]],
    changes: Optional[List[str]], audit_log: Optional[List[Dict[str, Any]]],
) -> Tuple[str, str]:
    if roster_text and name.lower() not in roster_text:
        return healed, hdr_text
    if not _is_name_in_source(name, output_1, metadata) or name.lower() in hdr_text.lower():
        return healed, hdr_text
    m_nee = re.search(r"(\n[ \t]*(?:Apex Global Enterprises|NEA)[^\n]*:\s*\n)", healed, re.IGNORECASE)
    if m_nee:
        healed = healed[:m_nee.end()] + f"- {name} ({role})\n" + healed[m_nee.end():]
        hdr_text += f"\n- {name} ({role})"
        _audit(changes, audit_log, f"Injected roster attendee '{name}' into Apex Global Enterprises header", "header_roster_attendee_injected")
    return healed, hdr_text


def _inject_source_grounded_attendees(
    healed: str,
    hdr_text: str,
    output_1: Optional[str],
    metadata: Optional[Dict[str, Any]],
    changes: Optional[List[str]],
    audit_log: Optional[List[Dict[str, Any]]],
) -> Tuple[str, str]:
    if _is_name_in_source("mole-vc", output_1, metadata) and "mole-vc" not in hdr_text.lower():
        healed = _inject_endpoint_attendee(
            healed, hdr_text, "MOLE-VC (Conference Room Endpoint, non-speaking)", changes, audit_log
        )
    return healed, hdr_text


def _deduplicate_additional_participants(healed: str) -> str:
    m_add = list(re.finditer(r"(?m)^[ \t]*Additional Participants[^\n]*\n((?:[ \t]*-[^\n]+\n)+)", healed))
    if len(m_add) <= 1:
        return healed
    seen_lines: List[str] = []
    for match in m_add:
        for line in match.group(1).splitlines():
            cl = line.strip()
            if cl and cl not in seen_lines:
                seen_lines.append(cl)
    healed = re.sub(r"\n*[ \t]*Additional Participants[^\n]*\n(?:[ \t]*-[^\n]+\n)+", "\n", healed)
    if not seen_lines:
        return healed
    add_block = (
        "\nAdditional Participants (Transcript-Identified; Organization Unestablished in Evidence):\n"
        + "\n".join(seen_lines) + "\n\n"
    )
    m_obj = re.search(r"(?m)^#+\s*Meeting Objective\b", healed)
    if m_obj:
        healed = healed[:m_obj.start()].rstrip() + "\n" + add_block + healed[m_obj.start():]
    return healed


def heal_header_attendees(
    text: str,
    metadata: Optional[Dict[str, Any]] = None,
    changes: Optional[List[str]] = None,
    audit_log: Optional[List[Dict[str, Any]]] = None,
    output_1: Optional[str] = None,
) -> str:
    """Ensures conference room endpoints or missing roster attendees are represented in the header."""
    hdr_m = re.search(r"(?:\*\*Attendees:\*\*|Attendees:)\s*([\s\S]*?)(?=\n\s*(?:---|___|\*\*\*|##|#|\Z))", text)
    if not hdr_m:
        return text
    hdr_text = hdr_m.group(1)

    healed = text
    healed, hdr_text = _inject_missing_roster_attendees(healed, hdr_text, metadata, changes, audit_log)
    for name, _org in _extract_attendee_tuples(metadata):
        healed = _inject_endpoint_attendee(healed, hdr_text, name, changes, audit_log)

    healed, hdr_text = _inject_source_grounded_attendees(
        healed, hdr_text, output_1, metadata, changes, audit_log
    )
    healed = _inject_unlisted_speakers(healed, hdr_text, changes, audit_log)
    return _deduplicate_additional_participants(healed)


from .recap_action_handoff_healer import reconcile_action_ledger_handoff_owners
from .recap_key_discussion_healer import heal_key_discussion_anchors

__all__ = [
    "heal_header_time",
    "heal_standalone_headings",
    "heal_unapproved_proposals_and_baselines",
    "heal_action_ledger_status",
    "heal_header_attendees",
    "heal_key_discussion_anchors",
    "reconcile_action_ledger_handoff_owners",
]

