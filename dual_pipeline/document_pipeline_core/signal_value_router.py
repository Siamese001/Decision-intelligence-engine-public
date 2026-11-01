"""
dual_pipeline/document_pipeline_core/signal_value_router.py

Upstream Document Signal & Value Router.
Evaluates incoming documents prior to Stage 03 extraction to compute a multi-dimensional
strategic signal profile (internal client portal provenance, workstream/charter density,
financial/NAV presence, staffing roster breadth, and perspective origin).
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from dual_pipeline.document_pipeline_core.archetype_classifier import (
    DocumentArchetype,
    classify_document_archetype,
)


class SignalTier(str, Enum):
    HIGH_SIGNAL_AUTHORITATIVE = "HIGH_SIGNAL_AUTHORITATIVE"
    MEDIUM_SIGNAL_OPERATIONAL = "MEDIUM_SIGNAL_OPERATIONAL"
    LOW_SIGNAL_ENABLEMENT_OR_TEMPLATE = "LOW_SIGNAL_ENABLEMENT_OR_TEMPLATE"


class PerspectiveOrigin(str, Enum):
    CLIENT_INTERNAL_NEA = "CLIENT_INTERNAL_NEA"
    CLIENT_INTERNAL_NEE = "CLIENT_INTERNAL_NEE"
    JOINT_CLIENT_PARTNER = "JOINT_CLIENT_PARTNER"
    PARTNER_partner_advisory_INTERNAL = "PARTNER_partner_advisory_INTERNAL"
    EXTERNAL_OR_GENERAL = "EXTERNAL_OR_GENERAL"


class ExtractionMode(str, Enum):
    EXHAUSTIVE_PORTFOLIO_AND_ROSTER = "EXHAUSTIVE_PORTFOLIO_AND_ROSTER"
    EXHAUSTIVE_STRUCTURAL_EXTRACTION = "EXHAUSTIVE_STRUCTURAL_EXTRACTION"
    STANDARD_DOCUMENT_EXTRACTION = "STANDARD_DOCUMENT_EXTRACTION"
    BOUNDED_WORKSHOP_EXTRACTION = "BOUNDED_WORKSHOP_EXTRACTION"


_NEA_PORTAL_URL_RE = re.compile(
    r"(?:https?://)?planning\.apex_globalanalytics\.com(?:/[a-zA-Z0-9_\-/?=&%]*)?",
    re.IGNORECASE,
)

_NEA_INTERNAL_CUES_RE = re.compile(
    r"\b(?:apex_global\s+analytics|nea\s+planning|nea\s+operations|quarterly\s+planning|"
    r"all\s+workstream\s+assignments|est\.\s*nav\s*\(\$k\)|unfilled\s+fte|assignable\s+people|"
    r"forge\b|dociq\b|grid_os\b)\b",
    re.IGNORECASE,
)

_NEE_INTERNAL_CUES_RE = re.compile(
    r"\b(?:apex_global\s+energy|florida\s+power\s*&\s*light|apex\b|neer\b|pgd\s+renewables|"
    r"nuclear\s+asset\s+health|position\s+request\s+form|hr360|documentum|maximo|jarvis\b)\b",
    re.IGNORECASE,
)

_TECHNICAL_ARCHITECTURE_CUES_RE = re.compile(
    r"\b(?:architecture|process\s+flow|integration|gateway|pipeline|api\b|federation|"
    r"authentication|successfactors|tyk\b|documentum|maximo|mulesoft|kafka|conduit|"
    r"dociq|forge\b|hr360|scada|arcgis|backfill|classifier|lifecycle)\b",
    re.IGNORECASE,
)

_partner_advisory_CUES_RE = re.compile(
    r"\b(?:partner_advisory\b|partner_advisory\s+responsibility|partner_advisory\s+traverse|agentic\s+managed\s+services|"
    r"geo-pod|industry-pod|sales\s+immersion)\b",
    re.IGNORECASE,
)

_FINANCIAL_BUDGET_RE = re.compile(
    r"\$-?\d[\d,]*(?:\.\d+)?\s*[KMBkmb]?\b|\b(?:est\.\s*nav|hourly\s+rate|estimated\s+service\s+fees|"
    r"rebate\s+credit|rate\s+card\s+revenue|rack\s+rate)\b",
    re.IGNORECASE,
)

_CHARTER_AND_MISSION_RE = re.compile(
    r"\b(?:charter|missions?|workstreams?|resource\s+note:|deliverables?|statement\s+of\s+work|"
    r"no\s+associated\s+charter|assignment\s+progress)\b",
    re.IGNORECASE,
)

_ROSTER_ROLE_CUES_RE = re.compile(
    r"(?:\bPM:\s*[A-Z][a-zA-Z\s,\-']+\b|\bSM:\s*[A-Z][a-zA-Z\s,\-']+\b|"
    r"\bFuture\s+Hire\s*#\d+\b|\b(?:Contractor|Principal|Staff|Senior|Associate)\b)",
)

_SPOOF_GUARD_RE = re.compile(
    r"\b(?:test-spoof|mock-url|fake-citation|synthetic-benchmark-spoof|placeholder\s+link|lorem\s+ipsum)\b",
    re.IGNORECASE,
)

_AUTHORITATIVE_ARCHETYPES = frozenset({
    DocumentArchetype.CONTRACTUAL_SOW_PROSE,
    DocumentArchetype.POPULATED_ACCOUNT_PLAN_DASHBOARD,
    DocumentArchetype.PURSUIT_ORALS_PROPOSAL,
    DocumentArchetype.FINANCIAL_STAFFING_WORKBOOK,
    DocumentArchetype.VISUAL_TOPOLOGY_AND_ORG_CHART,
    DocumentArchetype.ROLE_FRAMEWORK_CATALOG,
})


@dataclass(frozen=True)
class DocumentSignalProfile:
    """Structured strategic routing profile for an incoming document."""

    archetype: str
    signal_tier: str
    strategic_value_score: float
    perspective_origin: str
    recommended_extraction_mode: str
    dynamic_mutation_budget: int
    requires_graph_delta_hitl: bool
    enable_unit_batch_extraction: bool
    enable_5_agent_intelligence: bool
    workstream_density_count: int
    financial_signal_count: int
    roster_signal_count: int
    page_count: int
    signal_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _extract_meta_page_int(source: Dict[str, Any]) -> int:
    for k in ("pages_count", "page_count"):
        val = source.get(k)
        if isinstance(val, int) and val > 0:
            return int(val)
    return 0


def _detect_pages(text: str, meta: Dict[str, Any]) -> int:
    direct = _extract_meta_page_int(meta)
    if direct:
        return direct
    cov = meta.get("_source_coverage") or meta.get("source_coverage")
    if isinstance(cov, dict) and _extract_meta_page_int(cov):
        return _extract_meta_page_int(cov)
    units = meta.get("source_units") or meta.get("slide_units")
    if isinstance(units, list) and units:
        return len(units)
    totals = [
        int(m)
        for m in re.findall(r"\bPage\s+\d+\s+of\s+(\d+)\b", text or "", re.I)
        if m.isdigit() and 0 < int(m) <= 500
    ]
    if totals:
        return max(totals)
    numbered = [
        int(p)
        for p in re.findall(r"(?:^|\n)\s*(?:#+\s*)?(?:Slide|Page)\s+(\d+)\b", text or "", re.I)
        if p.isdigit() and 0 < int(p) <= 500
    ]
    if numbered:
        return max(numbered)
    return 1 if (text and text.strip()) else 0


def _resolve_perspective_origin(
    meta: Dict[str, Any],
    has_nea_portal: bool,
    nea_hits: int,
    nee_hits: int,
    partner_advisory_hits: int,
) -> PerspectiveOrigin:
    explicit = str(meta.get("perspective_origin") or "").upper()
    if explicit in PerspectiveOrigin.__members__:
        return PerspectiveOrigin[explicit]
    if has_nea_portal or (nea_hits >= 3 and partner_advisory_hits == 0):
        return PerspectiveOrigin.CLIENT_INTERNAL_NEA
    if (nea_hits >= 1 or nee_hits >= 2) and partner_advisory_hits >= 1:
        return PerspectiveOrigin.JOINT_CLIENT_PARTNER
    if nee_hits >= 2 and partner_advisory_hits == 0:
        return PerspectiveOrigin.CLIENT_INTERNAL_NEE
    if partner_advisory_hits >= 2 and nea_hits == 0 and nee_hits == 0:
        return PerspectiveOrigin.PARTNER_partner_advisory_INTERNAL
    if nea_hits >= 1:
        return PerspectiveOrigin.CLIENT_INTERNAL_NEA
    return PerspectiveOrigin.CLIENT_INTERNAL_NEE if nee_hits >= 1 else PerspectiveOrigin.EXTERNAL_OR_GENERAL


def _archetype_score_delta(archetype: DocumentArchetype) -> tuple[float, str]:
    if archetype == DocumentArchetype.TABULAR_PORTFOLIO_EXPORT:
        return 0.45, "tabular_portfolio_export_archetype"
    if archetype in _AUTHORITATIVE_ARCHETYPES:
        return 0.35, f"authoritative_archetype:{archetype.value.lower()}"
    if archetype == DocumentArchetype.STRATEGIC_OFFERING_PLAYBOOK:
        return 0.25, "strategic_offering_playbook"
    if archetype == DocumentArchetype.SPARSE_ENABLEMENT_WORKSHOP:
        return -0.15, "sparse_enablement_workshop_penalty"
    return 0.0, ""


def _perspective_score_delta(perspective: PerspectiveOrigin) -> tuple[float, str]:
    if perspective in (PerspectiveOrigin.CLIENT_INTERNAL_NEA, PerspectiveOrigin.CLIENT_INTERNAL_NEE):
        return 0.20, f"client_internal_perspective:{perspective.value.lower()}"
    if perspective == PerspectiveOrigin.JOINT_CLIENT_PARTNER:
        return 0.15, "joint_client_partner_perspective"
    return 0.0, ""


def _score_document_signals(
    archetype: DocumentArchetype,
    perspective: PerspectiveOrigin,
    pm_sm_count: int,
    fin_hits: int,
    charter_hits: int,
    roster_hits: int,
    pages: int,
    tech_hits: int = 0,
) -> tuple[float, List[str]]:
    score = 0.20
    reasons: List[str] = []

    for delta, reason in (_archetype_score_delta(archetype), _perspective_score_delta(perspective)):
        score += delta
        if reason:
            reasons.append(reason)

    if pm_sm_count >= 10:
        score += 0.20
        reasons.append(f"high_workstream_leadership_density:{pm_sm_count}")
    elif pm_sm_count >= 2:
        score += 0.10
        reasons.append(f"workstream_leadership_present:{pm_sm_count}")

    if fin_hits >= 10:
        score += 0.15
        reasons.append(f"dense_financial_nav_metrics:{fin_hits}")
    elif fin_hits >= 2:
        score += 0.08
        reasons.append(f"financial_metrics_present:{fin_hits}")

    if tech_hits >= 8:
        score += 0.25
        reasons.append(f"dense_technical_architecture_signals:{tech_hits}")
    elif tech_hits >= 3:
        score += 0.18
        reasons.append(f"technical_architecture_signals:{tech_hits}")

    if charter_hits >= 10 or roster_hits >= 20:
        score += 0.10
        reasons.append("exhaustive_charter_and_roster_signals")

    if pages >= 15 and score >= 0.60:
        score += 0.05
        reasons.append(f"multi_page_authoritative_depth:{pages}pp")

    return round(max(0.0, min(1.0, score)), 4), reasons


def _compute_dynamic_budget(
    archetype: DocumentArchetype,
    signal_tier: SignalTier,
    pages: int,
    pm_sm_count: int,
    roster_hits: int,
    meta: Dict[str, Any],
    tech_hits: int = 0,
) -> int:
    override = meta.get("max_mutation_budget")
    if override is not None:
        try:
            val = int(override)
            if val > 0:
                return min(2500, max(30, val))
        except (ValueError, TypeError):
            pass

    if signal_tier == SignalTier.LOW_SIGNAL_ENABLEMENT_OR_TEMPLATE:
        return 35
    if archetype == DocumentArchetype.TABULAR_PORTFOLIO_EXPORT:
        estimated_workstreams = max(pm_sm_count // 2, 10)
        return min(2000, max(300, (estimated_workstreams * 6) + roster_hits + 60))
    if signal_tier == SignalTier.HIGH_SIGNAL_AUTHORITATIVE:
        page_scaled = round(60 + 25 * math.log2(1 + max(1, pages)) + min(80, roster_hits // 2))
        return min(600, max(90, page_scaled))
    base_med = 90 if tech_hits >= 3 else 45
    return min(150, max(base_med, 40 + pages * 4 + tech_hits * 5))


def _resolve_tier_and_mode(
    archetype: DocumentArchetype,
    score: float,
) -> tuple[SignalTier, ExtractionMode]:
    high_tier_archs = _AUTHORITATIVE_ARCHETYPES | {DocumentArchetype.TABULAR_PORTFOLIO_EXPORT}
    if archetype == DocumentArchetype.SPARSE_ENABLEMENT_WORKSHOP or score < 0.40:
        tier = SignalTier.LOW_SIGNAL_ENABLEMENT_OR_TEMPLATE
    elif score >= 0.70 or archetype in high_tier_archs:
        tier = SignalTier.HIGH_SIGNAL_AUTHORITATIVE
    else:
        tier = SignalTier.MEDIUM_SIGNAL_OPERATIONAL

    if archetype == DocumentArchetype.TABULAR_PORTFOLIO_EXPORT:
        mode = ExtractionMode.EXHAUSTIVE_PORTFOLIO_AND_ROSTER
    elif tier == SignalTier.HIGH_SIGNAL_AUTHORITATIVE:
        mode = ExtractionMode.EXHAUSTIVE_STRUCTURAL_EXTRACTION
    elif tier == SignalTier.LOW_SIGNAL_ENABLEMENT_OR_TEMPLATE:
        mode = ExtractionMode.BOUNDED_WORKSHOP_EXTRACTION
    else:
        mode = ExtractionMode.STANDARD_DOCUMENT_EXTRACTION
    return tier, mode


def _empty_signal_profile() -> DocumentSignalProfile:
    return DocumentSignalProfile(
        archetype=DocumentArchetype.AUTHORITATIVE_MEMO_OR_SPEC.value,
        signal_tier=SignalTier.LOW_SIGNAL_ENABLEMENT_OR_TEMPLATE.value,
        strategic_value_score=0.0,
        perspective_origin=PerspectiveOrigin.EXTERNAL_OR_GENERAL.value,
        recommended_extraction_mode=ExtractionMode.BOUNDED_WORKSHOP_EXTRACTION.value,
        dynamic_mutation_budget=30,
        requires_graph_delta_hitl=False,
        enable_unit_batch_extraction=False,
        enable_5_agent_intelligence=False,
        workstream_density_count=0,
        financial_signal_count=0,
        roster_signal_count=0,
        page_count=0,
        signal_reasons=["empty_or_whitespace_document"],
    )


def _format_unit_text_part(idx: int, unit: Any) -> str:
    if not isinstance(unit, dict):
        return ""
    content = str(unit.get("content") or unit.get("text") or "").strip()
    if not content:
        return ""
    pnum = unit.get("unit_number") or unit.get("page") or idx
    return content if "## page" in content.lower() else f"## Page {pnum}\n{content}"


def reconstruct_document_text_from_metadata(
    document_text: Optional[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Return stripped document_text or reconstruct it from metadata slide_units/source_units."""
    stripped = str(document_text or "").strip()
    if stripped:
        return stripped
    meta = metadata or {}
    if isinstance(meta.get("source_text"), str) and meta["source_text"].strip():
        return meta["source_text"].strip()
    for key in ("slide_units", "source_units"):
        units = meta.get(key)
        if isinstance(units, list) and units:
            parts = [p for i, u in enumerate(units, start=1) if (p := _format_unit_text_part(i, u))]
            if parts:
                return "\n\n".join(parts)
    return ""


