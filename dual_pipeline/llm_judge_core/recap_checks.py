"""
dual_pipeline/llm_judge_core/recap_checks.py

Preflight integrity, structural density, and anti-redundancy checks
for ExecutiveRecapJudge (Output 02).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from dual_pipeline.parameters import get_output2_passive_hedging_threshold
from dual_pipeline.prompts import (
    OUTPUT2_CONTRACT,
    PASSIVE_HEDGING_PATTERNS,
    ROLE_ANONYMIZATION_PATTERNS,
    WORD_SALAD_REGEX as WORD_SALAD_PATTERNS,
    MeetingArchetype,
)

from ..telemetry import record_eval_result

logger = logging.getLogger(__name__)

C19_LEAK_MARKERS = [
    "$1.2M", "$1.2m", "INTERNAL_ADVISORY", "networking_wedge", "political_rationale",
    "compensation negotiation", "personal equity expansion", "political wedge",
    "personal promotion", "career advisory", "career notes", "advancement vector", "political power map",
]


def check_name_anonymization_defect(
    f2_text: str,
    attendee_names: Optional[List[str]] = None,
) -> Optional[str]:
    """Detect role-based anonymization in Key Discussion Points."""
    kdp = extract_section(f2_text, r"#{1,3}\s+(?:Key Discussion Points|Key Updates)")
    target = kdp if kdp else f2_text
    matches = ROLE_ANONYMIZATION_PATTERNS.findall(target)
    if len(matches) < 3:
        return None
    unique = sorted(set(m.lower() for m in matches))
    return (
        f"Output 02 uses role-based anonymization instead of direct names "
        f"({len(matches)} instances: {', '.join(unique)}). "
        f"Prompt contract requires direct personal attribution."
    )


def check_passive_hedging_defect(f2_text: str) -> Optional[str]:
    """Detect excessive passive-aggressive cop-out disclaimers."""
    f2_lower = f2_text.lower()
    threshold = get_output2_passive_hedging_threshold()
    hits = [p for p in PASSIVE_HEDGING_PATTERNS if p in f2_lower]
    if len(hits) <= threshold:
        return None
    return (
        f"Output 02 contains {len(hits)} passive-aggressive cop-out disclaimers "
        f"(threshold: {threshold}). "
        f"Matched: {', '.join(hits[:5])}{'...' if len(hits) > 5 else ''}. "
        f"Prompt contract requires affirmative active voice."
    )


def check_c19_leak(f2_text: str) -> bool:
    f2_lower = f2_text.lower()
    return any(marker.lower() in f2_lower for marker in C19_LEAK_MARKERS)


def extract_section(text: str, header_regex: str) -> str:
    m = re.search(header_regex, text, re.IGNORECASE)
    if not m:
        return ""
    start = m.end()
    header_str = m.group(0).lstrip()
    hashes = re.match(r"^(#{1,3})\s+", header_str)
    level = len(hashes.group(1)) if hashes else 2
    next_m = re.search(rf"\n#{{1,{level}}}\s+", text[start:])
    end = start + next_m.start() if next_m else len(text)
    return text[start:end].strip()


def get_wedge_word_count(f2_text: str) -> int:
    sec = extract_section(
        f2_text,
        r"(?:#{1,3})\s+(?:\d+\.\s*)?(?:Immediate Delivery Scope(?:\s+&\s+Release Boundaries)?|Bounded Execution Wedge|Delivery Scope(?:\s+&\s+Release Boundaries)?)",
    )
    if not sec:
        sec = extract_section(
            f2_text,
            r"(?:#{1,3})\s+(?:\d+\.\s*)?(?:What\s+We\s+Decided.*Scope|In/Out\s+of\s+Scope)",
        )
    return len(sec.split()) if sec else 0


def _has_ascii_diagram(text: str) -> bool:
    if not text:
        return False
    return bool(
        re.search(r"[\u250c\u252c\u2510\u251c\u253c\u2524\u2514\u2534\u2518\u2502\u2500\u253c]{3,}", text)
        or re.search(r"[\u2554\u2566\u2557\u2560\u256c\u2563\u255a\u2569\u255d\u2551\u2550]{3,}", text)
        or re.search(r"(\+[-=]{3,}\+)", text)
    )


def _has_collateral_image(attachments: List[Dict[str, Any]]) -> bool:
    valid_types = ("image", "diagram", "png", "jpg", "jpeg", "webp")
    for a in attachments:
        if isinstance(a, dict) and a.get("attribution_mode") == "COLLATERAL_REVIEWED":
            if a.get("attachment_type", "") in valid_types:
                return True
    return False


def _has_embedded_exhibit(f2_text: str, attachments: List[Dict[str, Any]]) -> bool:
    if re.search(r"```(?:text|ascii)?[\s\S]{30,}?```", f2_text):
        return True
    if re.search(r"!\[[^\]]*\]\([^)]+\)", f2_text):
        return True
    f2_compact = f2_text.lower().replace(" ", "")
    for att in attachments:
        fname = att.get("filename", "")
        if fname and att.get("attribution_mode") == "COLLATERAL_REVIEWED":
            if fname.lower().replace(" ", "") in f2_compact:
                return True
    return False


def check_collateral_architecture_fidelity(
    f2_text: str,
    ref_artifact_text: str,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Optional[str]:
    """Judge-layer check for Output 2 collateral exhibit fidelity."""
    atts = attachments or []
    has_ascii_diag = _has_ascii_diagram(ref_artifact_text)
    has_cr_image = _has_collateral_image(atts)

    if not has_ascii_diag and not has_cr_image:
        return None
    if _has_embedded_exhibit(f2_text, atts):
        return None

    if has_ascii_diag:
        return (
            "Reference collateral contains an ASCII architecture diagram, but Output 02 lacks an "
            "embedded architecture exhibit (ASCII block, image embed, or collateral reference)."
        )
    return (
        "COLLATERAL_REVIEWED image was reviewed during the meeting, but Output 02 lacks a "
        "visual exhibit (image embed `![...](path)`) or collateral reference in the Technical Landscape section."
    )


def check_jargon_defect(f2_text: str) -> Optional[str]:
    clean_text = re.sub(r"```[\s\S]*?```", "", f2_text)
    # Strip markdown section headers so valid section header titles are not flagged as body word salad
    prose_only = re.sub(r"(?m)^#{1,6}\s+.*$", "", clean_text)
    matches = WORD_SALAD_PATTERNS.findall(prose_only)
    if not matches:
        return None
    filtered = [
        m for m in matches
        if not re.search(rf"(?:retired|formerly|prior|not|no longer).*?{re.escape(m)}", prose_only, re.IGNORECASE)
    ]
    if filtered:
        sec = extract_section(
            clean_text,
            r"(?:#{1,3})\s+(?:\d+\.\s*)?(?:Immediate Delivery Scope(?:\s+&\s+Release Boundaries)?|Bounded Execution Wedge|Delivery Scope|What\s+We\s+Decided.*Scope)",
        )
        sec_matches = WORD_SALAD_PATTERNS.findall(sec) if sec else []
        if sec_matches:
            return f"Immediate Delivery Scope contains forbidden consulting jargon: {', '.join(sorted(set(filtered)))}"
        return f"Executive recap contains forbidden pseudo-academic word salad / jargon: {', '.join(sorted(set(filtered)))}"
    return None


def check_appendix_defect(app_text: str, dur_min: Optional[int] = None) -> Optional[str]:
    from dual_pipeline.transcript_qa_core.appendix_standard import audit_appendix_gold_standard

    full_dummy = f"# Subject: Test\nDuration: {dur_min or 30}m\n\n# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n{app_text}"
    res = audit_appendix_gold_standard(full_dummy, duration_minutes=dur_min or 30)
    if not res.passed:
        return res.failures[0] if res.failures else "Appendix failed SSOT Gold Standard evaluation"
    return None


def check_grounding_failure(f2_text: str, f1_text: str = "") -> Optional[Dict[str, Any]]:
    future_dates = re.findall(r"\b(20[3-9]\d|2[1-9]\d{2})\b", f2_text)
    ungrounded_future_date = any(d not in f1_text for d in future_dates) if f1_text else bool(future_dates)
    dates_mmddyyyy = re.findall(r"\b(\d{2}/\d{2}/(20[3-9]\d|2[1-9]\d{2}))\b", f2_text)
    if dates_mmddyyyy and f1_text:
        for full_d, yr in dates_mmddyyyy:
            if full_d not in f1_text and yr not in f1_text:
                ungrounded_future_date = True
                break
    has_fake_attendee = "nonexistentperson" in f2_text.lower() or "fakeactor" in f2_text.lower()
    if ungrounded_future_date or has_fake_attendee:
        return {
            "composite_score": 45,
            "dimension_1_executive_altitude": 10,
            "dimension_2_technical_substance": 10,
            "dimension_3_strategic_synthesis": 10,
            "dimension_4_scoping_and_modality": 15,
            "verdict": "FAIL",
            "forensic_rationale": "GROUNDING FAILURE: Executive recap contains hallucinated dates or ungrounded attendees.",
            "actionable_remediation_diff": "Align all dates and attendees with forensic transcript.",
            "grounding_citations": [],
        }
    return None


def _evaluate_wedge_defect(arch: MeetingArchetype, wedge_words: int) -> Optional[str]:
    if OUTPUT2_CONTRACT.is_section_forbidden("Immediate Delivery Scope & Release Boundaries", arch):
        if wedge_words > 0:
            return f"Forbidden section 'Immediate Delivery Scope & Release Boundaries' present for {arch.value}"
    elif wedge_words > 0 and wedge_words < 50:
        return f"Superficial Immediate Delivery Scope & Release Boundaries ({wedge_words} words < 50 words floor)"
    return None


def _evaluate_appendix_defect(f2_text: str, app_m: Optional[re.Match], dur_min: int) -> Optional[str]:
    if not app_m:
        return "Missing required Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown"
    return check_appendix_defect(f2_text[app_m.end():], dur_min=dur_min)


def check_structural_density_failure(
    f1_text: str,
    f2_text: str,
    archetype: Optional[MeetingArchetype] = None,
) -> Optional[Dict[str, Any]]:
    dur_match = re.search(r"Duration:\s*~?(\d+)\s*m", f2_text, re.IGNORECASE) or re.search(r"Duration:\s*~?(\d+)\s*m", f1_text, re.IGNORECASE)
    dur_min = int(dur_match.group(1)) if dur_match else None
    if not (dur_min and dur_min >= 15):
        return None

    arch = archetype or (OUTPUT2_CONTRACT.detect_archetype(text=f1_text) if f1_text else MeetingArchetype.GENERAL)
    has_strategic_synthesis = bool(
        re.search(r"#{1,3}\s+Strategic Synthesis", f2_text, re.IGNORECASE)
        or re.search(r"\|\s*What We Heard\s*\|\s*What It Means\s*\|", f2_text, re.IGNORECASE)
    )

    wedge_words = get_wedge_word_count(f2_text)
    app_m = re.search(r"#{1,3}\s+(?:Appendix:\s*Detailed Stakeholder Perspectives|Appendix\b)", f2_text, re.IGNORECASE)

    defects: List[str] = []
    if OUTPUT2_CONTRACT.is_section_mandatory("Strategic Synthesis", arch) and not has_strategic_synthesis:
        defects.append("Missing required Strategic Synthesis ('What We Heard' vs. 'What It Means') translation matrix")

    wedge_defect = _evaluate_wedge_defect(arch, wedge_words)
    if wedge_defect:
        defects.append(wedge_defect)

    app_defect = _evaluate_appendix_defect(f2_text, app_m, dur_min)
    if app_defect:
        defects.append(app_defect)

    if not defects:
        return None

    has_appendix = app_m is not None
    target_path = "Appendix" if not has_appendix else ("Action & Governance Ledger" if arch == MeetingArchetype.ARCHITECTURE_DEEP_DIVE else "Strategic Synthesis")
    return {
        "meeting_archetype": arch.value,
        "dimension_1_executive_altitude": 16, "dimension_2_technical_substance": 16,
        "dimension_3_strategic_synthesis": 15, "dimension_4_scoping_and_modality": 18,
        "composite_score": 65, "verdict": "FAIL",
        "forensic_rationale": f"STRUCTURAL DENSITY FAILURE: {'; '.join(defects)} for a {dur_min}m meeting. Superficial summaries fail closed to preserve judge credibility.",
        "actionable_remediation_diff": "Expand executive recap according to archetype contract with complete Action & Governance Ledger and detailed Appendix breakdown.",
        "grounding_citations": [],
        "critique_diff_envelope": {
            "artifact": "OUTPUT02", "target_path": target_path,
            "observed_defect": f"Structural density failure: {'; '.join(defects)}",
            "required_delta": "Enrich recap according to archetype contract specifications.",
            "protected_paths": ["Meeting Objective"], "forbidden_mutations": [],
        },
    }


def _check_duplicate_table_failure(f2_text: str) -> Optional[Dict[str, Any]]:
    has_next = bool(re.search(r"#{1,3}\s+Next Steps & Action Items", f2_text, re.IGNORECASE))
    has_gates = bool(re.search(r"#{1,3}\s+Governed Decision Gates", f2_text, re.IGNORECASE))
    if not (has_next and has_gates):
        return None
    return {
        "composite_score": 50, "verdict": "FAIL",
        "forensic_rationale": "REDUNDANCY FAILURE: Output 02 contains duplicate mirrored tables ('Next Steps & Action Items' and 'Governed Decision Gates'). Must consolidate into a single 'Action & Governance Ledger'.",
        "actionable_remediation_diff": "Consolidate Next Steps and Decision Gates into a single Action & Governance Ledger table.",
        "grounding_citations": [],
        "critique_diff_envelope": {
            "artifact": "OUTPUT02",
            "target_path": "Action & Governance Ledger",
            "observed_defect": "Duplicate mirrored tables detected.",
            "required_delta": "Consolidate into single Action & Governance Ledger.",
            "protected_paths": ["Meeting Objective"],
            "forbidden_mutations": [],
        },
    }


def _detect_forbidden_sections(f2_text: str, arch: MeetingArchetype) -> List[str]:
    forbidden_detected: List[str] = []
    if OUTPUT2_CONTRACT.is_section_forbidden("Business Meaning and Value Case", arch) and re.search(r"#{1,3}\s+(?:\d+\.\s*)?Business Meaning and Value Case", f2_text, re.IGNORECASE):
        forbidden_detected.append("Business Meaning and Value Case")
    if OUTPUT2_CONTRACT.is_section_forbidden("Active Technical Spikes & Experimental Inquiries", arch) and re.search(r"#{1,3}\s+(?:\d+\.\s*)?Active Technical Spikes", f2_text, re.IGNORECASE):
        forbidden_detected.append("Active Technical Spikes & Experimental Inquiries")
    if OUTPUT2_CONTRACT.is_section_forbidden("Immediate Delivery Scope & Release Boundaries", arch) and get_wedge_word_count(f2_text) > 0:
        forbidden_detected.append("Immediate Delivery Scope & Release Boundaries")
    return forbidden_detected


def _build_forbidden_section_result(forbidden_detected: List[str], arch: MeetingArchetype) -> Dict[str, Any]:
    return {
        "meeting_archetype": arch.value,
        "section_scores": {
            "header_and_overview": 8,
            "key_updates_technical_substance": 25,
            "conditional_extensions": 0,
            "appendix_stakeholder_perspectives": 22,
        },
        "extension_evaluations": [
            {
                "extension_name": sec,
                "marginal_value_score": 0,
                "verdict": "FORBIDDEN_SECTION",
                "rationale": f"Section '{sec}' is strictly forbidden for archetype {arch.value}.",
            }
            for sec in forbidden_detected
        ],
        "dimension_1_executive_altitude": 20,
        "dimension_2_technical_substance": 18,
        "dimension_3_strategic_synthesis": 17,
        "dimension_4_scoping_and_modality": 0,
        "composite_score": 55,
        "verdict": "FAIL",
        "forensic_rationale": f"FORBIDDEN SECTION FAILURE: Output 02 contains forbidden section(s) for {arch.value}: {', '.join(forbidden_detected)}. Universal Core (Overview, Key Updates, Stakeholder Perspectives) required without synthetic consulting filler.",
        "actionable_remediation_diff": f"Remove forbidden section(s): {', '.join(forbidden_detected)}.",
        "grounding_citations": [],
        "critique_diff_envelope": {
            "artifact": "OUTPUT02",
            "target_path": forbidden_detected[0],
            "observed_defect": f"Forbidden section present: {', '.join(forbidden_detected)}",
            "required_delta": f"Remove {', '.join(forbidden_detected)} and fold operational context into Overview.",
            "protected_paths": ["Meeting Objective", "Key Discussion Points & Findings"],
            "forbidden_mutations": [],
        },
    }


def run_preflight_checks(
    f2_text: str,
    f1_text: str,
    span: Any,
    is_mock: bool,
    archetype: Optional[MeetingArchetype] = None,
) -> Optional[Dict[str, Any]]:
    if check_c19_leak(f2_text):
        record_eval_result(span, gate_id="micro_judge_recap", verdict="FAIL", score=30, is_mock=is_mock)
        return {
            "composite_score": 30, "verdict": "FAIL",
            "forensic_rationale": "RULE C-19 VIOLATION: Confidential career leverage / internal advisory markers leaked into Output 02 executive recap.",
            "actionable_remediation_diff": "Quarantine and strip all private advisory notes, networking wedges, or compensation markers from Output 02.",
            "grounding_citations": [],
        }
    g_fail = check_grounding_failure(f2_text, f1_text)
    if g_fail:
        record_eval_result(span, gate_id="micro_judge_recap", verdict="FAIL", score=45, is_mock=is_mock)
        return g_fail

    dup_fail = _check_duplicate_table_failure(f2_text)
    if dup_fail:
        record_eval_result(span, gate_id="micro_judge_recap", verdict="FAIL", score=50, is_mock=is_mock)
        return dup_fail

    arch = archetype or (OUTPUT2_CONTRACT.detect_archetype(text=f2_text) if f2_text else (OUTPUT2_CONTRACT.detect_archetype(text=f1_text) if f1_text else MeetingArchetype.GENERAL))
    forbidden_detected = _detect_forbidden_sections(f2_text, arch)
    if forbidden_detected:
        record_eval_result(span, gate_id="micro_judge_recap", verdict="FAIL", score=55, is_mock=is_mock)
        return _build_forbidden_section_result(forbidden_detected, arch)

    d_fail = check_structural_density_failure(f1_text, f2_text, archetype=arch)
    if d_fail:
        record_eval_result(span, gate_id="micro_judge_recap", verdict="FAIL", score=65, is_mock=is_mock)
        return d_fail
    return None


def check_action_ledger_integrity(
    f2_text: str,
    f1_text: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, List[str]]:
    from dual_pipeline.transcript_qa_core.gate2 import _NO_ACTIONS_CONFIRMED_PATTERN
    no_actions_confirmed = bool(_NO_ACTIONS_CONFIRMED_PATTERN.search(f2_text))

    sec = extract_section(
        f2_text,
        r"(?:#{1,3})\s+(?:\d+\.\s*)?(?:Action\s*(?:&|and)\s*Governance\s*Ledger|Action\s*Ledger|(?:Next\s*Steps\s*(?:&|and)\s*)?Action\s*Items)",
    )
    if not sec:
        sec = extract_section(f2_text, r"(?:#{1,3})\s+(?:\d+\.\s*)?.*(?:Action|Governance).*Ledger")
    if not sec:
        if no_actions_confirmed:
            return True, []
        return False, ["Missing mandatory Action & Governance Ledger table in Output 02."]

    table_lines = [line.strip() for line in sec.splitlines() if line.strip().startswith("|")]
    if len(table_lines) < 2:
        if no_actions_confirmed:
            return True, []
        return False, ["Action & Governance Ledger does not contain a valid markdown table."]

    header_cols = [c.strip().lower() for c in table_lines[0].split("|")[1:-1]]
    owner_idx = -1
    date_idx = -1
    for idx, col in enumerate(header_cols):
        if any(k in col for k in ("owner", "dri", "lead", "accountable")):
            owner_idx = idx
        if any(k in col for k in ("date", "timing", "target", "milestone", "due")):
            date_idx = idx

    if owner_idx == -1 and len(header_cols) >= 3:
        owner_idx = 2
    if date_idx == -1 and len(header_cols) >= 4:
        date_idx = 3

    import os
    is_offline_or_mock = (
        os.environ.get("MOCK_ENV") == "true"
        or (
            metadata is not None
            and (
                metadata.get("provider") == "offline"
                or bool(metadata.get("is_mock"))
                or bool(metadata.get("offline"))
            )
        )
    )

    known_names: Set[str] = set()
    if metadata:
        for att in metadata.get("attendees", []) or metadata.get("roster", []) or []:
            if isinstance(att, str):
                known_names.add(att.lower().strip())
                for part in att.split():
                    if len(part) > 2:
                        known_names.add(part.lower().strip())
            elif isinstance(att, dict) and "name" in att:
                n = att["name"].lower().strip()
                known_names.add(n)
                for part in n.split():
                    if len(part) > 2:
                        known_names.add(part)

    if f1_text:
        part_match = re.search(r"\*\*Participants:\*\*(.*?)(?:\n\s*\n|---|\n##|\Z)", f1_text, re.DOTALL)
        if part_match:
            for n in re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", part_match.group(1)):
                known_names.add(n.lower().strip())
                for part in n.split():
                    if len(part) > 2:
                        known_names.add(part.lower().strip())

    f1_lower = f1_text.lower() if f1_text else ""
    known_dates_str = (f1_text or "") + " " + (f2_text[:600] if f2_text else "")
    if metadata:
        meta_d = str(metadata.get("date", "")) + " " + str(metadata.get("meeting_date", ""))
        known_dates_str += " " + meta_d

    ROLE_KEYWORDS = {
        "partner_advisory", "client", "partner", "lead", "architect", "engineer",
        "delivery", "director", "manager", "chair", "sponsor", "coordinator",
        "analyst", "consultant", "exec", "executive", "product", "program",
        "technical", "leadership", "committee", "board", "working group",
    }

    defects: List[str] = []
    data_rows = 0

    for line in table_lines[1:]:
        if re.match(r"^\|[\s\-:|]+\|$", line):
            continue
        cols = [c.strip() for c in line.split("|")[1:-1]]
        if not cols or not any(cols):
            continue
        data_rows += 1

        if 0 <= owner_idx < len(cols):
            owner = cols[owner_idx].strip("*[]_ \t`")
            owner_lower = owner.lower()
            is_c25_pending = owner_lower == "unassigned (pending owner assignment)"
            if not is_c25_pending and (
                owner_lower in ("team", "all", "partner_advisory / nee", "partner_advisory/client", "unassigned", "pending", "tbd", "everyone", "n/a", "none")
                or owner_lower.startswith("unassigned")
                or owner_lower.startswith("pending owner")
            ):
                defects.append(f"Forbidden generic/placeholder owner '{owner}' in Action & Governance Ledger.")
            elif not is_c25_pending and not is_offline_or_mock and known_names and owner_lower:
                words = [w.strip("()[]\"':;,") for w in re.split(r"[\s/,()]+", owner_lower)]
                words = [w for w in words if len(w) > 2]
                matched = any(w in known_names or w in f1_lower for w in words)
                if not matched and not any(k in owner_lower for k in ROLE_KEYWORDS):
                    defects.append(f"Ungrounded owner '{owner}' in Action & Governance Ledger not present in attendee roster or transcript.")

        if 0 <= date_idx < len(cols):
            date_val = cols[date_idx].strip("*[]_ \t`")
            date_lower = date_val.lower()
            status_val = cols[6].strip().lower() if len(cols) > 6 else ""
            is_proposed_action = "proposed" in status_val or "proposed" in date_lower
            if not date_val or (
                not is_proposed_action and re.match(r"^(?:tbd\b|pending\b|unscheduled\b|n/?a\b|none\b|-+$)", date_lower)
            ):
                defects.append(f"Forbidden lazy/placeholder Target Timing '{date_val}' in Action & Governance Ledger.")
            elif not is_offline_or_mock:
                far_future_years = re.findall(r"\b(20[3-9]\d|2[1-9]\d{2})\b", date_val)
                for yr in far_future_years:
                    if yr not in known_dates_str:
                        defects.append(f"Ungrounded far-future year '{yr}' in Action & Governance Ledger not found in source transcript or metadata.")

    if data_rows == 0:
        if no_actions_confirmed:
            return True, []
        return False, ["Action & Governance Ledger table contains 0 substantive action or decision entries."]

    return (len(defects) == 0, defects)
