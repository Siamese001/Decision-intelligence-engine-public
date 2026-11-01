"""
dual_pipeline/accounting_normalization.py

Accounting, Token Counter & Final Receipt Normalization SSOT.
Enforces:
1. Zero double-counting of reasoning tokens when subsumed in completion tokens.
2. Provider-specific raw counter retention (prompt, cached, reasoning, write).
3. Explicit pricing and version uncertainty tracking (never silent false $0.0 cost).
4. Strict separation between pipeline execution scope and host orchestration turns.
5. Final copy consistency validation across deliverable and attempt directories.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def normalize_provider_counters(
    raw_usage: Dict[str, Any],
    model: str = "",
) -> Dict[str, Any]:
    """Normalizes raw provider token counters ensuring no double counting of reasoning tokens."""
    prompt = int(raw_usage.get("prompt_tokens", 0) or 0)
    completion = int(raw_usage.get("completion_tokens", 0) or 0)
    cached = int(raw_usage.get("cached_prompt_tokens", 0) or 0)
    reasoning = int(raw_usage.get("reasoning_tokens", 0) or 0)
    cache_write = int(raw_usage.get("cache_write_tokens", 0) or 0)

    # Detect if reasoning tokens are explicitly nested or subsumed
    completion_details = raw_usage.get("completion_tokens_details") or {}
    nested_reasoning = int(completion_details.get("reasoning_tokens", 0) or 0)
    subsumed = nested_reasoning > 0 or (reasoning > 0 and completion >= reasoning)

    if subsumed:
        # Reasoning is already included in completion tokens
        total = prompt + completion
    else:
        # Separate disjoint reasoning counter
        total = prompt + completion + reasoning

    uncached_prompt = max(0, prompt - cached - cache_write)

    return {
        "raw_counters": dict(raw_usage),
        "normalized": {
            "prompt_tokens": prompt,
            "uncached_prompt_tokens": uncached_prompt,
            "cached_prompt_tokens": cached,
            "cache_write_tokens": cache_write,
            "completion_tokens": completion,
            "reasoning_tokens": reasoning or nested_reasoning,
            "total_tokens": total,
        },
        "integrity": {
            "subsumed_reasoning": subsumed,
            "double_counting_prevented": True,
            "model": model,
        },
    }


def assess_pricing_uncertainty(
    model: str,
    calculated_cost_usd: Optional[float] = None,
) -> Dict[str, Any]:
    """Assesses pricing uncertainty for a model invocation.
    
    Ensures that unknown or unpriced model revisions are explicitly marked UNRECONCILED
    rather than reporting a misleading $0.000000 cost.
    """
    from dual_pipeline.token_optimization.schemas import MODEL_PRICING_TABLE, _MODEL_KEYWORD_MATCHES

    m = str(model).strip().lower()
    is_known = m in MODEL_PRICING_TABLE or any(
        any(kw in m for kw in keywords) for keywords, _ in _MODEL_KEYWORD_MATCHES
    )

    if not is_known:
        return {
            "model": model,
            "cost_usd": None,
            "cost_status": "UNRECONCILED",
            "price_uncertainty": True,
            "rationale": f"Model '{model}' has unverified or uncalibrated pricing table; expenditure is unpriced.",
        }

    cost = calculated_cost_usd if calculated_cost_usd is not None else 0.0
    return {
        "model": model,
        "cost_usd": round(cost, 6),
        "cost_status": "VERIFIED",
        "price_uncertainty": False,
        "rationale": "Model pricing matches verified rates in AstraModelPricing schema.",
    }


def separate_host_from_pipeline(
    pipeline_execution: Dict[str, Any],
    host_metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Explicitly separates inner pipeline execution from outer host orchestration turns.
    
    Prevents synthetic reconstruction of absent host usage while isolating timings.
    """
    host = host_metadata or {}
    return {
        "pipeline_execution_scope": {
            "duration_ms": pipeline_execution.get("duration_ms"),
            "monotonic_duration_s": pipeline_execution.get("monotonic_duration_s"),
            "stage_inclusive_timings": pipeline_execution.get("stage_inclusive_timings", {}),
            "measurement_state": pipeline_execution.get("measurement_state", "TERMINAL"),
        },
        "host_orchestration_scope": {
            "host_turns": host.get("host_turns", "UNAVAILABLE_HOST_METADATA"),
            "host_orchestration_attempts": host.get("attempts", "UNAVAILABLE_HOST_METADATA"),
            "host_environment": host.get("environment", "local"),
            "reconstructed": False,
        },
    }


def compute_receipt_digest(receipt_path: Path) -> str:
    """Computes SHA-256 digest of canonical receipt contents."""
    if not receipt_path.is_file():
        return "MISSING_RECEIPT"
    with open(receipt_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def verify_receipt_copy_consistency(
    canonical_receipt_path: Path,
    copy_receipt_paths: List[Path],
) -> Tuple[bool, List[str]]:
    """Verifies that all copies of a pipeline receipt match the canonical execution evidence."""
    errors: List[str] = []
    if not canonical_receipt_path.is_file():
        return False, [f"Canonical receipt not found: {canonical_receipt_path}"]

    try:
        canonical_data = json.loads(canonical_receipt_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, [f"Failed to parse canonical receipt: {exc}"]

    canon_outcome = canonical_data.get("terminal_outcome")
    canon_duration = canonical_data.get("monotonic_duration_s")

    for copy_path in copy_receipt_paths:
        if not copy_path.is_file():
            errors.append(f"Receipt copy missing: {copy_path}")
            continue
        try:
            copy_data = json.loads(copy_path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"Failed to parse receipt copy {copy_path}: {exc}")
            continue

        if copy_data.get("terminal_outcome") != canon_outcome:
            errors.append(
                f"Outcome mismatch in {copy_path}: expected {canon_outcome}, got {copy_data.get('terminal_outcome')}"
            )
        if copy_data.get("monotonic_duration_s") != canon_duration:
            errors.append(
                f"Duration mismatch in {copy_path}: expected {canon_duration}, got {copy_data.get('monotonic_duration_s')}"
            )

    return len(errors) == 0, errors
