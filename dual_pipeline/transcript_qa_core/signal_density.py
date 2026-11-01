"""
dual_pipeline/transcript_qa_core/signal_density.py

Technical Signal Density Index (TSDI) and Adaptive Rigor Tiering Engine.
Evaluates transcript dialogue and meeting metadata to classify sessions into:
- TIER_1_DEEP_ARCHITECTURE (High-signal technical whiteboarding / algorithmic sync)
- TIER_2_STRATEGIC_OPERATIONAL (Enterprise strategic alignment / delivery milestones)
- TIER_3_LIGHTWEIGHT_STATUS (Low-signal operational check-ins / onboarding)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class RigorTier(str, Enum):
    """Governed technical rigor tier for meeting synthesis and sizing."""
    TIER_1_DEEP_ARCHITECTURE = "TIER_1_DEEP_ARCHITECTURE"
    TIER_2_STRATEGIC_OPERATIONAL = "TIER_2_STRATEGIC_OPERATIONAL"
    TIER_3_LIGHTWEIGHT_STATUS = "TIER_3_LIGHTWEIGHT_STATUS"


TECHNICAL_LEXICON: Set[str] = {
    # Vision & ML
    "ocr", "vit", "iou", "laplacian", "levenshtein", "stp", "bounding box",
    "inference", "transformer", "consensus", "threshold", "model a", "model b",
    "spatial", "vector", "embedding", "bge-m3", "prefix", "heuristic", "focal",
    "luminance", "blur", "sharpness", "classification", "token", "multimodal",
    # Systems & Infrastructure
    "maximo", "s3", "api", "kafka", "queue", "concurrency", "wal", "database",
    "sqlite", "latency", "throughput", "rpc", "grpc", "vpc", "sidecar",
    "microservice", "pipeline", "backend", "frontend", "gateway",
    # Asset Domain & Physical
    "nacelle", "turbine", "nameplate", "switch", "cabinet", "device class",
    "un-serialized", "serial number", "telemetry", "ge2x", "hirschmann", "bussmann",
    # Architecture Governance
    "falsifiable", "spike", "trade-off", "architecture", "topology", "dag",
    "state machine", "quarantine", "idempotent", "blast radius",
}


@dataclass(frozen=True)
class SignalDensityProfile:
    """Componentized sizing and governance profile derived from signal density."""
    tsdi_score: float
    rigor_tier: RigorTier
    technical_terms_found: List[str]
    has_diagrams: bool
    min_narrative_words: int
    max_narrative_words: int
    max_exhibit_words: int
    max_total_words: int
    tradeoff_matrix_required: bool
    technical_spikes_required: bool
    consulting_wedge_forbidden: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tsdi_score": round(self.tsdi_score, 4),
            "rigor_tier": self.rigor_tier.value,
            "technical_terms_count": len(self.technical_terms_found),
            "has_diagrams": self.has_diagrams,
            "min_narrative_words": self.min_narrative_words,
            "max_narrative_words": self.max_narrative_words,
            "max_exhibit_words": self.max_exhibit_words,
            "max_total_words": self.max_total_words,
            "tradeoff_matrix_required": self.tradeoff_matrix_required,
            "technical_spikes_required": self.technical_spikes_required,
            "consulting_wedge_forbidden": self.consulting_wedge_forbidden,
        }


def _scan_technical_lexicon(clean_text: str) -> tuple[List[str], int]:
    matched_terms: List[str] = []
    total_hits = 0
    for term in TECHNICAL_LEXICON:
        pattern = rf"\b{re.escape(term)}\b"
        hits = len(re.findall(pattern, clean_text))
        if hits > 0:
            matched_terms.append(term)
            total_hits += hits
    return matched_terms, total_hits


def _detect_has_diagram(meta: Dict[str, Any]) -> bool:
    if meta.get("has_architecture_diagram") or meta.get("reference_architecture_ascii"):
        return True
    for a in meta.get("attachments", []):
        if isinstance(a, dict) and (a.get("attachment_type") == "diagram" or a.get("has_diagrams")):
            return True
    return False


def _resolve_meeting_duration(meta: Dict[str, Any], text: str = "") -> int:
    from dual_pipeline.transcript_duration_helper import resolve_meeting_duration
    dur = resolve_meeting_duration(meta, text)
    if dur is not None and dur > 0:
        return dur
    if text:
        est = round(len(text.split()) / 130)
        if est >= 5:
            return est
    return 30


def compute_duration_word_bounds(
    duration_minutes: int,
    rigor_tier: RigorTier = RigorTier.TIER_2_STRATEGIC_OPERATIONAL,
    has_diagrams: bool = False,
) -> tuple[int, int, int, int]:
    """
    Computes unified, continuous duration-scaled word bounds for Output 02
    resolved from centralized SSOT parameters.
    """
    from dual_pipeline.parameters_ops import compute_output2_duration_word_bounds
    return compute_output2_duration_word_bounds(
        duration_minutes,
        is_tier3=(rigor_tier == RigorTier.TIER_3_LIGHTWEIGHT_STATUS),
        has_diagrams=has_diagrams,
    )


def _tier1_budget(dur_val: int) -> tuple[int, int, int, int]:
    return compute_duration_word_bounds(dur_val, RigorTier.TIER_1_DEEP_ARCHITECTURE, False)


def _tier2_budget(dur_val: int) -> tuple[int, int, int, int]:
    return compute_duration_word_bounds(dur_val, RigorTier.TIER_2_STRATEGIC_OPERATIONAL, False)


def _tier3_budget(dur_val: int) -> tuple[int, int, int, int]:
    return compute_duration_word_bounds(dur_val, RigorTier.TIER_3_LIGHTWEIGHT_STATUS, False)


def _classify_rigor_profile(
    tsdi_score: float,
    has_diag: bool,
    dur_val: int,
) -> tuple[RigorTier, int, int, int, int, bool, bool, bool]:
    if tsdi_score >= 0.15 or (has_diag and dur_val >= 20):
        tier = RigorTier.TIER_1_DEEP_ARCHITECTURE
        min_n, max_n, max_e, max_t = compute_duration_word_bounds(dur_val, tier, has_diag)
        return tier, min_n, max_n, max_e, max_t, True, True, True
    if tsdi_score >= 0.06:
        tier = RigorTier.TIER_2_STRATEGIC_OPERATIONAL
        min_n, max_n, max_e, max_t = compute_duration_word_bounds(dur_val, tier, has_diag)
        return tier, min_n, max_n, max_e, max_t, False, False, False
    tier = RigorTier.TIER_3_LIGHTWEIGHT_STATUS
    min_n, max_n, max_e, max_t = compute_duration_word_bounds(dur_val, tier, has_diag)
    return tier, min_n, max_n, max_e, max_t, False, False, False


def compute_technical_signal_density(
    transcript_text: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> SignalDensityProfile:
    """
    Computes deterministic Technical Signal Density Index (TSDI) and assigns
    adaptive rigor tiers and componentized word budgets.
    """
    meta = metadata or {}
    clean_text = transcript_text.lower()
    total_words = max(len(clean_text.split()), 1)

    matched_terms, total_hits = _scan_technical_lexicon(clean_text)
    has_diag = _detect_has_diagram(meta)

    # Calculate raw TSDI
    unique_term_ratio = len(matched_terms) / max(len(TECHNICAL_LEXICON), 1)
    frequency_ratio = (total_hits / max(total_words, 100)) if len(matched_terms) >= 2 else 0.0
    collateral_bonus = 0.25 if has_diag else 0.0

    raw_score = (unique_term_ratio * 0.45) + (min(frequency_ratio * 10.0, 0.30)) + collateral_bonus
    tsdi_score = min(max(raw_score, 0.0), 1.0)
    dur_val = _resolve_meeting_duration(meta, clean_text)
    (
        tier,
        min_narrative,
        max_narrative,
        max_exhibit,
        max_total,
        tradeoff_req,
        spikes_req,
        wedge_forbid,
    ) = _classify_rigor_profile(tsdi_score, has_diag, dur_val)

    meta_tier = meta.get("rigor_tier")
    if not meta_tier:
        arch_val = str(meta.get("archetype") or meta.get("meeting_type") or "").upper()
        if "STRATEGIC" in arch_val:
            meta_tier = "TIER_2_STRATEGIC_OPERATIONAL"
    if meta_tier:
        tier_key = str(meta_tier).upper()
        if tier_key in RigorTier.__members__:
            tier = RigorTier[tier_key]
            min_narrative, max_narrative, max_exhibit, max_total = compute_duration_word_bounds(
                dur_val, tier, has_diag
            )
            if tier == RigorTier.TIER_1_DEEP_ARCHITECTURE:
                tradeoff_req, spikes_req, wedge_forbid = True, True, True
            elif tier in (RigorTier.TIER_2_STRATEGIC_OPERATIONAL, RigorTier.TIER_3_LIGHTWEIGHT_STATUS):
                tradeoff_req, spikes_req, wedge_forbid = False, False, False

    return SignalDensityProfile(
        tsdi_score=tsdi_score,
        rigor_tier=tier,
        technical_terms_found=sorted(matched_terms),
        has_diagrams=has_diag,
        min_narrative_words=min_narrative,
        max_narrative_words=max_narrative,
        max_exhibit_words=max_exhibit,
        max_total_words=max_total,
        tradeoff_matrix_required=tradeoff_req,
        technical_spikes_required=spikes_req,
        consulting_wedge_forbidden=wedge_forbid,
    )
