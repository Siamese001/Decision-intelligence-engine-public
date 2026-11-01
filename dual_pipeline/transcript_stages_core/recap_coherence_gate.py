"""Deterministic Cross-Pass Coherence Gate & Section-Targeted Remediation Router for Deliverable 02.

Enforces:
1. Section-Targeted Remediation: Slices prior recap candidate and re-executes only the failed pass (2A, 2B, or 2C),
   freezing passing sections byte-identically with SHA256 hash preservation.
2. Cross-Pass Coherence Gate: Action & Governance Ledger owners and entities (Pass 2B) must be a subset of
   entities declared in Strategic Narrative (Pass 2A) and the Forensic Transcript.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Dict, List

logger = logging.getLogger(__name__)

SENTINEL_NON_OWNERS = frozenset({
    "unassigned",
    "team",
    "all",
    "everyone",
    "n/a",
    "none",
    "-",
    "—",
    "",
    "tbd",
    "joint",
    "shared",
    "item / description",
    "owner",
    "accountable owner",
    "owner / assignee",
})


class CrossPassDivergenceError(ValueError):
    """Raised when Action Ledger entities or decisions contradict or diverge from Strategic Narrative or Transcript."""
    pass


def parse_recap_sections(recap_text: str) -> Dict[str, str]:
    """Slices an authoritative Output 2 recap into Pass 2A, 2B, and 2C components."""
    clean = (recap_text or "").strip()
    if not clean:
        return {"2A": "", "2B": "", "2C": ""}

    ledger_pattern = r"(?im)^#{1,3}\s+Action & Governance Ledger[^\n]*$"
    appendix_pattern = r"(?im)^#{1,3}\s+(?:Appendix:\s*)?Detailed Stakeholder Perspectives[^\n]*$"
    tone_pattern = r"(?im)^#{1,3}\s+Meeting Tone Arc[^\n]*$"

    ledger_match = re.search(ledger_pattern, clean)
    appendix_match = re.search(appendix_pattern, clean)
    tone_match = re.search(tone_pattern, clean)

    sections = {"2A": "", "2B": "", "2C": ""}

    if ledger_match and appendix_match and ledger_match.start() < appendix_match.start():
        sections["2A"] = clean[:ledger_match.start()].strip()
        if tone_match and ledger_match.start() < tone_match.start() < appendix_match.start():
            sections["2B"] = clean[ledger_match.end():tone_match.start()].strip()
            sections["2A"] = f"{sections['2A']}\n\n{clean[tone_match.start():appendix_match.start()].strip()}".strip()
        else:
            sections["2B"] = clean[ledger_match.end():appendix_match.start()].strip()
        sections["2C"] = clean[appendix_match.end():].strip()
    elif ledger_match and appendix_match and appendix_match.start() < ledger_match.start():
        # Inverse layout fallback
        sections["2A"] = clean[:appendix_match.start()].strip()
        sections["2C"] = clean[appendix_match.end():ledger_match.start()].strip()
        sections["2B"] = clean[ledger_match.end():].strip()
    elif ledger_match:
        sections["2A"] = clean[:ledger_match.start()].strip()
        if tone_match and ledger_match.start() < tone_match.start():
            sections["2B"] = clean[ledger_match.end():tone_match.start()].strip()
            sections["2A"] = f"{sections['2A']}\n\n{clean[tone_match.start():].strip()}".strip()
        else:
            sections["2B"] = clean[ledger_match.end():].strip()
    elif appendix_match:
        sections["2A"] = clean[:appendix_match.start()].strip()
        sections["2C"] = clean[appendix_match.end():].strip()
    else:
        sections["2A"] = clean

    return sections


def compute_section_hashes(sections: Dict[str, str]) -> Dict[str, str]:
    """Computes SHA256 hex digests for each section."""
    return {
        pass_id: hashlib.sha256(content.encode("utf-8")).hexdigest()
        for pass_id, content in sections.items()
    }


def resolve_remediation_target_passes(critique: str) -> List[str]:
    """Maps a judge critique or QA failure to the responsible pass(es)."""
    if not critique or not critique.strip():
        return ["2A", "2B", "2C"]

    c_lower = critique.lower()

    # Pass 2C indicators: appendix, stakeholder perspectives, attendee breakdown, Rule C-21
    is_2c = bool(
        re.search(
            r"\b(appendix|perspective|perspectives|contributions?|stakeholder|speaker breakdown|c-21|c21)\b",
            c_lower,
        )
    )

    # Pass 2B indicators: action items, ledger, commitments, milestones, deadlines, owners
    is_2b = bool(
        re.search(
            r"\b(action|ledger|commitments?|milestones?|target date|deadlines?|owner|owners|table format)\b",
            c_lower,
        )
    )

    # Pass 2A indicators: narrative, executive summary, objective, findings, discussion points, header, duration
    is_2a = bool(
        re.search(
            r"\b(narrative|executive summary|meeting objective|discussion points?|strategic findings?|technical decisions?|header|duration)\b",
            c_lower,
        )
    )

    targets = []
    if is_2a:
        targets.append("2A")
    if is_2b:
        targets.append("2B")
    if is_2c:
        targets.append("2C")

    # If specific sections were identified, return targeted subset; else fallback to full DAG
    return targets if targets else ["2A", "2B", "2C"]


def is_sentinel_non_owner(owner: str) -> bool:
    """Checks whether an owner field value indicates an unassigned or non-individual sentinel."""
    clean = owner.strip().lower()
    if clean in SENTINEL_NON_OWNERS:
        return True
    sentinel_phrases = (
        "unassigned",
        "not assigned",
        "not individually",
        "not specified",
        "not identified",
        "not discussed",
        "no individual owner",
        "no specific owner",
        "no owner",
        "none assigned",
        "pending assignment",
        "to be determined",
        "open item",
        "joint ownership",
        "all attendees",
        "entire team",
        "partner_advisory team",
    )
    return any(phrase in clean for phrase in sentinel_phrases)


def extract_ledger_owners(ledger_text: str) -> List[str]:
    """Extracts owner names from Markdown table rows in the Action Ledger."""
    owners: List[str] = []
    lines = [line.strip() for line in ledger_text.splitlines() if line.strip()]

    current_owner_col: Optional[int] = None

    for i, line in enumerate(lines):
        if not line.startswith("|") or not line.endswith("|"):
            current_owner_col = None
            continue
        cols = [c.strip() for c in line.split("|")[1:-1]]
        if not cols:
            continue

        # Skip separator line (e.g. |---|---|---|)
        if all(re.match(r"^:?-+:?$", c) for c in cols):
            continue

        lower_cols = [c.lower() for c in cols]

        # Check if this line is a header row (followed by a separator line or contains column keywords)
        is_header = False
        if i + 1 < len(lines):
            next_line = lines[i + 1]
            if next_line.startswith("|") and next_line.endswith("|"):
                next_cols = [c.strip() for c in next_line.split("|")[1:-1]]
                if next_cols and all(re.match(r"^:?-+:?$", c) for c in next_cols):
                    is_header = True

        if not is_header and current_owner_col is None:
            has_owner_col = any(any(kw in c for kw in ("owner", "assignee", "accountable", "responsible")) for c in lower_cols)
            has_other_col = any(any(kw in c for kw in ("item", "action", "decision", "date", "status", "next step", "dependencies")) for c in lower_cols)
            if has_owner_col and has_other_col:
                is_header = True

        if is_header:
            owner_idx = next(
                (
                    idx
                    for idx, c in enumerate(lower_cols)
                    if any(kw in c for kw in ("owner", "assignee", "accountable", "responsible"))
                ),
                None,
            )
            current_owner_col = owner_idx
            continue

        # If this table has no recognized owner column, do not extract owners from it
        if current_owner_col is None:
            continue

        if len(cols) > current_owner_col:
            raw_owner = cols[current_owner_col]
            clean_owner = re.sub(r"[*_`\[\]]", "", raw_owner).strip()
            # Clean email or role suffixes, e.g. "Amit Ayer (PartnerAdvisory)" -> "Amit Ayer"
            clean_owner = re.sub(r"\s*\([^)]*\)", "", clean_owner).strip()
            if not is_sentinel_non_owner(clean_owner) and len(clean_owner) > 1:
                # Handle comma or 'and' separated multi-owners
                parts = re.split(r",|\band\b", clean_owner, flags=re.IGNORECASE)
                for part in parts:
                    clean_part = part.strip()
                    if not is_sentinel_non_owner(clean_part) and len(clean_part) > 1:
                        owners.append(clean_part)

    return owners


def validate_cross_pass_coherence(
    narrative: str,
    ledger_text: str,
    forensic_transcript: str,
) -> bool:
    """Validates that all owners and actors in Pass 2B exist in Pass 2A or the Forensic Transcript.

    Raises CrossPassDivergenceError if an action item owner is hallucinated or ungrounded.
    """
    owners = extract_ledger_owners(ledger_text)
    if not owners:
        return True

    text_corpus_lower = f"{narrative}\n\n{forensic_transcript}".lower()

    for owner in owners:
        owner_lower = owner.lower()
        # Direct substring match in narrative or transcript
        if owner_lower in text_corpus_lower:
            continue

        # Check token-level containment for first/last names (e.g. "Amit" in "Amit Ayer")
        owner_tokens = [tok for tok in re.split(r"\s+", owner_lower) if len(tok) > 2]
        if owner_tokens and all(tok in text_corpus_lower for tok in owner_tokens):
            continue

        logger.error(
            "CrossPassDivergenceError: Owner '%s' in 2B Action Ledger does not exist in 2A Narrative or Transcript.",
            owner,
        )
        raise CrossPassDivergenceError(
            f"Cross-pass entity divergence: Owner '{owner}' declared in Action & Governance Ledger (Pass 2B) "
            "does not exist in Strategic Narrative (Pass 2A) or Forensic Transcript."
        )

    return True


def stitch_recap_sections(narrative: str, ledger: str, perspectives: str) -> str:
    """Combines 2A narrative, 2B ledger, and 2C perspectives into authoritative Output 2 format."""
    ledger_clean = re.sub(r"(?im)^#+\s*Action & Governance Ledger[^\n]*\n", "", ledger.strip()).strip()
    perspectives_clean = re.sub(
        r"(?im)^#+\s*(?:Appendix:\s*)?Detailed Stakeholder Perspectives[^\n]*\n",
        "",
        perspectives.strip(),
    ).strip()

    use_h1 = bool(re.search(r"(?m)^#\s+[A-Za-z0-9]", narrative))
    h2_prefix = "#" if use_h1 else "##"

    return (
        f"{narrative.strip()}\n\n"
        f"{h2_prefix} Action & Governance Ledger\n\n"
        f"{ledger_clean}\n\n"
        f"# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
        f"{perspectives_clean}"
    ).strip()
