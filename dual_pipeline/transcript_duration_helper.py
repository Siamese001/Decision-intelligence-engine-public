"""
dual_pipeline/transcript_duration_helper.py

Single Source of Truth (SSOT) utility for robust, deterministic meeting duration
resolution across metadata scaffolding, QA gates, and adversarial evaluators.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Dict, Optional


def parse_time_duration_minutes(time_str: Optional[str]) -> Optional[int]:
    """Parse duration in minutes from a time string (e.g. '04:32 PM - 04:59 PM ET' -> 27)."""
    if not time_str:
        return None
    dur_m = re.search(r"Duration:\s*~?(\d+)\s*m", time_str, re.IGNORECASE)
    if dur_m:
        try:
            return int(dur_m.group(1))
        except ValueError:
            pass
    range_m = re.search(
        r"(\d{1,2}):(\d{2})\s*([AP]M)\s*[-–]\s*(\d{1,2}):(\d{2})\s*([AP]M)",
        time_str,
        re.IGNORECASE,
    )
    if range_m:
        h1, m1, p1, h2, m2, p2 = range_m.groups()
        try:
            t1 = datetime.strptime(f"{h1}:{m1} {p1.upper()}", "%I:%M %p")
            t2 = datetime.strptime(f"{h2}:{m2} {p2.upper()}", "%I:%M %p")
            diff = int((t2 - t1).total_seconds() / 60)
            return max(1, diff + (24 * 60 if diff < 0 else 0))
        except Exception:
            pass
    return None


def parse_transcript_timestamp_duration_minutes(text: str) -> Optional[int]:
    """Calculate duration from first and last timestamp locators in transcript text."""
    if not text:
        return None
    timestamps = re.findall(r"\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]", text)
    if not timestamps or len(timestamps) < 2:
        return None

    def to_sec(ts: tuple) -> int:
        return int(ts[0]) * 3600 + int(ts[1]) * 60 + int(ts[2]) if ts[2] else int(ts[0]) * 60 + int(ts[1])

    secs = [to_sec(ts) for ts in timestamps]
    diff = max(secs) - min(secs)
    return max(1, round(diff / 60)) if diff > 0 else None


def _extract_meta_duration(meta: Dict[str, Any]) -> Optional[int]:
    for key in ("duration_minutes", "meeting_duration_minutes", "meeting_duration", "duration"):
        val = meta.get(key)
        if val is None:
            continue
        try:
            dur_int = int(val)
            if dur_int > 0:
                return dur_int
        except (ValueError, TypeError):
            continue
    return None


def _extract_explicit_text_duration(text: str) -> Optional[int]:
    if not text:
        return None
    m = re.search(r"(?:\*\*|\b)Duration(?:\*\*|\b)?[:\s*]*~?(\d+)\s*(?:m\b|min|minutes)", text, re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _extract_text_timeline_duration(text: str) -> Optional[int]:
    if not text:
        return None
    time_line_m = re.search(r"(?:\*\*|\b)Time(?:\*\*|\b)?[:\s*]*([^\n]+)", text, re.IGNORECASE)
    if not time_line_m:
        return None
    return parse_time_duration_minutes(time_line_m.group(1))


def _extract_substantive_floor(text: str) -> Optional[int]:
    if not text:
        return None
    word_count = len(text.split())
    has_turns = bool(re.search(r"\[\d{1,2}:\d{2}\]", text))
    if word_count >= 500 or (word_count >= 300 and has_turns):
        return 15
    return None


def resolve_meeting_duration(
    metadata: Optional[Dict[str, Any]] = None,
    text: str = "",
    default_substantive_floor: bool = True,
) -> Optional[int]:
    """Deterministically resolve meeting duration across metadata and textual locators."""
    meta = metadata or {}
    for candidate in (
        _extract_meta_duration(meta),
        parse_time_duration_minutes(str(meta["time"])) if meta.get("time") else None,
        _extract_explicit_text_duration(text),
        _extract_text_timeline_duration(text),
        parse_transcript_timestamp_duration_minutes(text) if text else None,
        _extract_substantive_floor(text) if default_substantive_floor else None,
    ):
        if candidate:
            return candidate
    return None


_DURATION_BUDGET_TIERS: tuple[tuple[int, int, int], ...] = (
    (90, 100000, 75),
    (60, 75000, 65),
    (45, 55000, 55),
    (31, 35000, 45),
)

_MATERIALITY_TECH_TERMS: tuple[str, ...] = (
    "architecture", "historian", "scada", "ems", "maximo", "sap", "nerc", "cip",
    "lighthouse", "opco", "integration", "prototype", "governance", "workstream",
    "pipeline", "telemetry", "database", "microservice", "sow", "roadmap",
    "latency", "throughput", "inference", "vector", "topology", "cybersecurity",
)

_EXTERNAL_PARTNER_TOKENS: tuple[str, ...] = (
    "bcg", "partner_advisory", "mckinsey", "bain", "deloitte", "accenture",
    "pwc", "kpmg", "google", "microsoft", "aws",
)


def _scale_by_duration_thresholds(
    dur: Optional[int],
    char_len: int,
    base_budget: int,
) -> int:
    """Scale mutation budget based on meeting duration or transcript volume."""
    d = dur or 0
    for min_dur, min_chars, budget in _DURATION_BUDGET_TIERS:
        if d >= min_dur or char_len >= min_chars:
            return budget
    return base_budget


def _extract_orgs_from_item(item: Any, orgs: set[str]) -> None:
    """Extract organization tokens from a single attendee item."""
    if isinstance(item, dict):
        org_val = item.get("org") or item.get("organization")
        if org_val:
            orgs.add(str(org_val).strip().lower())
            return
    if isinstance(item, str):
        item_lower = item.lower()
        for tok in _EXTERNAL_PARTNER_TOKENS:
            if re.search(rf"\b{tok}\b", item_lower):
                orgs.add(tok)


def _count_attendees_and_orgs(meta: Dict[str, Any]) -> tuple[int, set[str]]:
    """Extract attendee count and distinct organization tokens from metadata."""
    att = meta.get("attendees") or meta.get("roster") or meta.get("structured_attendees")
    orgs: set[str] = set()
    if isinstance(att, list):
        for item in att:
            _extract_orgs_from_item(item, orgs)
        return len(att), orgs

    if isinstance(att, dict):
        count = sum(len(names) for names in att.values() if isinstance(names, list))
        return count, {str(k).strip().lower() for k in att.keys()}

    return 0, orgs


def _archetype_bonus(tier_str: str, arch_str: str) -> int:
    """Calculate bonus based on explicit rigor tier and meeting archetype."""
    if tier_str == "TIER_1_DEEP_ARCHITECTURE" or any(
        k in arch_str for k in ("ARCHITECTURE", "DEEP_DIVE", "GOVERNANCE", "STEERING")
    ):
        return 20
    if tier_str == "TIER_2_STRATEGIC_OPERATIONAL" or "STRATEGIC" in arch_str:
        return 15
    return 0


def _partner_roster_bonus(meta: Dict[str, Any]) -> int:
    """Calculate bonus based on multi-organization and partner breadth."""
    att_count, orgs = _count_attendees_and_orgs(meta)
    header_blob = " ".join(
        str(meta.get(k) or "") for k in ("meeting_name", "title", "project", "client")
    ).lower()
    partner_hits = {t for t in _EXTERNAL_PARTNER_TOKENS if re.search(rf"\b{t}\b", header_blob)}
    all_orgs = orgs | partner_hits

    if len(all_orgs) >= 2 and att_count >= 5:
        return 20
    if len(all_orgs) >= 2 or len(partner_hits) >= 1 or att_count >= 6:
        return 15
    return 0


def _tech_signal_bonus(text: str, current_bonus: int) -> int:
    """Calculate bonus based on technical and architectural signal density."""
    clean_text = (text or "").lower()
    if not clean_text:
        return 0
    tech_hits = sum(1 for term in _MATERIALITY_TECH_TERMS if re.search(rf"\b{re.escape(term)}\b", clean_text))
    if tech_hits >= 6:
        return 15
    if tech_hits >= 3 and (current_bonus > 0 or len(clean_text) >= 4000):
        return 10
    return 0


def compute_materiality_budget_bonus(
    metadata: Optional[Dict[str, Any]] = None,
    text: str = "",
) -> int:
    """
    Compute additive mutation budget bonus (0..45) from meeting materiality:
    1. Explicit rigor_tier / meeting archetype (TIER_1 / ARCHITECTURE / STRATEGIC)
    2. Multi-organization / external consulting partner breadth & roster size
    3. Technical & architectural signal density in transcript text
    """
    meta = metadata or {}
    tier_str = str(meta.get("rigor_tier") or "").upper()
    if tier_str == "TIER_3_LIGHTWEIGHT_STATUS":
        return 0

    arch_str = str(meta.get("archetype") or meta.get("meeting_archetype") or meta.get("meeting_type") or "").upper()
    bonus = _archetype_bonus(tier_str, arch_str)
    bonus += _partner_roster_bonus(meta)
    bonus += _tech_signal_bonus(text, bonus)
    return min(45, bonus)


def _is_roster_edge(rel: str, src: str, tgt: str) -> bool:
    """Check if a triple corresponds to roster or tabular workstream assignment scaffolding."""
    if src.startswith("person:"):
        return (
            (tgt.startswith("org:") and rel in ("MEMBER_OF", "LEADS", "OWNS", "GOVERNS"))
            or (tgt.startswith("role:") and rel == "HAS_ROLE")
            or (tgt.startswith("workstream:") and rel in ("MEMBER_OF", "LEADS", "OWNS", "OWNS_OR_LEADS", "GOVERNS", "CONTRIBUTES_TO"))
        )
    return (
        (src.startswith("workstream:") and tgt.startswith("org:") and rel in ("MEMBER_OF", "CONTAINS", "GOVERNS", "DELIVERS", "PRODUCES", "OWNS"))
        or (src.startswith("org:") and tgt.startswith("workstream:") and rel in ("GOVERNS", "CONTAINS", "OWNS", "DELIVERS"))
    )


def _extract_roster_signal_graph(signals: list[Any]) -> tuple[int, set[str]]:
    """Count roster edges and collect associated participant entity IDs."""
    roster_edges = 0
    roster_ids: set[str] = set()
    for s in signals:
        if not isinstance(s, dict):
            continue
        rel = str(s.get("relationship") or s.get("rel_type") or s.get("predicate") or "").upper()
        src = str(s.get("from_id") or s.get("source_id") or s.get("subject") or "")
        tgt = str(s.get("to_id") or s.get("target_id") or s.get("object") or "")
        if _is_roster_edge(rel, src, tgt):
            roster_edges += 1
            roster_ids.add(src)
            roster_ids.add(tgt)
    return roster_edges, roster_ids


def count_roster_scaffolding_mutations(mutations: Optional[Dict[str, Any]]) -> int:
    """
    Count roster scaffolding mutations exempt from the domain mutation budget:
    - All person:* -> org:* (MEMBER_OF) and person:* -> role:* (HAS_ROLE) signals.
    - All person:* entity upserts and participating roster org:*/role:* entity upserts
      beyond the minimal 2-person / 1-org baseline (3 entities).
    """
    if not isinstance(mutations, dict):
        return 0
    signals = mutations.get("relationship_signals") or mutations.get("signals") or []
    entities = mutations.get("entity_upserts") or mutations.get("entities") or []

    roster_edges, roster_ids = _extract_roster_signal_graph(signals)
    roster_entities = sum(
        1 for e in entities
        if isinstance(e, dict) and str(e.get("id") or e.get("entity_id") or "") in roster_ids
    )

    return roster_edges + max(0, roster_entities - 3)


def _is_doc_meta(meta: Dict[str, Any]) -> bool:
    return bool(
        meta.get("is_document")
        or meta.get("output3_only")
        or meta.get("source_type") in ("document", "visual_document")
        or meta.get("is_visual_document")
        or meta.get("canonical_modality") == "DOCUMENT"
        or meta.get("archetype") == "DOCUMENT_MODE"
    )


def _compute_doc_pages_and_density(meta: Dict[str, Any], text: str) -> tuple[int, int]:
    units = meta.get("source_units") or []
    sc = meta.get("_source_coverage") or meta.get("source_coverage") or {}
    sc_pages = sc.get("pages_count") if isinstance(sc, dict) else 0
    text_slides = len(re.findall(r"(?im)^(?:#{1,4}\s*)?(?:Slide|Page)\s+\d+\b", text or ""))
    pages = max(int(sc_pages or 0), len(units), text_slides)
    lines = (text or "").splitlines()
    table_rows = sum(1 for ln in lines if ln.strip().startswith("|") and "---" not in ln)
    bullet_lines = sum(1 for ln in lines if ln.strip().startswith(("- ", "* ")))
    density_bonus = min(30, (table_rows // 3) + (bullet_lines // 6))
    return pages, density_bonus


def _resolve_doc_budget(meta: Dict[str, Any], text: str, base_budget: int, max_ceiling: int) -> int:
    override = meta.get("max_mutation_budget")
    if override:
        try:
            return max(base_budget, int(override))
        except (ValueError, TypeError):
            pass
    pages, density_bonus = _compute_doc_pages_and_density(meta, text)
    if str(meta.get("document_archetype") or "") == "PURSUIT_ORALS_PROPOSAL" or pages >= 30:
        log_budget = round(base_budget + 22 * math.log2(1 + max(1, pages)) + density_bonus)
        return max(base_budget, min(210, max(120, log_budget)))
    c_len = len(text) if text else 0
    if pages >= 15:
        doc_ceiling = max(max_ceiling, min(180, pages * 6 + density_bonus))
        return max(base_budget, min(doc_ceiling, max(60, pages * 4 + density_bonus)))
    if c_len >= 15000:
        return max(base_budget, min(max(max_ceiling, 120), 60 + density_bonus))
    if pages >= 10 or c_len >= 10000:
        budget = 50 + (density_bonus // 2)
    elif pages >= 5 or c_len >= 5000:
        budget = 45 + (density_bonus // 2)
    else:
        budget = 40
    return max(base_budget, min(max_ceiling, budget))


def resolve_duration_mutation_budget(
    metadata: Optional[Dict[str, Any]] = None,
    text: str = "",
    base_budget: int = 30,
    max_ceiling: int = 90,
) -> int:
    """
    Resolve Gate 3.10 graph domain mutation budget across duration AND meeting materiality.
    - Short lightweight status sessions (<= 30 mins, low materiality): base_budget (30)
    - High-materiality sessions (multi-org BCG/PartnerAdvisory/executive, deep architecture, high TSDI):
      scales with materiality bonus up to max_ceiling (60-90) even when <= 30 mins.
    - Extended sessions (31-105+ mins): 45-75+ scaled with materiality.
    """
    meta = metadata or {}
    if _is_doc_meta(meta):
        return _resolve_doc_budget(meta, text, base_budget, max_ceiling)

    override = meta.get("max_mutation_budget")
    if override:
        try:
            return int(override)
        except (ValueError, TypeError):
            pass

    dur = resolve_meeting_duration(meta, text, default_substantive_floor=False)
    char_len = len(text) if text else 0
    duration_scaled = _scale_by_duration_thresholds(dur, char_len, base_budget)
    materiality_bonus = compute_materiality_budget_bonus(meta, text)
    combined = max(duration_scaled, base_budget + materiality_bonus)
    if duration_scaled > base_budget and materiality_bonus > 0:
        combined = max(combined, duration_scaled + (materiality_bonus // 2))

    return max(base_budget, min(max_ceiling, combined))
