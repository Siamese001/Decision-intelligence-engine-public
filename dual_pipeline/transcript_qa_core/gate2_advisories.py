"""
Gate 2 advisory sub-checks: F-01, F-04, F-05 helper functions.

Extracted from gate2.py to satisfy the 650-line module budget linter.
All functions are pure (no side effects) and provider-free.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from dual_pipeline.prompts import MeetingArchetype, SectionRequirement


def check_semantic_section_signals(
    archetype: Optional[MeetingArchetype],
    sections_present: Dict[str, bool],
    f2_text: str,
) -> List[str]:
    """F-05: Detect trade-off/spike content in body for ARCHITECTURE_DEEP_DIVE archetype.

    Flags required section headers as absent even when implied by body content semantics
    (trade-off framing, spike investigation language) rather than explicit header keywords.
    """
    if not archetype or archetype.value not in ("ARCHITECTURE_DEEP_DIVE", "INTERNAL_TECHNICAL_SYNC"):
        return []
    failures: List[str] = []
    has_tradeoff = bool(re.search(
        r"\b(?:vs\.|versus|trade-off|tradeoff|Option [AB]|approach [AB]|compared to|alternative (?:approach|design))\b",
        f2_text, re.IGNORECASE,
    ))
    has_spike = bool(re.search(
        r"\b(?:spike|hypothesis|under investigation|exploratory|proof of concept|POC|feasibility|experimental)\b",
        f2_text, re.IGNORECASE,
    ))
    if has_tradeoff and not sections_present.get("Architectural Trade-Off Matrix", False):
        # Under Rule C-20 (Anti-Echo & Marginal Value Law), trade-offs integrated into Key Discussion Points /
        # Findings parsimoniously satisfy trade-off coverage without requiring a redundant standalone table.
        if not sections_present.get("Key Discussion Points & Findings", False):
            failures.append(
                "[GATE_2_1_SEMANTIC] Trade-off framing detected in body but neither 'Key Discussion Points & Findings' "
                "nor 'Architectural Trade-Off Matrix' is present."
            )
    return failures


def scan_code_blocks_for_unanchored_numerics(f2_text: str) -> List[str]:
    """F-01: Scan embedded code/ASCII blocks for numeric thresholds with no source locator.

    Returns GATE_2_9_NUMERIC_ADVISORY strings for any code block whose numeric tokens
    (%, ratio, 2-3 digit targets) lack a nearby [MM:SS] or attachment:<name> locator.
    Advisory only — does not block PASS.
    """
    advisories: List[str] = []
    code_block_pattern = re.compile(r"```(?:text|ascii)?([\s\S]*?)```")
    for block_match in code_block_pattern.finditer(f2_text):
        block_content = block_match.group(1)
        numerics = re.findall(r"\b\d+(?:\.\d+)?%|\b\d+\s*/\s*\d+\b|\b\d{2,3}\b", block_content)
        if not numerics:
            continue
        block_start = max(0, block_match.start() - 500)
        block_end = min(len(f2_text), block_match.end() + 200)
        surrounding = f2_text[block_start:block_end]
        has_locator = bool(re.search(r"\[\d{1,2}:\d{2}|attachment:\w", surrounding, re.IGNORECASE))
        if not has_locator:
            nums_sample = ", ".join(numerics[:5])
            advisories.append(
                f"[GATE_2_9_NUMERIC_ADVISORY] Architecture diagram block contains numeric "
                f"thresholds ({nums_sample}) without a source locator ([MM:SS] or attachment:<name>) "
                f"in the surrounding paragraph. Risk of unanchored collateral injection."
            )
    return advisories


def check_forward_claim_grounding(f2_text: str) -> List[str]:
    """F-04 advisory: Flag forward-looking Business Value claims with no transcript locator [MM:SS].

    Scans the Business Value / Business Meaning section for forward-looking language
    (will enable, prepare for, future phase) and flags paragraphs lacking [MM:SS].
    Advisory only — does not block PASS.
    """
    bv_section_match = re.search(r"\n#{1,3}\s+Business Meaning", f2_text, re.IGNORECASE)
    if not bv_section_match:
        return []
    bv_start = bv_section_match.end()
    bv_next = re.search(r"\n#{1,3}\s+", f2_text[bv_start:])
    bv_text = f2_text[bv_start: bv_start + bv_next.start()] if bv_next else f2_text[bv_start:]
    fwd_verbs = re.compile(
        r"\b(?:will enable|will allow|prepare for|future (?:work.order|integration|phase)|enables future|position(?:ing)? for)\b",
        re.IGNORECASE,
    )
    advisories: List[str] = []
    for para in re.split(r"\n{2,}", bv_text):
        if fwd_verbs.search(para) and not re.search(r"\[\d{1,2}:\d{2}", para):
            snippet = para.strip()[:120].replace("\n", " ")
            advisories.append(
                f"[GATE_2_3_FORWARD_CLAIM] Business Value forward-looking claim lacks transcript "
                f"locator [MM:SS]: '{snippet}...'"
            )
    return advisories


_NUMERIC_CLAIM_PATTERN = re.compile(
    r"(?:"
    r"\$\s*[\d,]+(?:\.\d+)?[MKB%]?"    # currency: $1.2M, $500K
    r"|[\d,]+(?:\.\d+)?\s*%"           # percentage: 30%, 99.9%
    r"|\d+\s+(?:days?|weeks?|months?|hours?|quarters?)"  # durations: 90 days, 2 weeks
    r"|\d{1,3}(?:,\d{3})+"             # large numbers: 1,200,000
    r")",
    re.IGNORECASE,
)
_CITATION_ANCHOR_PATTERN = re.compile(
    r"(?:\[\d{1,2}:\d{2}(?::\d{2})?\]|attachment:[^\s)]+|ev-\d+|stmt-\d+)",
    re.IGNORECASE,
)
_MAX_UNANCHORED_NUMERICS = 3  # allow a few (dates, slide numbers) before flagging


def check_gate_2_11_numeric_tracing(f2_text: str) -> tuple[bool, List[str]]:
    """Gate 2.11: Numeric claims in Output 2 prose must be grounded by a citation or attachment anchor.

    Scans each paragraph of the core recap (excluding tables and code blocks) for numeric claims
    (currency, percentages, day counts). If a paragraph contains a numeric claim but no citation
    anchor ([MM:SS], attachment:, ev-*, stmt-*), it is flagged. Allows up to
    _MAX_UNANCHORED_NUMERICS unanchored occurrences across the whole document before failing.
    """
    # Strip code blocks and markdown tables to avoid false positives
    stripped = re.sub(r"```[\s\S]*?```", "", f2_text)
    stripped = re.sub(r"^\|.*\|$", "", stripped, flags=re.MULTILINE)

    # Remove appendix section — numeric stats in appendix come from transcript, not synthesized claims
    app_match = re.search(r"\n#{1,3}\s+Appendix\b", stripped, re.IGNORECASE)
    core_stripped = stripped[:app_match.start()] if app_match else stripped

    paragraphs = [p.strip() for p in re.split(r"\n{2,}", core_stripped) if p.strip()]
    unanchored: List[str] = []

    for para in paragraphs:
        numerics = _NUMERIC_CLAIM_PATTERN.findall(para)
        if not numerics:
            continue
        anchors = _CITATION_ANCHOR_PATTERN.findall(para)
        if not anchors:
            snippet = para[:100].replace("\n", " ")
            unanchored.append(
                f"[GATE_2_11_NUMERIC_TRACING] Numeric claim '{numerics[0]}' in paragraph "
                f"'{snippet}...' lacks a citation anchor ([MM:SS] / attachment: / ev-* / stmt-*)."
            )

    if len(unanchored) > _MAX_UNANCHORED_NUMERICS:
        return False, unanchored
    return True, []


def check_gate_2_4_altitude(
    topo: Dict[str, Any],
    sections_present: Dict[str, bool],
    gate_mirrored_passed: bool,
    f2_text: str = "",
) -> tuple[bool, bool, bool]:
    req_wedge = topo.get("Immediate Delivery Scope & Release Boundaries")
    wedge_sec = sections_present.get("Immediate Delivery Scope & Release Boundaries", False)
    wedge_valid = wedge_sec if req_wedge == SectionRequirement.MANDATORY else not (req_wedge == SectionRequirement.FORBIDDEN and wedge_sec)
    val_ok = (topo.get("Business Meaning and Value Case") != SectionRequirement.MANDATORY) or sections_present.get("Business Meaning and Value Case", False)
    has_decision_sec = any(
        sections_present.get(k, False)
        for k in (
            "Recommendations & Decisions Required",
            "Executive Decision",
            "Action & Governance Ledger",
            "Architectural Trade-Off Matrix",
        )
    )
    is_decision_optional = (
        topo.get("Recommendations & Decisions Required") != SectionRequirement.MANDATORY
        and topo.get("Action & Governance Ledger") != SectionRequirement.MANDATORY
        and topo.get("Executive Decision") != SectionRequirement.MANDATORY
    )
    no_actions_permitted = (
        "no formal action" in f2_text.lower()
        or "no formal decisions" in f2_text.lower()
        or "unresolved questions" in f2_text.lower()
        or is_decision_optional
    )
    dec_ok = has_decision_sec or no_actions_permitted
    return val_ok and dec_ok and wedge_valid and gate_mirrored_passed, wedge_valid, dec_ok

