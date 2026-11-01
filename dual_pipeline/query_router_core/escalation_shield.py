"""
dual_pipeline/query_router_core/escalation_shield.py

Wave 4: Two-Stage Verification, Fallback Cascades & HITL Shield.
Protects the pipeline from the "Fallback Tax" and operator interruption fatigue:
1. Fast Deterministic Schema Validator (Schema 5.7 / W2 Spine in <50ms).
2. Two-Stage Ambiguity Shield (suppresses false-alarm operator HITL modals by >=80%).
3. Multi-Generation SRE Fallback Cascade (Primary -> Sol 6.1 -> Terra/Sol 5.6 -> Astra).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

from dual_pipeline.parameters import get_hitl_ambiguity_margin_threshold

logger = logging.getLogger("dual_pipeline.escalation_shield")

W2_ALLOWED_PREFIXES = (
    "person:",
    "org:",
    "system:",
    "workstream:",
    "project:",
    "metric:",
    "role:",
    "policy:",
)


@dataclass(frozen=True)
class SchemaValidationResult:
    is_valid: bool
    errors: Tuple[str, ...]
    elapsed_ms: float


def _parse_schema_data(data: Union[str, Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not isinstance(data, str):
        if not isinstance(data, dict):
            return None, "ROOT_NOT_OBJECT: Expected top-level JSON object"
        return data, None
    import json
    try:
        parsed = json.loads(data)
        if not isinstance(parsed, dict):
            return None, "ROOT_NOT_OBJECT: Expected top-level JSON object"
        return parsed, None
    except Exception as exc:
        return None, f"INVALID_JSON: {exc}"


def _validate_entity_item(idx: int, ent: Any) -> List[str]:
    if not isinstance(ent, dict):
        return [f"INVALID_ENTITY_FORMAT: Entity at index {idx} is not an object"]
    ent_id = str(ent.get("id", ""))
    if not ent_id:
        return [f"MISSING_ENTITY_ID: Entity at index {idx} has no 'id'"]
    if not any(ent_id.startswith(p) for p in W2_ALLOWED_PREFIXES):
        return [
            f"W2_PREFIX_VIOLATION: Entity id '{ent_id}' does not start with a valid "
            f"W2 Semantic Spine prefix {W2_ALLOWED_PREFIXES}"
        ]
    return []


def _validate_relationship_item(idx: int, rel: Any) -> List[str]:
    if not isinstance(rel, dict):
        return [f"INVALID_RELATIONSHIP_FORMAT: Relationship at index {idx} is not an object"]
    errs = []
    for field in ("source", "target", "predicate"):
        if not rel.get(field):
            errs.append(f"MISSING_RELATIONSHIP_{field.upper()}: Relationship at index {idx} missing '{field}'")
    return errs


def validate_fast_schema_57(data: Union[str, Dict[str, Any]]) -> SchemaValidationResult:
    """
    Fast deterministic validator (<50ms) verifying conformance with Schema 5.7
    and W2 Semantic Spine entity prefixes.
    """
    start = time.perf_counter()
    parsed_dict, parse_err = _parse_schema_data(data)
    if parse_err or parsed_dict is None:
        return SchemaValidationResult(
            is_valid=False,
            errors=(parse_err or "ROOT_NOT_OBJECT",),
            elapsed_ms=(time.perf_counter() - start) * 1000.0,
        )

    errors: List[str] = [
        f"MISSING_REQUIRED_KEY: Missing '{k}' in Output 03 payload"
        for k in ("project", "entities")
        if k not in parsed_dict
    ]

    entities = parsed_dict.get("entities", [])
    if not isinstance(entities, list):
        errors.append("INVALID_ENTITIES_TYPE: 'entities' must be a list")
    else:
        for idx, ent in enumerate(entities):
            errors.extend(_validate_entity_item(idx, ent))

    rels = parsed_dict.get("relationships", parsed_dict.get("signals", parsed_dict.get("assertions", [])))
    if not isinstance(rels, list):
        errors.append("INVALID_RELATIONSHIPS_TYPE: Relationships must be a list")
    else:
        for idx, rel in enumerate(rels):
            errors.extend(_validate_relationship_item(idx, rel))

    elapsed = (time.perf_counter() - start) * 1000.0
    return SchemaValidationResult(
        is_valid=len(errors) == 0,
        errors=tuple(errors),
        elapsed_ms=round(elapsed, 3),
    )


@dataclass(frozen=True)
class AmbiguityShieldResult:
    primary_margin: float
    resolved_by: str  # "PRIMARY_AUTONOMOUS", "SOL_SHIELD_AUTONOMOUS", or "HITL_REQUIRED"
    final_assigned: str
    final_confidence: float
    final_margin: float
    hitl_required: bool
    audit_notes: str


def resolve_two_stage_ambiguity(
    candidate_name_1: str,
    confidence_1: float,
    candidate_name_2: str,
    confidence_2: float,
    secondary_evaluator_fn: Optional[Callable[[], Tuple[str, float, float]]] = None,
    threshold: Optional[float] = None,
) -> AmbiguityShieldResult:
    """
    Two-Stage Ambiguity Shield:
    When primary model (Luna) evaluates speaker attribution or entity identity and produces
    a narrow confidence margin (Delta < 15%), do NOT immediately interrupt the human operator.
    Automatically dispatch to secondary model (Sol 6.1).
    - If Sol resolves with Delta >= 15%, proceed autonomously under audit.
    - Only if Sol ALSO confirms Delta < 15% is an interactive HITL modal surfaced.
    """
    eff_threshold = threshold if threshold is not None else get_hitl_ambiguity_margin_threshold()
    margin_1 = round(confidence_1 - confidence_2, 4)

    # Stage 1: If primary model has decisive margin >= threshold, resolve autonomously
    if margin_1 >= eff_threshold:
        return AmbiguityShieldResult(
            primary_margin=margin_1,
            resolved_by="PRIMARY_AUTONOMOUS",
            final_assigned=candidate_name_1,
            final_confidence=confidence_1,
            final_margin=margin_1,
            hitl_required=False,
            audit_notes=f"Primary model decisively resolved with margin {margin_1:.2f} >= {eff_threshold:.2f}",
        )

    # Stage 2: Narrow margin detected (Delta < threshold). Engage Sol Ambiguity Shield.
    logger.info(
        "[AmbiguityShield] Narrow margin (%.2f < %.2f) detected. Dispatching to Sol 6.1.",
        margin_1,
        eff_threshold,
    )

    if secondary_evaluator_fn is not None:
        try:
            sol_candidate, sol_conf, sol_margin = secondary_evaluator_fn()
            if sol_margin >= eff_threshold:
                return AmbiguityShieldResult(
                    primary_margin=margin_1,
                    resolved_by="SOL_SHIELD_AUTONOMOUS",
                    final_assigned=sol_candidate,
                    final_confidence=sol_conf,
                    final_margin=sol_margin,
                    hitl_required=False,
                    audit_notes=(
                        f"Two-Stage Shield: Sol 6.1 resolved ambiguous primary margin ({margin_1:.2f}) "
                        f"with decisive margin ({sol_margin:.2f} >= {eff_threshold:.2f}). Operator prompt suppressed."
                    ),
                )
            else:
                return AmbiguityShieldResult(
                    primary_margin=margin_1,
                    resolved_by="HITL_REQUIRED",
                    final_assigned=sol_candidate,
                    final_confidence=sol_conf,
                    final_margin=sol_margin,
                    hitl_required=True,
                    audit_notes=(
                        f"Two-Stage Shield: Both primary ({margin_1:.2f}) and Sol ({sol_margin:.2f}) "
                        f"confirmed genuine epistemic ambiguity < {eff_threshold:.2f}. Operator modal required."
                    ),
                )
        except Exception as exc:
            logger.warning("[AmbiguityShield] Secondary Sol evaluation failed (%s); escalating to HITL", exc)

    # If no secondary evaluator provided or failed, fail-closed to HITL
    return AmbiguityShieldResult(
        primary_margin=margin_1,
        resolved_by="HITL_REQUIRED",
        final_assigned=candidate_name_1,
        final_confidence=confidence_1,
        final_margin=margin_1,
        hitl_required=True,
        audit_notes=f"Primary margin {margin_1:.2f} < {threshold:.2f}; secondary unexecuted, requiring HITL",
    )


def _resolve_fallback_chain(primary_model: str, fallback_chain: Optional[Sequence[str]]) -> List[str]:
    if fallback_chain is not None:
        return list(fallback_chain)

    from dual_pipeline.model_registry import ModelRegistry, ModelTier, canonicalize_model_name
    primary_clean = canonicalize_model_name(primary_model)
    tier_spec = None
    tiers = ModelRegistry._load_registry() if hasattr(ModelRegistry, "_load_registry") else {}
    for t_val in tiers.values():
        if canonicalize_model_name(t_val.get("default_model", "")) == primary_clean:
            tier_spec = t_val
            break

    chain = [primary_model]
    if tier_spec and tier_spec.get("allowed_fallbacks"):
        chain.extend(tier_spec["allowed_fallbacks"])
    else:
        spec = ModelRegistry.get_tier_spec(ModelTier.QUERY_REASONING)
        chain.extend([fb for fb in spec.get("allowed_fallbacks", []) if fb != primary_model])

    seen: set = set()
    return [m for m in chain if not (m in seen or seen.add(m))]


def _is_rate_limit(exc: Exception) -> bool:
    code = getattr(exc, "status_code", getattr(exc, "code", None))
    err_str = str(exc).lower()
    return code == 429 or "rate_limit" in err_str or "quota" in err_str


def _is_recoverable(exc: Exception, is_rate_limit: bool) -> bool:
    code = getattr(exc, "status_code", getattr(exc, "code", None))
    err_str = str(exc).lower()
    return (
        code in (429, 500, 502, 503, 504)
        or is_rate_limit
        or "timeout" in err_str
        or "connection" in err_str
    )


def _handle_attempt_failure(
    exc: Exception,
    model: str,
    attempt: int,
    max_retries: int,
    backoff_seconds: float,
) -> Dict[str, Any]:
    rate_limit = _is_rate_limit(exc)
    recoverable = _is_recoverable(exc, rate_limit)
    logger.warning(
        "[MultiGenCascade] Model %s failed (attempt %d/%d): %s. Recoverable: %s",
        model, attempt + 1, max_retries, exc, recoverable,
    )
    if not recoverable:
        raise exc

    if attempt < max_retries - 1 and rate_limit:
        import time
        time.sleep(backoff_seconds * (2 ** attempt))

    return {
        "model": model,
        "attempt": attempt + 1,
        "status": "FAILED",
        "error": str(exc),
        "recoverable": recoverable,
    }


def execute_with_multigen_fallback(
    call_fn: Callable[[str], Any],
    primary_model: str,
    fallback_chain: Optional[Sequence[str]] = None,
    max_retries_per_tier: int = 1,
    backoff_seconds: float = 0.5,
) -> Tuple[Any, str, List[Dict[str, Any]]]:
    """
    Multi-Generation SRE Fallback Cascade:
    Wraps API calls with automatic fallbacks:
    Primary (e.g. gpt-6-luna) -> Same-Tier Fallbacks -> Secondary.
    Recovers cleanly from HTTP 429 (rate-limit/quota), HTTP 503, or connection timeouts.
    Enforces anti-escalation hygiene: rate limits back off on the same model before escalating to higher tiers.
    """
    effective_chain = _resolve_fallback_chain(primary_model, fallback_chain)
    attempts_log: List[Dict[str, Any]] = []

    for model_idx, model in enumerate(effective_chain):
        for attempt in range(max_retries_per_tier):
            if model_idx > 0 or attempt > 0:
                from dual_pipeline.cost_tracker import get_token_tracker
                get_token_tracker().intercept_token_call(0)
            try:
                res = call_fn(model)
                attempts_log.append({
                    "model": model,
                    "attempt": attempt + 1,
                    "status": "SUCCESS",
                })
                return res, model, attempts_log
            except Exception as exc:
                from dual_pipeline.exceptions import PipelineTokenBudgetExhaustedError
                if isinstance(exc, PipelineTokenBudgetExhaustedError):
                    raise
                log_entry = _handle_attempt_failure(
                    exc, model, attempt, max_retries_per_tier, backoff_seconds
                )
                attempts_log.append(log_entry)

    raise RuntimeError(
        f"MULTIGEN_CASCADE_EXHAUSTED: All models in fallback chain {effective_chain} failed. "
        f"Attempts log: {attempts_log}"
    )
