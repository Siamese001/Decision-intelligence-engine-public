"""
Deterministic QA Gate 2: Executive Recap QA Gates across all 7 mandatory sections.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from dual_pipeline.parameters import (
    get_output2_default_word_limits as _get_default_word_limits,
    get_output2_section_density_floors,
    get_output2_tier1_word_limits as _get_tier1_word_limits,
    get_output2_tier3_word_limits as _get_tier3_word_limits,
    get_output2_word_count_limits as _get_word_count_limits,
)
from dual_pipeline.prompts import (
    OUTPUT2_CONTRACT,
    MeetingArchetype,
    SectionRequirement,
    validate_c21_depth_floors_and_anchors,
)
from dual_pipeline.transcript_duration_helper import resolve_meeting_duration
from dual_pipeline.transcript_qa_core.gate2_advisories import (
    check_forward_claim_grounding as _check_forward_claim_grounding,
)
from dual_pipeline.transcript_qa_core.gate2_advisories import (
    check_gate_2_4_altitude as _check_gate_2_4_altitude,
)
from dual_pipeline.transcript_qa_core.gate2_advisories import (
    check_gate_2_11_numeric_tracing as _check_gate_2_11_numeric_tracing,
)
from dual_pipeline.transcript_qa_core.gate2_advisories import (
    check_semantic_section_signals as _check_semantic_section_signals,
)
from dual_pipeline.transcript_qa_core.gate2_advisories import (
    scan_code_blocks_for_unanchored_numerics as _scan_code_blocks_for_unanchored_numerics,
)

logger = logging.getLogger(__name__)

def _extract_section_text(text: str, header_pattern: str) -> str:
    m = re.search(header_pattern, text, re.IGNORECASE)
    if not m:
        return ""
    start = m.end()
    level = len(m.group(0).lstrip().split()[0])
    next_m = re.search(rf"\n#{{1,{level}}}\s+", text[start:])
    end = start + next_m.start() if next_m else len(text)
    return text[start:end].strip()

def _check_mirrored_tables(f2_text: str) -> tuple[bool, List[str]]:
    """Detects forbidden mirrored duplicate governance tables."""
    failures: List[str] = []
    has_next_steps = bool(re.search(r"#{1,3}\s+Next Steps & Action Items", f2_text, re.IGNORECASE))
    has_decision_gates = bool(re.search(r"#{1,3}\s+Governed Decision Gates", f2_text, re.IGNORECASE))
    if has_next_steps and has_decision_gates:
        failures.append(
            "[GATE_2_MIRRORED_TABLES_DETECTED] Output 2 contains separate duplicate 'Next Steps & Action Items' "
            "and 'Governed Decision Gates' tables. Consolidate into a single unified 'Action & Governance Ledger'."
        )
    return len(failures) == 0, failures


C29_EXPLICIT_MARKERS = [
    "exploratory question",
    "thought-framing",
    "exploratory inquiry",
    "operator inquiry",
    "operator framing",
    "hypothetical framing",
]


def _check_c29_operator_thought_framing_firewall(
    f2_text: str, metadata: Optional[Dict[str, Any]] = None
) -> tuple[bool, List[str]]:
    """Enforces Constraint C-29: Operator Thought-Framing Question Firewall for Output 02."""
    failures: List[str] = []
    text_lower = f2_text.lower()

    # 1. Check for explicit thought-framing markers
    for marker in C29_EXPLICIT_MARKERS:
        if marker in text_lower:
            failures.append(
                f"[C29_VIOLATION] Output 02 contains explicit operator thought-framing marker: '{marker}'"
            )

    # 2. Check for unratified operator framing questions from metadata
    if metadata and isinstance(metadata, dict):
        op_questions = metadata.get("operator_framing_questions", [])
        if isinstance(op_questions, list):
            norm_f2 = " ".join(re.sub(r"[^\w\s]", " ", text_lower).split())
            for q_item in op_questions:
                if not isinstance(q_item, dict):
                    continue
                if q_item.get("ratified", False):
                    continue
                q_text = str(q_item.get("query", "")).strip()
                if not q_text:
                    continue
                norm_q = " ".join(re.sub(r"[^\w\s]", " ", q_text.lower()).split())
                if norm_q and norm_q in norm_f2:
                    failures.append(
                        f"[C29_VIOLATION] Output 02 leaks unratified operator thought-framing query: '{q_text}'"
                    )

    return len(failures) == 0, failures


_NO_ACTIONS_CONFIRMED_PATTERN = re.compile(
    r"no new action (?:commitments )?were ratified|no (?:consolidated )?ledger is rendered"
    r"|meeting established no forward.looking task|no (?:formal )?(?:actions?|commitments?|next steps?|deliverables?) (?:were )?(?:established|made|ratified|assigned|recorded|identified|scheduled)"
    r"|no (?:owner|deadline|target) (?:was )?(?:assigned|confirmed|established)|no forward (?:action|commitment|task|decision|deliverable)[^.]*(?:was |were )?explicitly (?:accepted|committed|established|assigned|ratified)"
    r"|no (?:accountable )?owner[^.]*(?:was |were )?(?:explicitly )?(?:accepted|confirmed|assigned|committed)|remain unresolved (?:decisions?|questions?) rather than assigned (?:tasks?|actions?)"
    r"|no (?:owner-)?accepted (?:engineering |implementation )?task|declined to commit[^.]*(?:on behalf|another contributor)"
    r"|no (?:explicit |formal )?(?:acceptance|commitment|action items?|tasks?) (?:was |were )?(?:made|recorded|established|assigned) (?:in|during) (?:this |the )?(?:meeting|session)"
    r"|(?:action|task|commitment)s? (?:therefore )?remain (?:unresolved|unassigned|open)|no direct (?:action items?|tasks?|commitments?) were (?:identified|assigned|established)|discussion was (?:purely |primarily )?exploratory with no (?:assigned |formal )?action items?|no (?:verbal )?(?:delivery )?commitments?[^.]*(?:was |were )?(?:established|made|ratified|assigned|recorded|identified)"
    r"|no forward[\s-]looking (?:items?|tasks?|actions?|commitments?|deliverables?)[^.]*(?:with (?:a )?confirmed owner|were (?:identified|established|assigned))|(?:proposed )?(?:work|initiatives?|items?|tasks?|commitments?)[^.]*remains? unassigned|(?:the\s+)?supplied entries establish no (?:assigned )?(?:actions?|commitments?|deadlines?)"
    r"|no forward commitments? (?:or action items? )?were assigned"
    r"|no formal action items were assigned or ratified during this technical sync"
    r"|the discussion was an exploratory architecture deep-dive"
    r"|no (?:actions?|decisions?|owners?|dates?|commitments?)[^.]*(?:are|were) recorded",
    re.IGNORECASE,
)


def _check_structural_completeness(
    f2_text: str,
    archetype: Optional[MeetingArchetype] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> tuple[bool, Dict[str, bool], List[str], List[str]]:
    """Gate 2.1: Check required sections are present based on archetype.

    F-05 enhancement: For ARCHITECTURE_DEEP_DIVE archetype, semantically detect
    trade-off framing and spike signals in the body text — if found, require the
    corresponding section headers even if they are absent.
    """
    arch = archetype or MeetingArchetype.GENERAL
    topo = OUTPUT2_CONTRACT.get_section_topology(arch)

    section_patterns = {
        "Meeting Objective": r"(?:#{1,3})\s+(?:\d+\.\s*)?(?:Meeting\s+Objective|Executive\s+Objective|Meeting\s+Purpose|Purpose\s+(?:and|&)\s+Objective|Executive\s+Summary\s+&\s+Objective|Executive\s+Summary(?:\s+&\s+(?:The\s+Bottom\s+Line|Core\s+Decision|BLUF))?|Objective|Overview)",
        "Key Discussion Points & Findings": r"(?:#{1,3})\s+(?:\d+\.\s*)?(?:Key Discussion Points|Key Findings|Discussion Points|Context,\s+Ground\s+Truth(?:\s+&\s+Systems\s+Landscape)?|Context\s+(?:and|&)\s+Ground\s+Truth|Strategic\s+Findings|Key\s+Updates)",
        "Architectural Trade-Off Matrix": r"(?:#{1,3})\s+(?:\d+\.\s*)?(?:Architectural\s+Trade-Off(?:\s+Matrix|\s+Analysis)?|Technical\s+Trade-Off(?:\s+Matrix)?|Trade-Off\s+Matrix)",
        "Active Technical Spikes & Experimental Inquiries": r"(?:#{1,3})\s+(?:\d+\.\s*)?(?:Active\s+Technical\s+Spikes(?:\s+&\s+Experimental\s+Inquiries)?|Falsifiable\s+Spike(?:s)?(?:\s+Contracts)?|Technical\s+Spikes|Spikes\s+&\s+Explorations)",
        "Action & Governance Ledger": r"(?:#{1,3})\s+(?:\d+\.\s*)?(?:Action\s+&\s+Governance\s+Ledger|Next Steps(?:\s+&\s+Action\s+Items)?|Action Items|Governance\s+&\s+Action\s+Ledger|What\s+We\s+Decided.*Action\s+Items)",
        "Recommendations & Decisions Required": r"(?:#{1,3})\s+(?:\d+\.\s*)?(?:Recommendation(?:s)?\s+&\s+Decisions\s+Required|Executive\s+Decision|Executive\s+Decisions\s+&\s+Ratified\s+Agreements|Core\s+Decision|Decision\s+Record|Open\s+Decisions|What\s+We\s+Decided(?:\s*,\s*What's\s+In/Out\s+of\s+Scope)?|Decisions(?:\s+Ratified)?)",
        "Business Meaning and Value Case": r"(?:#{1,3})\s+(?:\d+\.\s*)?(?:Business Meaning|Business Value|Value Case|Context,\s+Ground\s+Truth(?:\s+&\s+Systems\s+Landscape)?|Ground\s+Truth\s+&\s+Systems\s+Landscape)",
        "Immediate Delivery Scope & Release Boundaries": r"(?:#{1,3})\s+(?:\d+\.\s*)?(?:Immediate Delivery Scope(?:\s+&\s+Release Boundaries)?|Bounded Execution Wedge|Delivery Scope(?:\s+&\s+Release Boundaries)?|What\s+We\s+Decided.*Scope|In/Out\s+of\s+Scope|Release\s+Boundaries)",
        "Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown": r"(?:#{1,3})\s+(?:\d+\.\s*)?(?:Appendix:\s*Detailed Stakeholder Perspectives(?:\s*&\s*Contribution Breakdown)?|Appendix\b)",
    }

    sections_present = {}
    failures: List[str] = []

    for name, pattern in section_patterns.items():
        match = bool(re.search(pattern, f2_text, re.IGNORECASE))
        sections_present[name] = match
        req = topo.get(name, SectionRequirement.OPTIONAL)

        if req == SectionRequirement.FORBIDDEN and match:
            failures.append(
                f"[GATE_2_FORBIDDEN_SECTION_DETECTED] Section '{name}' is strictly forbidden for archetype {arch.value}."
            )
        elif req == SectionRequirement.MANDATORY and not match:
            if name == "Action & Governance Ledger" and bool(_NO_ACTIONS_CONFIRMED_PATTERN.search(f2_text)):
                pass
            elif name == "Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown":
                dur = resolve_meeting_duration({}, f2_text)
                if dur is None or dur >= 15:
                    failures.append(f"Output 2 missing required section: '{name}'")
            else:
                failures.append(f"Output 2 missing required section: '{name}'")

    from .gate2_titles import check_live_meeting_subject_heading
    header_subject, subj_failures = check_live_meeting_subject_heading(f2_text, meta=meta)
    failures.extend(subj_failures)

    header_date = bool(re.search(r"(?:\*\*Date:\*\*|Date:)\s*\d{2}/\d{2}/\d{4}", f2_text))
    header_attendees = bool(re.search(r"(?:\*\*Attendees[:\s*]*\*\*|Attendees:|^\*\*Attendees\*\*\s*$|^#{1,3}\s*Attendees\b)", f2_text, re.MULTILINE))
    if not header_date:
        failures.append("Output 2 missing header Date in MM/DD/YYYY format.")
    if not header_attendees:
        failures.append("Output 2 missing Attendees section in header.")
    iso_dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", f2_text)
    if iso_dates:
        failures.append(f"Output 2 contains forbidden ISO-8601 dates: {iso_dates[:3]}")

    gate_passed = len(failures) == 0 and header_subject and header_date and not iso_dates
    semantic_failures = _check_semantic_section_signals(archetype, sections_present, f2_text)
    failures.extend(semantic_failures)
    if semantic_failures:
        gate_passed = False
    return gate_passed, sections_present, iso_dates, failures


def _validate_action_item_line(
    line: str,
    is_tech_sync: bool,
    passive_verbs: re.Pattern,
) -> tuple[bool, bool, List[str]]:
    parts = [c.strip() for c in line.split("|")]
    if len(parts) < 4:
        return True, True, []
    statuses = ("open", "pending", "blocked", "closed", "in progress", "in-progress", "proposed")
    is_action = lambda s: s.lower() in ("action", "gate", "blocker", "milestone", "decision")
    if len(parts) >= 7 and is_action(parts[2]):
        action_text, owner_text = parts[3], (parts[4] if parts[4].lower() not in statuses else parts[5])
    elif len(parts) >= 6 and is_action(parts[1]):
        action_text, owner_text = parts[2], (parts[3] if parts[3].lower() not in statuses else parts[4])
    else:
        action_text, owner_text = parts[2], parts[3]
    failures: List[str] = []
    no_passive, no_unassigned = not bool(passive_verbs.match(action_text)), True
    if not no_passive:
        failures.append(f"Passive verb in action item: '{action_text}'")
    if owner_text in ("TBC", "Unknown", "Not assigned", "") and not is_tech_sync:
        no_unassigned = False
        failures.append(f"Unassigned owner in action item: '{action_text}'")
    generic_owners = {"team", "all", "everyone", "joint", "partner_advisory / nee", "partner_advisory / apex", "partner_advisory / client", "partner_advisory/nee", "partner_advisory/client"}
    m_org = re.match(r"^(?:PartnerAdvisory|NEA|Client|NEE|APEX)\s*\(\s*([^,\n)]+)", owner_text, re.IGNORECASE)
    clean_owner = m_org.group(1).strip() if m_org else re.sub(r"\(.*?\)", "", owner_text).strip()
    if owner_text.strip().lower() in generic_owners or clean_owner.strip().lower() in generic_owners or ("/" in clean_owner and not is_tech_sync):
        no_unassigned = False
        failures.append(f"Generic or multiple owners in action item (single accountable DRI required): '{owner_text}'")
    return no_passive, no_unassigned, failures


def _check_typed_action_register(
    f2_text: str,
    archetype: Optional[MeetingArchetype] = None,
) -> tuple[bool, int, bool, bool, List[str]]:
    table_lines = [
        line for line in f2_text.splitlines()
        if re.match(r"^\|\s*\*{0,2}(?:\d+|action|gate|blocker|milestone|decision)\*{0,2}\s*\|", line, re.IGNORECASE)
    ]
    action_items_count = len(table_lines)
    no_passive_actions = True
    no_unassigned_owners = True
    failures: List[str] = []
    is_tech_sync = (archetype == MeetingArchetype.ARCHITECTURE_DEEP_DIVE)

    has_spikes = bool(re.search(r"\|\s*Spike Title\s*\|\s*Hypothesis\s*\|", f2_text, re.IGNORECASE))
    has_tradeoff = bool(re.search(r"\|\s*Option.*\|\s*Technical Mechanism\s*\|", f2_text, re.IGNORECASE))

    passive_verbs = re.compile(
        r"^\s*(Acknowledge|Note|Consider|Be aware|Discuss|Explore|Observe|Reflect|Recognize|Understand|Appreciate|Keep in mind|TBC)",
        re.IGNORECASE,
    )
    for line in table_lines:
        line_no_passive, line_no_unassigned, line_fails = _validate_action_item_line(
            line, is_tech_sync, passive_verbs
        )
        if not line_no_passive:
            no_passive_actions = False
        if not line_no_unassigned:
            no_unassigned_owners = False
        failures.extend(line_fails)

    no_actions_ratified = bool(_NO_ACTIONS_CONFIRMED_PATTERN.search(f2_text))
    sync_covered = is_tech_sync and (has_spikes or has_tradeoff)

    # F-02: Determine action_status_rationale to disambiguate empty table from extraction failure
    if action_items_count >= 1:
        action_status_rationale = "ACTIONS_PRESENT"
    elif no_actions_ratified:
        action_status_rationale = "NO_ACTIONS_CONFIRMED"
    elif sync_covered:
        action_status_rationale = "SYNC_COVERED_BY_SPIKE_OR_TRADEOFF_TABLE"
    else:
        # Empty table with no ratifying phrase — could be extraction failure
        action_status_rationale = "INDETERMINATE_ZERO_ACTIONS"
        failures.append(
            "[GATE_2_2_INDETERMINATE] Output 2 action table is empty and no explicit "
            "no-action confirmation phrase was found. This is indistinguishable from a "
            "silent extraction failure. Add a phrase such as 'No formal actions were established "
            "in this session' to confirm no actions, or populate the action table."
        )

    gate_passed = (
        (action_items_count >= 1 or no_actions_ratified or sync_covered)
        and no_passive_actions
        and no_unassigned_owners
    )

    return gate_passed, action_items_count, no_passive_actions, no_unassigned_owners, failures, action_status_rationale


def _check_word_count_bounds(
    word_count: int,
    meeting_dur: Optional[int],
    has_diagrams: bool = False,
    rigor_tier: Optional[str] = None,
    total_word_count: Optional[int] = None,
) -> tuple[bool, List[str]]:
    min_words, max_words, label = _get_word_count_limits(
        meeting_dur, has_diagrams=has_diagrams, rigor_tier=rigor_tier
    )
    if word_count < min_words:
        msg = (
            f"Output 2 executive recap word count too light: {word_count} words (< {min_words} words floor{label}). Superficial summaries erode credibility and fail closed."
            if (meeting_dur and meeting_dur >= 30 and rigor_tier != "TIER_3_LIGHTWEIGHT_STATUS")
            else f"Output 2 executive recap word count too {'short' if not meeting_dur else 'light'}: {word_count} words (< {min_words} words {'minimum' if not meeting_dur else 'floor'}{label})."
        )
        return False, [msg]
    # NOTE: Upper word ceiling is advisory-only under the simplified word limits policy.
    # Thorough summaries are never failed closed for exceeding the upper target ceiling.
    if max_words and word_count > max_words:
        logger.info(
            "Output 2 executive recap word count (%d words) exceeded advisory target ceiling (%d words). Treated as non-blocking advisory telemetry.",
            word_count,
            max_words,
        )
    return True, []


def _check_strategic_synthesis(
    f2_text: str,
    meeting_dur: Optional[int],
    archetype: Optional[MeetingArchetype] = None,
) -> tuple[bool, bool, List[str]]:
    has_strategic_synthesis = bool(
        re.search(r"#{1,3}\s+Strategic Synthesis", f2_text, re.IGNORECASE)
        or re.search(r"\|\s*What We Heard\s*\|\s*What It Means\s*\|", f2_text, re.IGNORECASE)
    )
    failures: List[str] = []
    gate_passed = True
    arch = archetype or MeetingArchetype.GENERAL
    if OUTPUT2_CONTRACT.is_section_mandatory("Strategic Synthesis", arch) and not has_strategic_synthesis:
        gate_passed = False
        failures.append(
            "Output 2 missing required Strategic Synthesis ('What We Heard' vs. 'What It Means') translation matrix."
        )
    return gate_passed, has_strategic_synthesis, failures


def _extract_section_fallback(f2_text: str, patterns: List[str]) -> str:
    for pat in patterns:
        txt = _extract_section_text(f2_text, pat)
        if txt:
            return txt
    return ""


def _check_deep_section_density(
    f2_text: str,
    meeting_dur: Optional[int],
    archetype: Optional[MeetingArchetype] = None,
    rigor_tier: Optional[str] = None,
) -> tuple[bool, int, int, int, List[str]]:
    biz_text = _extract_section_fallback(f2_text, [
        r"(?:#{1,3})\s+(?:\d+\.\s*)?(?:Business Meaning and Value Case|Business Meaning|Business Value|Value Case|Context,\s+Ground\s+Truth(?:\s+&\s+Systems\s+Landscape)?|Ground\s+Truth\s+&\s+Systems\s+Landscape)",
    ])
    wedge_text = _extract_section_fallback(f2_text, [
        r"(?:#{1,3})\s+(?:\d+\.\s*)?(?:Immediate Delivery Scope(?:\s+&\s+Release Boundaries)?|Bounded Execution Wedge|Delivery Scope(?:\s+&\s+Release Boundaries)?|What\s+We\s+Decided.*Scope|In/Out\s+of\s+Scope)",
    ])
    dec_text = _extract_section_fallback(f2_text, [
        r"(?:#{1,3})\s+(?:\d+\.\s*)?(?:Recommendations\s+&\s+Decisions\s+Required|Executive\s+Decision|Executive\s+Decisions\s+&\s+Ratified\s+Agreements|Core\s+Decision|Decision\s+Record|Decisions(?:\s+Ratified)?|What\s+We\s+Decided)",
    ])
    biz_words, wedge_words, dec_words = len(biz_text.split()), len(wedge_text.split()), len(dec_text.split())
    failures: List[str] = []
    if meeting_dur:
        sec_floors = get_output2_section_density_floors()
        t3_flr = sec_floors["tier3_section_floor"]
        std_flr = sec_floors["standard_30m_section_floor"]
        floor = t3_flr if rigor_tier == "TIER_3_LIGHTWEIGHT_STATUS" else (std_flr if meeting_dur >= 30 else max(t3_flr, int(meeting_dur * 1.5)))
        topo = OUTPUT2_CONTRACT.get_section_topology(archetype or MeetingArchetype.GENERAL)
        items = [
            ("Business Meaning and Value Case", biz_words, "Must articulate business catalyst, value at stake, and operational mechanisms."),
            ("Immediate Delivery Scope & Release Boundaries", wedge_words, "Must specify target user, in/out scope boundaries, systems touched, and acceptance criteria."),
            ("Recommendations & Decisions Required", dec_words, "Must articulate core decision required, options considered, and consequence of delay."),
        ]
        for name, count, desc in items:
            disp = "Bounded Execution Wedge" if (name == "Immediate Delivery Scope & Release Boundaries" and "bounded execution wedge" in f2_text.lower()) else (
                "Executive Decision" if (name == "Recommendations & Decisions Required" and "executive decision" in f2_text.lower() and "recommendations & decisions required" not in f2_text.lower()) else name
            )
            if ((topo.get(name) == SectionRequirement.MANDATORY) or count > 0) and count < floor:
                failures.append(f"Output 2 '{disp}' is superficial ({count} words < {floor} words minimum). {desc}")
    return len(failures) == 0, biz_words, wedge_words, dec_words, failures


def _check_appendix_completeness(
    f2_text: str,
    meeting_dur: Optional[int],
    meta: Optional[Dict[str, Any]] = None,
) -> tuple[bool, bool, List[str]]:
    dur_val = int(meeting_dur.get("duration_minutes", 30) or 30) if isinstance(meeting_dur, dict) else (int(meeting_dur) if meeting_dur is not None else 30)
    from dual_pipeline.transcript_qa_core.appendix_standard import audit_appendix_gold_standard

    meta_dict = meta or {}
    spk_shares = meta_dict.get("speaker_shares")
    if not spk_shares and meta_dict.get("spk_counts"):
        tot = sum(meta_dict["spk_counts"].values()) or 1
        spk_shares = {s: cnt / tot for s, cnt in meta_dict["spk_counts"].items()}
    if not spk_shares:
        source_txt = meta_dict.get("source_text") or meta_dict.get("f1_text") or meta_dict.get("raw_transcript") or ""
        counts: Dict[str, int] = {}
        for m in re.finditer(r"^([A-Z][a-zA-Z\s\.\x27-]+?):\s+", source_txt, re.M):
            counts[m.group(1).strip()] = counts.get(m.group(1).strip(), 0) + 1
        if counts:
            tot = sum(counts.values()) or 1
            spk_shares = {s: cnt / tot for s, cnt in counts.items()}
            meta_dict.update({"spk_counts": counts, "speaker_shares": spk_shares})

    res = audit_appendix_gold_standard(
        f2_text,
        duration_minutes=dur_val,
        speaker_shares=spk_shares,
        primary_speakers=meta_dict.get("primary_speakers"),
    )
    return res.passed, res.has_appendix, res.failures


def _check_collateral_architecture_fidelity(f2_text: str, meta: Dict[str, Any]) -> tuple[bool, bool, List[str], List[str]]:
    """Gate 2.9: Output 2 must embed or reference collateral when diagram/image collateral was reviewed.

    F-01 enhancement: When a diagram is present and the output contains an embedded
    code block with numeric thresholds (percentages, ratios, named targets), verify
    that each such threshold has a source locator (attachment: or [MM:SS]).

    Accepts either:
    - A ```text/ascii code block (>= 30 chars) — verbatim ASCII architecture exhibit.
    - A markdown image embed `![...](...)` referencing a COLLATERAL_REVIEWED attachment.
    - A collateral reference inline like '[Referenced Collateral: filename]' in the Technical Landscape.
    For PDF/document attachments, substantial architecture prose is also accepted.
    """
    attachments = meta.get("attachments", [])
    collateral_reviewed_filenames = [
        a.get("filename", "") for a in attachments
        if isinstance(a, dict) and (
            a.get("attribution_mode") == "COLLATERAL_REVIEWED"
            or (
                a.get("attachment_type") in ("diagram", "image", "png", "jpg", "jpeg", "webp")
                and a.get("attribution_mode") != "BACKGROUND"
            )
            or (
                str(a.get("filename", "")).lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
                and a.get("attribution_mode") == "COLLATERAL_REVIEWED"
            )
        )
    ]

    drop_log_reviewed: List[str] = []
    for d_key in ("output_dir", "target_output_dir", "deliverable_dir"):
        d_val = meta.get(d_key)
        if d_val and Path(d_val).is_dir():
            drop_log_path = Path(d_val) / "collateral_drop_log.json"
            if drop_log_path.is_file():
                try:
                    entries = json.loads(drop_log_path.read_text(encoding="utf-8"))
                    if isinstance(entries, list):
                        for e in entries:
                            if isinstance(e, dict):
                                fn = e.get("filename") or e.get("file_name") or ""
                                mode = str(e.get("attribution_mode") or "").upper()
                                is_rev = e.get("reviewed") is not False and mode != "BACKGROUND"
                                if fn and is_rev:
                                    drop_log_reviewed.append(fn)
                    elif isinstance(entries, dict):
                        for fn, info in entries.items():
                            if isinstance(info, dict) and info.get("attribution_mode") != "BACKGROUND":
                                drop_log_reviewed.append(fn)
                except Exception:
                    pass

    all_reviewed_fnames = set(collateral_reviewed_filenames) | set(drop_log_reviewed)

    has_diagram = bool(
        all_reviewed_fnames
        or meta.get("has_architecture_diagram")
        or meta.get("reference_architecture_ascii")
        or any(
            isinstance(a, dict) and (
                a.get("attachment_type") == "diagram"
                or a.get("attribution_mode") == "COLLATERAL_REVIEWED"
                or (
                    a.get("attachment_type") in ("image", "png", "jpg", "jpeg", "webp")
                    and a.get("attribution_mode") != "BACKGROUND"
                )
            )
            for a in attachments
        )
    )
    if not has_diagram:
        return True, False, [], []

    if all_reviewed_fnames:
        missing_reviewed = []
        for fname in sorted(all_reviewed_fnames):
            if fname and fname.lower().replace(" ", "") not in f2_text.lower().replace(" ", ""):
                missing_reviewed.append(fname)
        if missing_reviewed:
            return False, True, [
                f"Gate 2.9: Output 2 lacks reference/embed for reviewed screenshot(s): {', '.join(missing_reviewed)}"
            ], []
        return True, True, [], []

    # 2. ASCII / code block embed (generic diagram fallback)
    has_code_embed = bool(re.search(r"```(?:text|ascii)?[\s\S]{30,}?```", f2_text))
    numeric_threshold_advisory = _scan_code_blocks_for_unanchored_numerics(f2_text) if has_code_embed else []
    if has_code_embed:
        return True, True, [], numeric_threshold_advisory

    # 3. Markdown image embed: ![caption](path) present in Technical Landscape section
    landscape_section = _extract_section_text(f2_text, r"\n#{1,3}\s+Key Discussion Points.*?Findings")
    if not landscape_section:
        landscape_section = f2_text  # fallback to full text
    has_image_embed = bool(re.search(r"!\[[^\]]*\]\([^)]+\)", landscape_section))
    if has_image_embed:
        return True, True, [], []

    # 3. Inline collateral reference annotation
    for fname in [a.get("filename", "") for a in attachments if isinstance(a, dict) and a.get("attribution_mode") in ("COLLATERAL_REVIEWED", "BACKGROUND")]:
        if fname and fname.lower().replace(" ", "") in f2_text.lower().replace(" ", ""):
            return True, True, [], []
    # 4. PDF/document: accept substantial architecture prose
    pdf_attachments = [a for a in attachments if isinstance(a, dict) and a.get("attachment_type") in ("document", "pdf") and not str(a.get("filename", "")).lower().endswith((".json", ".jsonl", ".csv", ".tsv", ".xml"))]
    if pdf_attachments:
        arch_prose = bool(re.search(r"\b(?:Zone|Stage|Pipeline|Architecture|Intake|Pre.?flight|Dual.?Model|LLM|OCR|quality gate|Workstream|Classifier|Retraining|Backfill|Documentum|Hierarchy)\b", f2_text, re.IGNORECASE))
        arch_kdp_match = re.search(r"- \*\*[^*]+\*\*[^\n]{80,}(?:zone|stage|pipeline|architecture|dual.model|llm|quality gate|workstream|classifier|retraining|backfill|documentum|hierarchy)", f2_text, re.IGNORECASE)
        if arch_prose and arch_kdp_match:
            return True, True, [], []
        return False, True, ["Collateral is a PDF architecture document, but Output 2 lacks sufficient architecture prose describing zones, stages, or pipeline topology in Key Discussion Points."], []

    return False, True, [
        "Collateral contains an architecture diagram / reviewed image, but Output 2 lacks an embedded "
        "ASCII exhibit, image embed `![...](...)`, or inline collateral reference in the Technical Landscape section."
    ], []


def _check_gate_2_10_word_salad(f2_text: str) -> tuple[bool, List[str]]:
    banned_salad = [
        ("evidenced rejection behavior", r"\bevidenced rejection behavior\b"),
        ("aspirational operating concept", r"\baspirational operating concept\b"),
        ("configuration persistence versus data portability", r"\bconfiguration persistence versus data portability\b"),
        ("coaching future candidates", r"\bcoaching future candidates\b"),
        ("hypothesized routing", r"\bhypothesized routing\b"),
        ("manifest-controlled", r"\bmanifest-controlled\b"),
        ("bounded execution wedge", r"\bbounded execution wedge\b"),
    ]
    failures = [
        f"Output 2 violates Invariant 12: contains forbidden pseudo-academic word salad '{lbl}'."
        for lbl, pat in banned_salad if re.search(pat, f2_text, re.IGNORECASE)
    ]
    return len(failures) == 0, failures


def run_qa_gate_2(
    f2_text: str,
    meta: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    effective_meta = meta or metadata or {}
    failures: List[str] = []

    arch_val = effective_meta.get("archetype") or effective_meta.get("meeting_type")
    arch = MeetingArchetype.from_string(arch_val) if arch_val else OUTPUT2_CONTRACT.detect_archetype(text=f2_text)
    topo = OUTPUT2_CONTRACT.get_section_topology(arch)

    meeting_dur = resolve_meeting_duration(effective_meta, f2_text)
    app_match = re.search(r"\n#{1,3}\s+Appendix\b", f2_text, re.IGNORECASE)
    core_text = f2_text[:app_match.start()] if app_match else f2_text
    prose_text = re.sub(r"```[\s\S]*?```", "", core_text)
    word_count = len(prose_text.split())

    from dual_pipeline.transcript_qa_core.signal_density import compute_technical_signal_density
    signal_profile = compute_technical_signal_density(f2_text, metadata=effective_meta)
    has_diag = signal_profile.has_diagrams
    tier_val = signal_profile.rigor_tier.value
    total_word_count = len(f2_text.split())

    gate_2_1_passed, sections_present, iso_dates, f_2_1 = _check_structural_completeness(f2_text, archetype=arch, meta=effective_meta)
    failures.extend(f_2_1)

    gate_mirrored_passed, f_mirrored = _check_mirrored_tables(f2_text)
    failures.extend(f_mirrored)

    gate_2_2_passed, action_items_count, no_passive_actions, no_unassigned_owners, f_2_2, action_status_rationale = _check_typed_action_register(f2_text, archetype=arch)
    failures.extend(f_2_2)

    has_evidence_lineage = bool(re.search(r"\b(ev-\d+|stmt-\d+|\d{2}:\d{2})\b", f2_text, re.IGNORECASE))
    gate_2_3_passed = has_evidence_lineage
    if not gate_2_3_passed:
        failures.append("Output 2 lacks evidence lineage citations (ev-*, stmt-*, or timestamps [MM:SS]).")

    # F-04: Advisory check — forward-looking business value claims should trace to transcript locators
    forward_claim_advisory = _check_forward_claim_grounding(f2_text)

    gate_2_4_passed, wedge_valid, dec_ok = _check_gate_2_4_altitude(topo, sections_present, gate_mirrored_passed, f2_text=f2_text)

    gate_2_5_passed, f_2_5 = _check_word_count_bounds(
        word_count,
        meeting_dur,
        has_diagrams=has_diag,
        rigor_tier=tier_val,
        total_word_count=total_word_count,
    )
    failures.extend(f_2_5)

    gate_2_6_passed, has_strategic_synthesis, f_2_6 = _check_strategic_synthesis(f2_text, meeting_dur, archetype=arch)
    failures.extend(f_2_6)

    gate_2_7_passed, biz_words, wedge_words, dec_words, f_2_7 = _check_deep_section_density(
        f2_text, meeting_dur, archetype=arch, rigor_tier=tier_val
    )
    failures.extend(f_2_7)

    gate_2_8_passed, has_appendix, f_2_8 = _check_appendix_completeness(f2_text, meeting_dur, meta=effective_meta)
    failures.extend(f_2_8)

    gate_2_9_passed, has_collateral, f_2_9, numeric_threshold_advisory_2_9 = _check_collateral_architecture_fidelity(f2_text, effective_meta)
    failures.extend(f_2_9)

    gate_2_10_passed, salad_failures = _check_gate_2_10_word_salad(f2_text)
    failures.extend(salad_failures)

    gate_2_11_passed, f_2_11 = _check_gate_2_11_numeric_tracing(f2_text)
    # Gate 2.11 is a soft gate: reported but does not block PASS status
    advisory_failures = list(f_2_11)

    from dual_pipeline.transcript_qa_core.gate2_titles import check_gate_2_12_attendee_titles

    gate_2_12_passed, f_2_12, g212_details = check_gate_2_12_attendee_titles(f2_text, meta=effective_meta)
    failures.extend(f_2_12)
    gate_2_13_passed, f_2_13 = _check_c29_operator_thought_framing_firewall(f2_text, metadata=effective_meta)
    failures.extend(f_2_13)
    gate_2_14_passed, f_2_14 = validate_c21_depth_floors_and_anchors(f2_text, meta=effective_meta)
    failures.extend(f_2_14)

    from dual_pipeline.transcript_qa_core.gate2_grounding import check_gate_2_15

    gate_2_15_passed, f_2_15, g215_details = check_gate_2_15(f2_text, metadata=effective_meta)
    failures.extend(f_2_15)

    passed = all([
        gate_2_1_passed, gate_2_2_passed, gate_2_3_passed, gate_2_4_passed,
        gate_2_5_passed, gate_2_6_passed, gate_2_7_passed, gate_2_8_passed,
        gate_2_9_passed, gate_2_10_passed, gate_2_12_passed, gate_2_13_passed,
        gate_2_14_passed, gate_2_15_passed, gate_mirrored_passed,
    ])
    return {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "gates": {
            "Gate_2_1_Parsed_Structural_Completeness": {
                "passed": gate_2_1_passed, "sections": sections_present, "iso_dates_detected": iso_dates,
            },
            "Gate_2_10_Plain_English_Compliance": {
                "passed": gate_2_10_passed, "word_salad_detected": [f for f in salad_failures],
            },
            "Gate_2_2_Typed_Action_Register": {
                "passed": gate_2_2_passed, "action_count": action_items_count,
                "no_passive_actions": no_passive_actions, "no_unassigned_owners": no_unassigned_owners,
                "action_status_rationale": action_status_rationale,
            },
            "Gate_2_3_Evidence_Lineage_and_Epistemic_Status": {
                "passed": gate_2_3_passed, "evidence_citations_present": has_evidence_lineage,
                "forward_claim_advisory": forward_claim_advisory,
            },
            "Gate_2_4_Archetype_and_Altitude_Governance": {
                "passed": gate_2_4_passed, "archetype": arch.value,
                "value_case_present": sections_present.get("Business Meaning and Value Case", False),
                "decision_structure_present": dec_ok,
                "bounded_wedge_valid": wedge_valid, "no_mirrored_tables": gate_mirrored_passed,
            },
            "Gate_2_5_Word_Count_Bounds": {
                "passed": gate_2_5_passed, "word_count": word_count, "duration_minutes": meeting_dur,
            },
            "Gate_2_6_Strategic_Synthesis_Matrix": {
                "passed": gate_2_6_passed, "has_strategic_synthesis": has_strategic_synthesis,
            },
            "Gate_2_7_Deep_Section_Density": {
                "passed": gate_2_7_passed, "business_meaning_words": biz_words,
                "bounded_wedge_words": wedge_words, "executive_decision_words": dec_words,
            },
            "Gate_2_8_Appendix_Completeness": {
                "passed": gate_2_8_passed, "has_appendix": has_appendix, "duration_minutes": meeting_dur,
            },
            "Gate_2_9_Collateral_Architecture_Fidelity": {
                "passed": gate_2_9_passed, "has_diagram_collateral": has_collateral,
                "numeric_threshold_advisory": numeric_threshold_advisory_2_9,
            },
            "Gate_2_11_Numeric_Tracing": {
                "passed": gate_2_11_passed,
                "advisory": True,  # soft gate — does not block overall PASS
                "unanchored_claims": advisory_failures,
            },
            "Gate_2_12_Attendee_Title_Completeness": g212_details,
            "Gate_2_13_Operator_Thought_Framing_Firewall": {
                "passed": gate_2_13_passed,
                "failures": f_2_13,
            },
            "Gate_2_14_Section3_Thematic_Decoupling": {
                "passed": gate_2_14_passed,
                "failures": f_2_14,
            },
            "Gate_2_15_Claim_Grounding_And_Headings": g215_details,
        },
        "failures": failures,
        "advisory": advisory_failures,
    }


check_gate_2_9_collateral = _check_collateral_architecture_fidelity
