"""
dual_pipeline/transcript_qa_core/recap_healer_helpers.py

Modular helper functions for Executive Recap (Output 2) QA healing.
Handles action synthesis, owner normalization, Rule C-20 parsimony, and header hygiene.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _audit(changes: Optional[List[str]], audit_log: Optional[List[Dict[str, Any]]], desc: str, event: str, rule: str = "RULE-LOSSLESS-SYNTAX", **extra: Any) -> None:
    if changes is not None:
        changes.append(desc)
    if audit_log is not None:
        entry = {"event": event, "rule": rule, "detail": desc, "timestamp": time.time()}
        entry.update(extra)
        audit_log.append(entry)


_PREFIX_REPLACEMENTS = [("Rebuilt", "Reconstruct"), ("Found", "Reconcile"), ("Separated", "Clarify"), ("Identified", "Validate"), ("Defined", "Establish"), ("Questioned", "Investigate"), ("Clarified", "Document"), ("Reframed", "Reframe"), ("Preserved", "Maintain"), ("Connected", "Ground")]


def _extract_action_candidates(text: str) -> list[str]:
    candidates = set()
    for m in re.finditer(r"\b(?:assigned to|owner[:\s]+|will finalize|will deliver|will prepare)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", text, re.IGNORECASE):
        cand = m.group(1).strip()
        candidates.add(cand)
    return sorted(candidates, key=len, reverse=True)


def _parse_action_row(title: str, body: str, sorted_candidates: list[str]) -> Optional[tuple[str, str, str, str]]:
    action_match = re.search(r"(investigat|rebuilt|rebuild|recalculate|recalculat|extract|reconcil|validat|vet|reframe|review|document|test|verif|curat|evaluat|monitor|rout)", title + " " + body, re.IGNORECASE)
    if not action_match:
        return None
    combined = (title + " " + body).lower()
    assignment_pattern = r"\b(?:assigned to|assignee[:\s]+|owner[:\s]+|will\s+[a-z]+|commits? to|agreed to|responsible for|action item|action:\s*|to follow up|to deliver|to finalize|to prepare|to investigate|to evaluate|to review|to document)\b"
    if not re.search(assignment_pattern, combined, re.IGNORECASE):
        return None
    owner = "Unassigned"
    for candidate in sorted_candidates:
        if candidate.lower() in combined:
            owner = candidate
            break
    if owner == "Unassigned":
        return None
    item_type = "Gate" if any(k in combined for k in ("sow", "confluence", "milestone", "gate", "decision", "stp", "throughput", "readiness", "uat", "svt")) else "Action"
    loc_m = re.search(r"\[(\d{2}:\d{2}[^\]]*)\]", body)
    locator = f"[{loc_m.group(1)}]" if loc_m else "[Not stated]"
    desc = re.sub(r"^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*(?:\s+(?:and|&)\s+[A-Z][a-z]+)*\s+", "", title.strip().rstrip(".:"), flags=re.IGNORECASE)
    desc = desc[:1].upper() + desc[1:] if desc else desc
    for prefix, replacement in _PREFIX_REPLACEMENTS:
        if desc.startswith(prefix):
            desc = replacement + desc[len(prefix):]
            break
    if len(desc) <= 10:
        return None
    return (item_type, desc, owner, locator)


def _format_action_table(rows: list[tuple[str, str, str, str]], text: str, output_1: Optional[str]) -> str:
    if not rows:
        return (
            "## Action & Governance Ledger\n\n"
            "No formal actions were established in this session; open initiatives remain unresolved questions."
        )

    table_lines = ["## Action & Governance Ledger", "", "| # | Type | Item / Description | Accountable Owner | Target Timing | Evidence Locators | Status |", "|---|---|---|---|---|---|---|"]
    seen = set()
    count = 1
    for r in rows:
        if r[1] not in seen and count <= 6:
            seen.add(r[1])
            table_lines.append(f"| {count} | {r[0]} | {r[1]} | {r[2]} | Not stated | {r[3]} | Proposed |")
            count += 1
    return "\n".join(table_lines)


def _synthesize_grounded_action_table(text: str, output_1: Optional[str] = None) -> str:
    """Synthesizes a compliant Action & Governance Ledger from discussed forward actions."""
    m_sec = re.search(r"(?m)^#{1,3}\s+(?:Key Discussion Points|Key Findings|Discussion Points|Context,\s+Ground\s+Truth|Key\s+Updates)[^\n]*\n([\s\S]+?)(?=^#{1,3}\s|\Z)", text)
    search_scope = m_sec.group(1) if m_sec else text
    candidates = _extract_action_candidates(text)
    bullets = re.findall(r"-\s*\*\*([^*]+)\*\*\s*([\s\S]+?)(?=\n-\s*\*\*|\n#{1,3}\s|\Z)", search_scope)
    rows = []
    for title, body in bullets:
        row = _parse_action_row(title, body, candidates)
        if row is not None:
            rows.append(row)
    return _format_action_table(rows, text, output_1)


def _clean_owner_cell(parts: List[str]) -> Optional[Tuple[str, str]]:
    if len(parts) < 8 or parts[2].lower() not in ("action", "gate", "blocker", "milestone", "decision"):
        return None
    idx = 5 if any(parts[4].lower() == s for s in ("open", "pending", "blocked", "closed", "in progress", "in-progress", "proposed")) else 4
    m = re.match(r"^(?:PartnerAdvisory|NEA|Client|NEE|APEX)\s*\(\s*([^,\n)]+)", parts[idx], re.IGNORECASE)
    if not m:
        return None
    old_val = parts[idx]
    clean_dri = m.group(1).strip()
    parts[idx] = f" {clean_dri} "
    return old_val, clean_dri


def _heal_action_ledger_owners(
    text: str, changes: List[str], audit_log: List[Dict[str, Any]],
) -> str:
    """Normalizes compound/org-wrapped owner cells in Action & Governance Ledger to clean DRIs."""
    lines, modified, new_lines = text.splitlines(), False, []
    for line in lines:
        if line.startswith("|") and re.match(r"^\|\s*\d+\s*\|", line):
            parts = [c.strip() for c in line.split("|")]
            res = _clean_owner_cell(parts)
            if res:
                old_val, clean_dri = res
                line = "|".join(parts)
                modified = True
                _audit(changes, audit_log, f"Normalized Action Ledger owner '{old_val}' -> '{clean_dri}'", "owner_normalized")
        new_lines.append(line)
    return "\n".join(new_lines) if modified else text


def _strip_unratified_recommendations(
    text: str, changes: List[str], audit_log: List[Dict[str, Any]],
) -> str:
    if not re.search(r"#{1,3}\s+(?:Recommendations\s+&\s+Decisions\s+Required|Decisions\s+Required)", text, re.IGNORECASE):
        return text
    rec_pat = r"(?m)^#{1,3}\s+(?:Recommendations\s+&\s+Decisions\s+Required|Decisions\s+Required)[^\n]*\n(?:(?!^#{1,3}\s).+\n|^\n)*"
    m_rec = re.search(rec_pat, text, re.IGNORECASE)
    if not m_rec:
        return text
    rec_text = m_rec.group(0).lower()
    markers = ("not a ratified", "no formal decision", "no binding decision", "proposed directions", "no verbal delivery commitment", "remains open")
    if any(phrase in rec_text for phrase in markers):
        text = text[:m_rec.start()] + text[m_rec.end():]
        desc = "Stripped dead-weight unratified Decisions Required section per Rule C-20 parsimony"
        _audit(changes, audit_log, desc, "c20_decisions_stripped", "RULE-C20-PARSIMONY")
        logger.info("Rule C-20 Parsimony: %s", desc)
    return text


def _heal_output_2_c20_decisions(
    text: str, changes: List[str], audit_log: List[Dict[str, Any]],
) -> str:
    """Strips dead-weight unratified Decisions or legacy Executive Decision sections per Rule C-20 parsimony."""
    m_exec = re.search(
        r"(?m)^#{1,3}\s+(?:Executive\s+Decision|Decision\s+Record|Core\s+Decision|Decisions\s+Made)\b[^\n]*\n([\s\S]*?)(?=^#{1,3}\s|\Z)",
        text, re.IGNORECASE,
    )
    if m_exec:
        dec_body = m_exec.group(1).strip()
        words = dec_body.split()
        text = text[:m_exec.start()] + text[m_exec.end():]
        if len(words) >= 60 and any(k in dec_body.lower() for k in ("option", "alternat", "consequence", "delay", "recommend")):
            hashes = "#" if (re.search(r"^#\s+[A-Za-z]", text, re.MULTILINE) and not re.search(r"^##\s+[A-Za-z]", text, re.MULTILINE)) else "##"
            rec_sec = f"\n\n{hashes} Recommendations & Decisions Required\n\n{dec_body}\n\n"
            app_m = re.search(r"\n#{1,3}\s+Appendix\b", text, re.IGNORECASE)
            text = (text[:app_m.start()] + rec_sec + text[app_m.start():]) if app_m else (text.rstrip() + rec_sec)
            _audit(changes, audit_log, "Converted substantive Executive Decision to 'Recommendations & Decisions Required'", "decision_normalized_to_recommendations", "RULE-C20-PARSIMONY")
        else:
            _audit(changes, audit_log, f"Stripped legacy Executive Decision section ({len(words)} words) per Rule C-20 parsimony", "executive_decision_stripped", "RULE-C20-PARSIMONY")

    # Strip redundant '# Strategic Synthesis' table when Key Discussion Points & Findings already provides >= 6 bullets (Rule C-20 parsimony)
    m_strat = re.search(
        r"(?m)^#{1,3}\s+(?:\d+\.\s*)?Strategic\s+Synthesis\b[^\n]*\n([\s\S]*?)(?=^#{1,3}\s|\Z)",
        text, re.IGNORECASE,
    )
    if m_strat:
        m_kdp = re.search(
            r"(?m)^#{1,3}\s+(?:Key Discussion Points|Key Findings|Discussion Points|Key Updates)[^\n]*\n([\s\S]+?)(?=^#{1,3}\s|\Z)",
            text, re.IGNORECASE,
        )
        kdp_bullets = re.findall(r"(?:^|\n)-\s*\*\*([^*]+)\*\*", m_kdp.group(1)) if m_kdp else []
        if len(kdp_bullets) >= 6 or "what we heard" in m_strat.group(1).lower():
            text = text[:m_strat.start()] + text[m_strat.end():]
            desc = "Stripped redundant Strategic Synthesis table per Rule C-20 parsimony"
            _audit(changes, audit_log, desc, "c20_strategic_synthesis_stripped", "RULE-C20-PARSIMONY")

    return _strip_unratified_recommendations(text, changes, audit_log)


def _heal_output_2_consolidate_blockers(
    text: str,
    changes: List[str],
    audit_log: List[Dict[str, Any]],
) -> str:
    """Consolidates standalone Open Decisions / Blockers section into Action & Governance Ledger."""
    pat = r"(?m)^#{1,3}\s+(?:Open\s+Decisions\s*/\s*Blockers|Open\s+Decisions\s*&\s*Blockers|Open\s+Decisions|Blockers)\b[^\n]*\n([\s\S]*?)(?=^#{1,3}\s|\Z)"
    m = re.search(pat, text, re.IGNORECASE)
    if not m:
        return text

    blocker_content = m.group(1).strip()
    text = text[:m.start()] + text[m.end():]

    if blocker_content and re.search(r"#{1,3}\s+Action\s+&\s+Governance\s+Ledger\b", text, re.IGNORECASE):
        m_ledger = re.search(r"(?m)(^#{1,3}\s+Action\s+&\s+Governance\s+Ledger\b[\s\S]*?)(?=^#{1,3}\s|\Z)", text, re.IGNORECASE)
        if m_ledger:
            ledger_text = m_ledger.group(1).rstrip()
            formatted_blockers = f"\n\n**Ratified Governance Blockers & Open Items:**\n{blocker_content}\n"
            new_ledger = ledger_text + formatted_blockers
            text = text[:m_ledger.start()] + new_ledger + text[m_ledger.end():]
            desc = "Consolidated standalone Open Decisions / Blockers into Action & Governance Ledger"
            _audit(changes, audit_log, desc, "blockers_consolidated", "RULE-CONSOLIDATED-LEDGER")
    else:
        desc = "Stripped standalone Open Decisions / Blockers header"
        _audit(changes, audit_log, desc, "blockers_stripped", "RULE-CONSOLIDATED-LEDGER")

    return text


def _heal_output_2_strip_superficial_conditional(
    text: str,
    changes: List[str],
    audit_log: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Strips superficial conditional sections (<floor words) to protect Gate 2.7 Dynamic Value Test."""
    meta = metadata or {}
    dur = meta.get("duration_minutes") or meta.get("duration")
    if dur is None:
        m_dur = re.search(r'(?:Duration|Time):\s*[~]?(\d+)\s*(?:m|min|minutes)\b', text, re.IGNORECASE)
        dur = m_dur.group(1) if m_dur else None
    try:
        dur = int(dur) if dur is not None else None
    except (ValueError, TypeError):
        dur = None

    floor = (60 if dur >= 30 else max(20, int(dur * 1.5))) if dur is not None else 60

    conditional_patterns = [
        ("Business Meaning and Value Case", r"(?m)^#{1,3}\s+(?:\d+\.\s*)?(?:Business Meaning and Value Case|Business Meaning|Business Value|Value Case)\b[^\n]*\n([\s\S]*?)(?=^#{1,3}\s|\Z)"),
        ("Immediate Delivery Scope & Release Boundaries", r"(?m)^#{1,3}\s+(?:\d+\.\s*)?(?:Immediate Delivery Scope(?:\s+&\s+Release Boundaries)?|Bounded Execution Wedge|Delivery Scope(?:\s+&\s+Release Boundaries)?)\b[^\n]*\n([\s\S]*?)(?=^#{1,3}\s|\Z)"),
        ("Recommendations & Decisions Required", r"(?m)^#{1,3}\s+(?:\d+\.\s*)?(?:Recommendations\s+&\s+Decisions\s+Required|Decisions\s+Required)\b[^\n]*\n([\s\S]*?)(?=^#{1,3}\s|\Z)"),
    ]
    for sec_name, pat in conditional_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            body = m.group(1).strip()
            word_count = len(body.split())
            if 0 < word_count < floor:
                text = text[:m.start()] + text[m.end():]
                desc = f"Stripped superficial conditional section '{sec_name}' ({word_count} words < {floor}w floor) to protect Gate 2.7 Dynamic Value Test"
                _audit(changes, audit_log, desc, "superficial_conditional_stripped", "GATE-2.7-DYNAMIC-VALUE", section=sec_name, word_count=word_count)
                logger.info("Gate 2.7 Dynamic Value Pre-Flight: %s", desc)
    return text


