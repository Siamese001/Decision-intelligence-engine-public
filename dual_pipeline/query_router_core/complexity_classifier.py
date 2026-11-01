"""
dual_pipeline/query_router_core/complexity_classifier.py

5-Dimensional Complexity Tensor Router.
Replaces the flawed 1D token-window threshold with a multi-dimensional tensor:
C = <D_cog, A_margin, B_blast, S_disp, T_sla>

Guarantees:
1. False-Negative Protection: Short prompts with high-stakes financial/contractual
   conflicts are never routed to cheap low-reasoning models; they route to Sol 6.1.
2. False-Positive Protection: Massive-context (45k+ tokens) lexical formatting tasks
   are routed to Luna for extreme cost optimization.
3. Copilot Decoupling: Real-time latency requirements route to COPILOT_FAST_SNAP (Luna).
4. Epistemic Ambiguity Protection: Narrow margins (Delta < 15%) escalate to Sol.
5. Dialectical Evaluation Protection: Release gates and judicial tasks route to Astra.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

from dual_pipeline.model_registry import ModelTier
from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold

FINANCIAL_CONTRACTUAL_KEYWORDS = {
    "capex",
    "opex",
    "sow",
    "contract",
    "contractual",
    "liability",
    "warranty",
    "indemnity",
    "indemnification",
    "milestone",
    "penalty",
    "sla breach",
    "liquidated damages",
    "payment terms",
    "confidentiality breach",
    "margin compression",
    "financial contradiction",
    "budget overrun",
    "cost overrun",
    "capital expenditure",
    "operational expenditure",
    "commercial breach",
    "material breach",
    "retainage",
    "withheld payment",
    "scope reduction",
    "unbudgeted",
    "contract dispute",
}

LEXICAL_CLEANUP_TASKS = {
    "asr_cleanup",
    "webvtt_strip",
    "timestamp_strip",
    "format_text",
    "json_compact",
    "casing_normalize",
    "lexical_transform",
}

JUDICIAL_TASKS = {
    "dialectical_judge",
    "judge",
    "release_gate",
    "holistic_gate",
    "multi_judge_calibration",
    "verification_receipt",
}

COPILOT_TASKS = {
    "copilot",
    "live_copilot",
    "copilot_streaming",
    "fast_snap",
    "coaching_cue",
}


@dataclass(frozen=True)
class ComplexityTensor:
    """Encapsulates the 5 dimensions of task complexity."""
    cognitive_depth: int  # 1 (lexical) to 5 (dialectical judgment)
    ambiguity_margin: float  # 0.0 to 1.0 (Delta = c_top - c_second)
    blast_radius: int  # 1 (transient UI) to 4 (authoritative GraphDB mutation / Release Gate)
    needle_dispersion: int  # 1 (local paragraph) to 3 (multi-needle cross-document)
    latency_sla: int  # 1 (real-time <1.5s), 2 (interactive <10s), 3 (batch <300s)

    @property
    def composite_score(self) -> float:
        """Computes weighted composite score on a 1.0 to 5.0 scale."""
        margin_clamped = min(1.0, max(0.0, self.ambiguity_margin))
        ambiguity_penalty = (1.0 - margin_clamped) * 5.0
        return round(
            0.40 * self.cognitive_depth
            + 0.30 * self.blast_radius
            + 0.15 * self.needle_dispersion
            + 0.15 * ambiguity_penalty,
            4,
        )


def has_financial_or_contractual_conflict(text: str) -> bool:
    """Detects whether text involves financial, contractual, or commercial liabilities."""
    lowered = text.lower()
    pattern = r"\b(sow|capex|opex|indemnif\w+|infring\w+|breach|terminat\w+|default|nda|non-disclosure|invoic\w+|billing|rate card|unbudgeted|audit|dispute|contract|contractual|liability|warranty|milestone|penalty|retainage)\b|(\$[\d,]+|\b\d+\s*(?:million|billion|usd|eur)\b)"
    return bool(re.search(pattern, lowered))


def detect_cognitive_depth(
    payload_str: str,
    meta: Dict[str, Any],
) -> int:
    """Evaluates Cognitive Depth (D_cog in [1, 5])."""
    task = str(meta.get("task_type", "")).lower()
    if task in JUDICIAL_TASKS:
        return 5
    if task in LEXICAL_CLEANUP_TASKS:
        return 1

    # Check for strategic/contractual keywords
    if has_financial_or_contractual_conflict(payload_str):
        return 4

    # Check for multi-hop synthesis or conflict resolution
    if any(m in payload_str.lower() for m in ["contradiction", "reconcile", "multi-hop", "trade-off", "strategic"]):
        return 4

    # Respect explicit cognitive_depth if declared in metadata
    if "cognitive_depth" in meta and meta["cognitive_depth"] is not None:
        return int(meta["cognitive_depth"])

    # Check for relationship extraction / attribution intent
    if any(m in payload_str.lower() for m in ["extract", "entities", "attribution", "claims"]):
        return 2

    import re
    if re.search(r"\b(speaker\s+(attribution|disambiguation|identification)|attributed?\s+speaker)\b", payload_str.lower()):
        return 2

    return 2


def detect_blast_radius(meta: Dict[str, Any]) -> int:
    """Evaluates Blast Radius (B_blast in [1, 4])."""
    task = str(meta.get("task_type", "")).lower()
    if task in COPILOT_TASKS:
        return 1
    if task in JUDICIAL_TASKS or meta.get("mutates_graph", False):
        return 4
    if meta.get("creates_evidence_item", False):
        return 3
    if meta.get("draft_only", False):
        return 2
    return int(meta.get("blast_radius", 2))


def detect_needle_dispersion(payload_str: str, meta: Dict[str, Any]) -> int:
    """Evaluates Needle Dispersion (S_disp in [1, 3])."""
    token_est = max(len(payload_str.split()), len(payload_str) // 4) * 1.3
    if token_est > 25_000:
        return 3
    if token_est > 5_000:
        return 2
    return int(meta.get("needle_dispersion", 1))


def detect_latency_sla(meta: Dict[str, Any]) -> int:
    """Evaluates Latency SLA (T_sla in [1, 3])."""
    task = str(meta.get("task_type", "")).lower()
    if task in COPILOT_TASKS or meta.get("is_streaming", False) or meta.get("sla_seconds", 999) <= 2.0:
        return 1
    if meta.get("interactive", False) or meta.get("sla_seconds", 999) <= 10.0:
        return 2
    return int(meta.get("latency_sla", 3))


def evaluate_complexity_tensor(
    payload: Union[str, Dict[str, Any], List[Any]],
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[ComplexityTensor, ModelTier]:
    """
    Computes the 5D Complexity Tensor and maps it deterministically to ModelTier.
    """
    meta = dict(metadata or {})
    payload_str = str(payload) if not isinstance(payload, str) else payload

    # 1. Evaluate 5 dimensions
    d_cog = max(1, min(5, int(detect_cognitive_depth(payload_str, meta))))
    a_margin = max(0.0, min(1.0, float(meta.get("ambiguity_margin", meta.get("confidence_margin", 1.0)))))
    b_blast = max(1, min(4, int(detect_blast_radius(meta))))
    s_disp = max(1, min(3, int(detect_needle_dispersion(payload_str, meta))))
    t_sla = detect_latency_sla(meta)

    tensor = ComplexityTensor(
        cognitive_depth=d_cog,
        ambiguity_margin=a_margin,
        blast_radius=b_blast,
        needle_dispersion=s_disp,
        latency_sla=t_sla,
    )

    # 2. Rule-based invariant dispatch
    task = str(meta.get("task_type", "")).lower()

    # Rule A: Dialectical Judgment or Release Gate with high blast radius pins to DIALECTICAL_JUDGE (Astra)
    if d_cog == 5 or (task in JUDICIAL_TASKS and b_blast >= 3):
        return tensor, ModelTier.DIALECTICAL_JUDGE

    # Rule B: Real-time Copilot Streaming SLA always pins to COPILOT_FAST_SNAP (Luna)
    if t_sla == 1 or task in COPILOT_TASKS:
        return tensor, ModelTier.COPILOT_FAST_SNAP

    # Rule C: Ambiguity Escalation - narrow margin (Delta < 15%) escalates to Sol 6.1
    if a_margin < get_hitl_ambiguity_margin_threshold() and b_blast >= 2:
        return tensor, ModelTier.QUERY_REASONING

    # Rule D: Financial / Contractual Conflict / Strategic Decision Framing / Authoritative Evidence Creation
    if has_financial_or_contractual_conflict(payload_str) or d_cog >= 4 or task in ("decision", "decision_query") or meta.get("creates_evidence_item", False):
        return tensor, ModelTier.QUERY_REASONING

    # Rule E: Pure Lexical Cleanup / Low-Stakes Transform even on huge contexts
    if d_cog <= 2 and b_blast <= 2 and task in LEXICAL_CLEANUP_TASKS:
        return tensor, ModelTier.FAST_ROUTING

    # Rule F: General Composite Score routing
    score = tensor.composite_score
    if score >= 4.2:
        return tensor, ModelTier.DIALECTICAL_JUDGE
    if score >= 3.2:
        return tensor, ModelTier.QUERY_REASONING

    return tensor, ModelTier.FAST_ROUTING


def classify_task_complexity(
    payload: Union[str, Dict[str, Any], List[Any]],
    metadata: Optional[Dict[str, Any]] = None,
) -> ModelTier:
    """Convenience accessor returning just the resolved ModelTier."""
    _, tier = evaluate_complexity_tensor(payload, metadata)
    return tier


@dataclass(frozen=True)
class FrugalCascadePlan:
    """FrugalGPT confidence-cascaded routing plan (fast initial model -> frontier escalation on low margin)."""
    initial_tier: ModelTier
    initial_model: str
    initial_effort: str
    escalation_tier: ModelTier
    escalation_model: str
    escalation_effort: str
    min_confidence_margin: float
    requires_immediate_frontier: bool


def resolve_frugal_cascade_plan(
    payload: Union[str, Dict[str, Any], List[Any]],
    metadata: Optional[Dict[str, Any]] = None,
    min_confidence_margin: float = 0.80,
) -> FrugalCascadePlan:
    """Computes a FrugalGPT cascade routing plan starting on flash-lite/luna and escalating only on low confidence."""
    from dual_pipeline.model_registry import get_binding, resolve_tier_model_name

    tensor, primary_tier = evaluate_complexity_tensor(payload, metadata)
    meta = dict(metadata or {})
    prefer_google = str(meta.get("preferred_provider", "openai")).lower() == "google"
    google_fallbacks = get_binding(ModelTier.ANTIGRAVITY_AGENT_FLASH).allowed_fallbacks
    fast_google = google_fallbacks[0] if google_fallbacks else resolve_tier_model_name(ModelTier.ANTIGRAVITY_AGENT_FLASH)
    fast_model = fast_google if prefer_google else resolve_tier_model_name(ModelTier.FAST_ROUTING)
    frontier_model = resolve_tier_model_name(ModelTier.FRONTIER_HIGH_REASONING)

    requires_frontier = (
        primary_tier in (ModelTier.DIALECTICAL_JUDGE, ModelTier.FRONTIER_HIGH_REASONING)
        or tensor.cognitive_depth >= 4
        or tensor.blast_radius >= 4
        or tensor.ambiguity_margin < min_confidence_margin
    )
    if requires_frontier:
        return FrugalCascadePlan(
            initial_tier=primary_tier,
            initial_model=frontier_model,
            initial_effort="high",
            escalation_tier=ModelTier.FRONTIER_HIGH_REASONING,
            escalation_model=frontier_model,
            escalation_effort="max",
            min_confidence_margin=min_confidence_margin,
            requires_immediate_frontier=True,
        )

    return FrugalCascadePlan(
        initial_tier=ModelTier.FAST_ROUTING,
        initial_model=fast_model,
        initial_effort="low",
        escalation_tier=ModelTier.QUERY_REASONING,
        escalation_model=frontier_model,
        escalation_effort="high",
        min_confidence_margin=min_confidence_margin,
        requires_immediate_frontier=False,
    )
