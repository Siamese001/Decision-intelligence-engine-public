"""HITL Criticality Evaluation & Multi-Tier Gating Framework (Omega-Criticality).

Enforces that Human-in-the-Loop (HITL) decisions surface not only on confidence
margin variance (e.g. Delta > 0.20 or Delta <= 0.20), but also on absolute
systemic criticality:
1. Tier 0: Irreversible/Catastrophic operations (DB drops, entity deletions, C-19 boundary touches).
2. Tier 1: Core Architectural & Concurrency Refactoring (L0-L6 DAG changes, 400-line budget splits, WAL locks).
3. Tier 2: High-Stake Divergence (competing design options where Delta > 0.20).
4. Tier 3: Material Ambiguity (close alternatives where Delta <= 0.20).
5. Tier 4: Low-Stakes Autonomous (deterministic Class A unit tests, documentation).
"""
from __future__ import annotations

import math
import re
from enum import IntEnum
from typing import Any, Mapping, Sequence


class CriticalityTier(IntEnum):
    """Hierarchical criticality tiers for HITL decision arbitration."""
    TIER_0_IRREVERSIBLE = 0   # Lethal/Irreversible: DB drops, hard deletions, C-19 firewall
    TIER_1_ARCHITECTURAL = 1  # Core architecture: L0-L6 DAG, 400-line budget, WAL concurrency
    TIER_2_DIVERGENT = 2      # High-stake divergence: Delta > 0.20
    TIER_3_AMBIGUOUS = 3      # Material ambiguity: Delta <= 0.20
    TIER_4_AUTONOMOUS = 4     # Low-stakes routine operations: Class A tests, internal docs


TIER_DESCRIPTIONS = {
    CriticalityTier.TIER_0_IRREVERSIBLE: "Lethal / Irreversible Operation (Mandatory HITL, zero bypass)",
    CriticalityTier.TIER_1_ARCHITECTURAL: "Core Architectural & Concurrency Refactoring (Mandatory HITL)",
    CriticalityTier.TIER_2_DIVERGENT: "High-Stake Divergent Alternatives (Surfaces on Delta > 0.20)",
    CriticalityTier.TIER_3_AMBIGUOUS: "Material Ambiguity (Surfaces on Delta <= 0.20)",
    CriticalityTier.TIER_4_AUTONOMOUS: "Low-Stakes Autonomous Progression",
}

# Keywords indicating Tier 0 Irreversible operations
TIER_0_PATTERNS = (
    r"\b(drop\s+table|delete\s+from|truncate\s+table)\b",
    r"\b(hard_delete|purge_data|wipe_database|destroy_graph)\b",
    r"\b(c19_breach|career_firewall_override|export_internal_advisory)\b",
    r"\b(credential_leak|secret_exfiltration|bypass_auth)\b",
    r"\b(pr_merge_authorization|approve\s+(?:the\s+|this\s+)?pr\s+merge|authorize\s+(?:the\s+|this\s+|governed\s+)?(?:pr\s+)?(?:merge|promotion)|merge\s+(?:the\s+|this\s+)?pull\s+request|governed\s+(?:pr\s+)?merge|release\s+activation)\b",
    r"\b(?:merge|merging|promote|promoting|promotion|publish|publishing|publication|activate|activating|activation)\b[\s\S]{0,200}?\b(?:into\s+(?:the\s+|our\s+|local\s+)?main(?:\s+branch)?|to\s+(?:the\s+|our\s+|local\s+)?main(?:\s+branch)?|(?:the\s+|this\s+)?(?:candidate|branch|release|pull\s+request|knowledge\s+graph))\b",
)

# Keywords indicating Tier 1 Architectural Refactoring
TIER_1_PATTERNS = (
    r"\b(l0_l6_layer_change|cyclic_import|layer_inversion)\b",
    r"\b(400_line_budget_split|monolithic_refactor|decompose_subpackage)\b",
    r"\b(wal_mode_change|concurrency_lock_bypass|raw_sqlite_connect)\b",
    r"\b(provider_free_boundary_breach|synthetic_embedding_injection)\b",
    r"\b(schema_migration|database_alter_column)\b",
    r"\b(rule\s+files?|agent\s+rules?|governance\s+rules?|frontmatter\s+syntax|frontmatter\s+schema|hook\s+configuration)\b",
)