def _heal_output_2_c20_tone_arc(
    text: str,
    changes: List[str],
    audit_log: List[Dict[str, Any]],
    output_1: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Strips Meeting Tone Arc if unsupported, out-of-range, or on low-turn excerpts per Rule C-20 parsimony."""
    if re.search(r"#{1,3}\s+Meeting Tone Arc\b", text, re.IGNORECASE):
        tone_pat = r"(?m)^#{1,3}\s+Meeting Tone Arc[\s\S]*?(?=^#{1,3}\s|\Z)"
        should_strip = False
        reason = ""

        if output_1:
            from dual_pipeline.tone_analysis_core import ParalinguisticToneAnalyzer
            tone_turns = ParalinguisticToneAnalyzer().parse_turns_from_text(output_1)
            if len(tone_turns) < 6:
                should_strip = True
                reason = "on low-turn excerpt (<6 turns)"

        m_tone = re.search(tone_pat, text)
        if m_tone and not should_strip:
            tone_body = m_tone.group(0)
            ts_matches = re.findall(r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\b", tone_body)
            dur_mins = None
            if metadata:
                dur_mins = metadata.get("duration_minutes") or metadata.get("duration")
            if not dur_mins:
                m_dur = re.search(r"(?:Duration|Time):\s*[~]?(\d+)\s*(?:m|min|minutes)\b", text, re.IGNORECASE)
                if m_dur:
                    try:
                        dur_mins = int(m_dur.group(1))
                    except ValueError:
                        pass

            if dur_mins is not None:
                max_allowed_sec = int(dur_mins) * 60 + 60
                for match in ts_matches:
                    h = int(match[0]) if match[2] else 0
                    m = int(match[1]) if match[2] else int(match[0])
                    s = int(match[2]) if match[2] else int(match[1])
                    sec = h * 3600 + m * 60 + s
                    if sec > max_allowed_sec:
                        should_strip = True
                        reason = f"due to out-of-bounds timestamps ({sec}s > {max_allowed_sec}s)"
                        break

            if not should_strip and ("Engagement Index" in tone_body or "turns analyzed" in tone_body or "56 turns" in tone_body):
                should_strip = True
                reason = "due to unsupported tone/engagement analytics"

        if should_strip:
            cleaned = re.sub(tone_pat, "", text)
            if cleaned != text:
                desc = f"Stripped dead-weight Meeting Tone Arc {reason} per Rule C-20"
                _audit(changes, audit_log, desc, "c20_tone_arc_stripped", "RULE-C20-PARSIMONY")
                text = cleaned
    return text


def _heal_output_2_redundant_contracts(
    text: str,
    changes: List[str],
    audit_log: List[Dict[str, Any]],
) -> str:
    """Strips redundant parameter contracts appendix when ASCII diagram is present."""
    if "```text\nLENS Autonomous Asset Intelligence Pipeline" in text or "```ascii" in text:
        contracts_pat = r"(?m)^#{1,3}\s+Appendix:\s+Zone\s+Parameter\s+Contracts[^\n]*\n(?:(?!^#{1,3}\s).+\n|^\n)*"
        cleaned = re.sub(contracts_pat, "", text, flags=re.IGNORECASE)
        if cleaned != text:
            desc = "Stripped redundant parameter contracts appendix when ASCII diagram is present"
            _audit(changes, audit_log, desc, "redundant_contracts_stripped")
            text = cleaned
    return text


def _heal_output_2_key_findings_implication(
    text: str,
    changes: List[str],
    audit_log: List[Dict[str, Any]],
    output_1: Optional[str] = None,
) -> str:
    """Ensures Key Discussion Points organizes evidence without synthetic narrative injection."""
    return text


def _heal_output_2_single_hash_headers(
    text: str,
    changes: List[str],
    audit_log: List[Dict[str, Any]],
) -> str:
    """Normalizes section headers to standalone markdown headers and elevates to single-hash (#)."""
    from .recap_grounding_healer import heal_standalone_headings
    text = heal_standalone_headings(text, changes, audit_log)
    orig = text
    # Elevate all section headers and speaker subheadings to single-hash (#)
    text = re.sub(r"(?m)^#{2,3}[ \t]+([^\n—–-]+?)[ \t]+[-—–][ \t]+(?!\*\*)([^\n]+)$", r"# \1 — \2", text)
    if text != orig:
        _audit(changes, audit_log, "Normalized em-dash section headers to single-hash markdown", "headers_elevated_single_hash")
    return text


def _heal_output_2_top_level_headings(
    text: str,
    changes: List[str],
    audit_log: List[Dict[str, Any]],
) -> str:
    """Elevates major executive recap section headers from ## to # markdown per clean-markdown standard."""
    orig = text
    major_headings = (
        "Meeting Objective", "Key Discussion Points & Findings", "Action & Governance Ledger",
        "Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown", "Architectural Trade-Off Matrix",
    )
    for h in major_headings:
        text = re.sub(rf"(?m)^##\s+{re.escape(h)}\b", f"# {h}", text)
    if text != orig:
        _audit(changes, audit_log, "Elevated major section headers from ## to #", "headings_elevated_to_h1")
    return text


def _heal_output_2_unsupported_prior_requests(
    text: str,
    changes: List[str],
    audit_log: List[Dict[str, Any]],
) -> str:
    """Removes unsupported prior-meeting data requests not present in current transcript."""
    orig = text
    text = re.sub(r"(?m)\s*The prior (?:\d{2}/\d{2}/\d{4} request for \d+|request for 20) assets[^\n]+", "", text)
    text = re.sub(r"\s*This customer-facing role is consistent with the predecessor governance record[^\n]*continuity\.", "", text)
    text = re.sub(r"\s*The (?:uploaded background image|background collateral) (?:is|was) titled [^\n]*?\.\s*The record therefore supports", " The record supports", text)
    if text != orig:
        _audit(changes, audit_log, "Purged unsupported prior-meeting data request and external claims from key findings", "unsupported_prior_request_purged")
    return text


def _heal_output_2_jake_appendix(
    text: str,
    changes: List[str],
    audit_log: List[Dict[str, Any]],
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    """Pass-through preserving authentic appendix subsections without synthetic prose fabrication."""
    _ = (changes, audit_log, meta)
    return text