def _resolve_batch_and_5agent_flags(
    meta: Dict[str, Any], tier: SignalTier, archetype: DocumentArchetype, pages: int, text_len: int
) -> Tuple[bool, bool]:
    is_high_non_tab = tier == SignalTier.HIGH_SIGNAL_AUTHORITATIVE and archetype != DocumentArchetype.TABULAR_PORTFOLIO_EXPORT
    enable_batches = bool(meta.get("enable_unit_batch_extraction") is True or (is_high_non_tab and pages >= 12))
    enable_5_agent = bool(meta.get("enable_5_agent_intelligence") is True or (is_high_non_tab and text_len >= 1500))
    return enable_batches, enable_5_agent


def assess_document_signal_profile(
    document_text: Optional[str],
    metadata: Optional[Dict[str, Any]] = None,
    graph_path: Optional[Path] = None,
) -> DocumentSignalProfile:
    """Evaluate an incoming document's strategic signal density, perspective origin, and budget."""
    meta = dict(metadata or {})
    stripped = reconstruct_document_text_from_metadata(document_text, meta)
    if not stripped:
        return _empty_signal_profile()

    is_spoofed = bool(_SPOOF_GUARD_RE.search(stripped))
    archetype = classify_document_archetype(stripped, meta)
    pm_sm_count = len(re.findall(r"\b(?:SM|PM):\s*[^\n]+", stripped))
    spoof_downgraded = False
    if is_spoofed and pm_sm_count < 4 and archetype == DocumentArchetype.TABULAR_PORTFOLIO_EXPORT:
        archetype = DocumentArchetype.AUTHORITATIVE_MEMO_OR_SPEC
        spoof_downgraded = True

    has_nea_portal = bool(_NEA_PORTAL_URL_RE.search(stripped)) and not is_spoofed
    nea_hits = len(_NEA_INTERNAL_CUES_RE.findall(stripped))
    nee_hits = len(_NEE_INTERNAL_CUES_RE.findall(stripped))
    partner_advisory_hits = len(_partner_advisory_CUES_RE.findall(stripped))
    fin_hits = len(_FINANCIAL_BUDGET_RE.findall(stripped))
    charter_hits = len(_CHARTER_AND_MISSION_RE.findall(stripped))
    roster_hits = len(_ROSTER_ROLE_CUES_RE.findall(stripped))
    tech_hits = len(_TECHNICAL_ARCHITECTURE_CUES_RE.findall(stripped))
    pages = _detect_pages(stripped, meta)

    perspective = _resolve_perspective_origin(meta, has_nea_portal, nea_hits, nee_hits, partner_advisory_hits)
    score, reasons = _score_document_signals(
        archetype, perspective, pm_sm_count, fin_hits, charter_hits, roster_hits, pages, tech_hits
    )
    if spoof_downgraded or (is_spoofed and score > 0.45 and pm_sm_count < 4):
        score = min(score, 0.35)
        reasons.append("spoof_guard_clamped")

    tier, ext_mode = _resolve_tier_and_mode(archetype, score)
    budget = _compute_dynamic_budget(archetype, tier, pages, pm_sm_count, roster_hits, meta, tech_hits)
    requires_delta_hitl = bool(
        tier == SignalTier.HIGH_SIGNAL_AUTHORITATIVE
        and (perspective != PerspectiveOrigin.EXTERNAL_OR_GENERAL or graph_path is not None)
    )
    enable_batches, enable_5_agent = _resolve_batch_and_5agent_flags(meta, tier, archetype, pages, len(stripped))
    workstream_count = max(pm_sm_count // 2, len(re.findall(r"\bNEA\s+[A-Z0-9]", stripped)))

    return DocumentSignalProfile(
        archetype=archetype.value, signal_tier=tier.value, strategic_value_score=score,
        perspective_origin=perspective.value, recommended_extraction_mode=ext_mode.value,
        dynamic_mutation_budget=budget, requires_graph_delta_hitl=requires_delta_hitl,
        enable_unit_batch_extraction=enable_batches, enable_5_agent_intelligence=enable_5_agent,
        workstream_density_count=workstream_count, financial_signal_count=fin_hits,
        roster_signal_count=roster_hits, page_count=pages, signal_reasons=reasons,
    )


def apply_signal_profile_to_metadata(
    document_text: Optional[str],
    metadata: Dict[str, Any],
    graph_path: Optional[Path] = None,
) -> DocumentSignalProfile:
    """Compute DocumentSignalProfile and bind routing controls into document metadata in-place."""
    profile = assess_document_signal_profile(document_text, metadata, graph_path=graph_path)
    metadata["document_signal_profile"] = profile.to_dict()
    if "spoof_guard_clamped" in profile.signal_reasons:
        metadata["document_archetype"] = profile.archetype
    else:
        metadata.setdefault("document_archetype", profile.archetype)
    metadata["signal_tier"] = profile.signal_tier
    metadata["strategic_value_score"] = profile.strategic_value_score
    metadata["perspective_origin"] = profile.perspective_origin
    metadata["recommended_extraction_mode"] = profile.recommended_extraction_mode
    metadata["requires_graph_delta_hitl"] = profile.requires_graph_delta_hitl
    metadata["max_mutation_budget"] = profile.dynamic_mutation_budget
    metadata["enable_5_agent_intelligence"] = profile.enable_5_agent_intelligence
    if "enable_unit_batch_extraction" not in metadata and profile.enable_unit_batch_extraction:
        metadata["enable_unit_batch_extraction"] = True
    return profile