def calculate_epistemic_entropy(scores: Sequence[float]) -> float:
    """Calculate normalized Shannon entropy over candidate confidence scores.

    Returns:
        float: Entropy value in [0.0, 1.0]. A value near 1.0 indicates maximum
        disagreement/uncertainty among candidates.
    """
    if not scores or len(scores) <= 1:
        return 0.0

    total = sum(scores)
    if total <= 0.0:
        return 1.0

    probs = [s / total for s in scores]
    # Shannon entropy in bits
    entropy = 0.0
    for p in probs:
        if p > 1e-9:
            entropy -= p * math.log2(p)

    max_entropy = math.log2(len(scores))
    if max_entropy <= 0.0:
        return 0.0

    return round(min(1.0, max(0.0, entropy / max_entropy)), 4)


def _extract_context_override(
    context: Mapping[str, Any] | None,
) -> tuple[CriticalityTier, float, list[str]] | None:
    if not context or "tier" not in context:
        return None
    try:
        tier_val = context["tier"]
        if isinstance(tier_val, CriticalityTier):
            return tier_val, 1.0, ["Explicit CriticalityTier override in context."]
        if isinstance(tier_val, int) and tier_val in CriticalityTier._value2member_map_:
            return CriticalityTier(tier_val), 1.0, ["Explicit tier integer in context."]
    except (ValueError, KeyError):
        pass
    return None


def _check_explicit_flags(
    context: Mapping[str, Any] | None,
) -> tuple[CriticalityTier, float, list[str]] | None:
    if not context:
        return None
    if context.get("is_destructive") or context.get("is_schema_drop") or context.get("touches_c19_firewall"):
        return (
            CriticalityTier.TIER_0_IRREVERSIBLE,
            1.0,
            ["Context declares destructive, schema drop, or C-19 firewall mutation."],
        )
    if context.get("affects_layer_dag") or context.get("violates_400_line_budget") or context.get("modifies_storage_locks"):
        return (
            CriticalityTier.TIER_1_ARCHITECTURAL,
            0.85,
            ["Context declares L0-L6 DAG, 400-line budget, or storage locking impact."],
        )
    return None


def _build_combined_text(context: Mapping[str, Any] | None, operation_text: str) -> str:
    combined = operation_text.lower()
    if not context:
        return combined
    parts = [
        combined,
        str(context.get("category", "")).lower(),
        str(context.get("action", "")).lower(),
        str(context.get("operation_type", "")).lower(),
        str(context.get("details", "")).lower(),
    ]
    return " ".join(parts)


def _match_tier_patterns(
    combined_text: str,
) -> tuple[CriticalityTier, float, list[str]] | None:
    for pat in TIER_0_PATTERNS:
        if re.search(pat, combined_text):
            return CriticalityTier.TIER_0_IRREVERSIBLE, 1.0, [f"Matched Tier 0 pattern: '{pat}'"]
    for pat in TIER_1_PATTERNS:
        if re.search(pat, combined_text):
            return CriticalityTier.TIER_1_ARCHITECTURAL, 0.85, [f"Matched Tier 1 pattern: '{pat}'"]
    return None


def _match_heuristic_keywords(
    combined_text: str,
) -> tuple[CriticalityTier, float, list[str]]:
    if any(k in combined_text for k in ("architecture", "refactor", "database", "concurrency", "security")):
        return CriticalityTier.TIER_1_ARCHITECTURAL, 0.75, ["Heuristic match on core architectural / technical design keywords."]
    if any(k in combined_text for k in ("design_choice", "tradeoff", "strategy", "commercial")):
        return CriticalityTier.TIER_2_DIVERGENT, 0.60, ["Heuristic match on high-stakes design / strategy tradeoff."]
    if any(k in combined_text for k in ("ambiguity", "entity_resolution", "vocabulary", "speaker")):
        return CriticalityTier.TIER_3_AMBIGUOUS, 0.40, ["Heuristic match on ambiguity / entity resolution."]
    return CriticalityTier.TIER_4_AUTONOMOUS, 0.10, ["Routine operation; default to Tier 4 Autonomous."]


def evaluate_criticality_score(
    context: Mapping[str, Any] | None = None,
    operation_text: str = "",
) -> tuple[CriticalityTier, float, list[str]]:
    """Forensically inspect context and operation strings to assign a CriticalityTier.

    Returns:
        (tier: CriticalityTier, score: float, reasons: list[str])
    """
    override = _extract_context_override(context)
    if override:
        return override

    flag_result = _check_explicit_flags(context)
    if flag_result:
        return flag_result

    combined_text = _build_combined_text(context, operation_text)

    pattern_result = _match_tier_patterns(combined_text)
    if pattern_result:
        return pattern_result

    return _match_heuristic_keywords(combined_text)
