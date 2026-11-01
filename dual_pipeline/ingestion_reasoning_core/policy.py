"""Deterministic policy loading, validation, and complexity-based decision routing."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..contracts import create_artifact
from ..core import canonical_json, digest, stable_id, utc_now
from .constants import (
    AMBIGUOUS_IDENTITY_OUTCOMES,
    CLASSIFICATION_ORDER,
    COMPLEXITY_ORDER,
)
from .models import IngestionReasoningPolicy, validate_policy_raw


def load_ingestion_reasoning_policy(path: Path) -> IngestionReasoningPolicy:
    """Load and strictly validate the versioned ingestion model policy."""
    resolved = Path(path).expanduser().resolve()
    raw = json.loads(resolved.read_text(encoding="utf-8"))
    validate_policy_raw(raw)
    return IngestionReasoningPolicy(
        raw=raw,
        path=resolved,
        config_digest=digest(raw),
    )


def _promote(current: str, target: str) -> str:
    return target if COMPLEXITY_ORDER[target] > COMPLEXITY_ORDER[current] else current


def _highest_classification(bundle: Mapping[str, Any]) -> str:
    classifications = [
        str(candidate["classification"]) for candidate in bundle["candidates"]
    ]
    return max(classifications, key=CLASSIFICATION_ORDER.__getitem__)


def _evaluate_initial_complexity(
    routing: Mapping[str, Any],
    candidate_count: int,
    entity_mention_count: int,
    has_high_materiality: bool,
    has_complex_type: bool,
    ambiguous_identity_count: int,
    semantic_match_count: int,
) -> str:
    if (
        candidate_count <= routing["low_max_candidates"]
        and entity_mention_count <= 1
        and not has_high_materiality
        and not has_complex_type
        and ambiguous_identity_count == 0
        and semantic_match_count == 0
    ):
        return "low"
    if (
        candidate_count <= routing["normal_max_candidates"]
        and not has_high_materiality
        and not has_complex_type
    ):
        return "normal"
    return "high"


def _evaluate_final_complexity(
    initial: str,
    routing: Mapping[str, Any],
    candidate_count: int,
    prompt_size: int,
    has_high_materiality: bool,
    has_complex_type: bool,
    ambiguous_identity_count: int,
) -> tuple[str, list[str]]:
    final = initial
    reasons: list[str] = []
    if has_high_materiality:
        final = _promote(final, "high")
        reasons.append("HIGH_MATERIALITY")
    if has_complex_type:
        final = _promote(final, "high")
        reasons.append("COMPLEX_CANDIDATE_TYPE")
    if ambiguous_identity_count:
        final = _promote(final, "high")
        reasons.append("AMBIGUOUS_IDENTITY")
    if candidate_count > routing["normal_max_candidates"]:
        final = _promote(final, "high")
        reasons.append("LARGE_CANDIDATE_SET")
    if (
        candidate_count >= routing["most_complex_min_candidates"]
        or prompt_size >= routing["most_complex_prompt_chars"]
        or ambiguous_identity_count >= routing["most_complex_min_ambiguous_identities"]
    ):
        final = "most_complex"
        reasons.append("MOST_COMPLEX_THRESHOLD")
    return final, reasons


def _determine_reason_and_call(
    final: str,
    classification_allowed: bool,
    provider_egress_allowed: bool,
) -> tuple[bool, str]:
    if final == "low":
        return False, "LOW_COMPLEXITY_DETERMINISTIC"
    if not classification_allowed:
        return False, "RESTRICTED_EGRESS_FORBIDDEN"
    if not provider_egress_allowed:
        return False, "POLICY_EGRESS_FORBIDDEN"
    return True, "CONDITIONAL_MODEL_SELECTED"


def build_ingestion_reasoning_decision(
    *,
    policy: IngestionReasoningPolicy,
    mode: str,
    offline: bool,
    bundle: Mapping[str, Any],
    plan: Mapping[str, Any],
    resolutions: list[Mapping[str, Any]],
    comparison: Mapping[str, Any],
    governed_context: Mapping[str, Any],
    routes: list[Mapping[str, Any]],
    estimated_prompt_size_chars: int | None = None,
    prompt_size_source: str = "LEGACY_PACKET_PROXY",
    request_metrics: Mapping[str, Any] | None = None,
    budget_verdict: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Make the complete model/no-model decision before provider construction."""

    if mode not in {"shadow", "advisory"}:
        raise ValueError("ingestion reasoning mode must be shadow or advisory")
    candidate_count = len(bundle["candidates"])
    entity_mention_count = sum(
        len(candidate["entity_mentions"]) for candidate in bundle["candidates"]
    )
    ambiguous_identity_count = sum(
        value["outcome"] in AMBIGUOUS_IDENTITY_OUTCOMES for value in resolutions
    )
    semantic_match_count = sum(
        len(value["semantic_matches"])
        for value in comparison["comparisons"].values()
    )
    prompt_size = (
        estimated_prompt_size_chars
        if estimated_prompt_size_chars is not None
        else len(
            canonical_json(
                {
                    "bundle": bundle,
                    "plan": plan,
                    "resolutions": resolutions,
                    "comparison": comparison,
                    "governed_context": governed_context,
                    "routes": routes,
                }
            )
        )
    )
    has_high_materiality = any(
        candidate["materiality"] == "high" for candidate in bundle["candidates"]
    )
    high_types = set(policy.routing["high_candidate_types"])
    candidate_types = {
        candidate["candidate_type"] for candidate in bundle["candidates"]
    }
    has_complex_type = bool(candidate_types & high_types)

    initial = _evaluate_initial_complexity(
        routing=policy.routing,
        candidate_count=candidate_count,
        entity_mention_count=entity_mention_count,
        has_high_materiality=has_high_materiality,
        has_complex_type=has_complex_type,
        ambiguous_identity_count=ambiguous_identity_count,
        semantic_match_count=semantic_match_count,
    )
    final, promotion_reasons = _evaluate_final_complexity(
        initial=initial,
        routing=policy.routing,
        candidate_count=candidate_count,
        prompt_size=prompt_size,
        has_high_materiality=has_high_materiality,
        has_complex_type=has_complex_type,
        ambiguous_identity_count=ambiguous_identity_count,
    )

    highest_classification = _highest_classification(bundle)
    classification_allowed = (
        CLASSIFICATION_ORDER[highest_classification]
        <= CLASSIFICATION_ORDER[policy.maximum_egress_classification]
    )
    provider_egress_allowed = (
        policy.provider_egress_allowed and not offline and classification_allowed
    )
    call_selected, reason_code = _determine_reason_and_call(
        final=final,
        classification_allowed=classification_allowed,
        provider_egress_allowed=provider_egress_allowed,
    )

    model_policy = policy.model_for_tier(final) if call_selected else None
    decision_id = stable_id(
        "ingestion-reasoning-decision",
        bundle["run_id"],
        policy.config_digest,
        digest(
            {
                "plan": plan,
                "resolutions": resolutions,
                "comparison": comparison,
                "governed_context": governed_context,
                "routes": routes,
            }
        ),
        length=28,
    )
    return create_artifact(
        "ingestion-reasoning-decision",
        decision_id,
        decision_id=decision_id,
        run_id=bundle["run_id"],
        trace_id=bundle["trace_id"],
        mode=mode,
        bundle_digest=bundle["canonical_digest"]["value"],
        plan_digest=plan["canonical_digest"]["value"],
        resolution_digest=digest(resolutions),
        comparison_digest=digest(comparison),
        governed_context_digest=digest(governed_context),
        route_digest=digest(routes),
        policy_config_digest=policy.config_digest,
        initial_complexity=initial,
        final_complexity=final,
        promotion_reasons=sorted(set(promotion_reasons)),
        call_selected=call_selected,
        reason_code=reason_code,
        selected_model_tier=(
            model_policy.model_tier if model_policy is not None else None
        ),
        selected_model=model_policy.model if model_policy is not None else None,
        reasoning_effort=(
            model_policy.reasoning_effort if model_policy is not None else None
        ),
        provider_egress_allowed=provider_egress_allowed,
        candidate_count=candidate_count,
        entity_mention_count=entity_mention_count,
        ambiguous_identity_count=ambiguous_identity_count,
        semantic_match_count=semantic_match_count,
        estimated_prompt_size_chars=prompt_size,
        maximum_primary_calls=1,
        maximum_repair_calls=1,
        maximum_escalation_calls=0,
        decided_at=utc_now(),
        extensions={
            "com.partner_advisory.nee.ingestion-reasoning-policy": {
                "policy_path": policy.path.name,
                "highest_classification": highest_classification,
                "offline": offline,
            },
            "com.partner_advisory.nee.read-write-budget": {
                "prompt_size_source": prompt_size_source,
                "request_input_chars": int(
                    (request_metrics or {}).get("request_input_chars", 0)
                ),
                "request_schema_chars": int(
                    (request_metrics or {}).get("request_schema_chars", 0)
                ),
                "request_serialized_chars": int(
                    (request_metrics or {}).get("request_serialized_chars", prompt_size)
                ),
                "budget_status": (budget_verdict or {}).get(
                    "status", "NOT_APPLICABLE"
                ),
                "budget_reason_code": (budget_verdict or {}).get(
                    "reason_code", "PROVIDER_CALL_NOT_SELECTED"
                ),
                "maximum_provider_request_chars": int(
                    policy.routing["maximum_provider_request_chars"]
                ),
            },
        },
    )
