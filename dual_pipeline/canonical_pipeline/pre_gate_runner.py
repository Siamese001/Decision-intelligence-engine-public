"""
dual_pipeline/canonical_pipeline/pre_gate_runner.py

Helper module for running deterministic pre-gate screening within CanonicalAgenticPipeline.
Keeps pipeline.py complexity and file budgets strictly within repository invariants.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Union

from dual_pipeline.token_optimization.pre_gate_screener import (
    PreGateResult,
    PreGateValidationError,
    screen_pre_gate,
)


def run_pre_gate_check(
    meta: Dict[str, Any],
    metadata: Optional[Dict[str, Any]],
    source_text: str = "",
    input_path: Optional[Union[str, Path]] = None,
    modality: Optional[str] = None,
    tracker: Optional[Any] = None,
    stage: Optional[Any] = None,
) -> None:
    """Execute pre-gate screening and propagate results or raise validation errors."""
    if not meta.get("enforce_pre_gate", False):
        return

    peek = source_text
    if not peek and input_path and Path(input_path).exists():
        peek = Path(input_path).read_text(encoding="utf-8", errors="replace")[:2000]

    res = screen_pre_gate(
        metadata=meta,
        source_text=peek or meta.get("source_text") or "placeholder_content",
        modality=modality or meta.get("modality"),
        strict=meta.get("strict_pre_gate", False),
    )
    res_dict = res.to_dict()
    meta["pre_gate_screener"] = res_dict
    if isinstance(metadata, dict):
        metadata["pre_gate_screener"] = res_dict

    if not res.is_valid:
        if tracker and stage:
            tracker.complete_stage(
                stage,
                status="FAIL",
                details={
                    "pre_gate_errors": res.errors,
                    "tokens_saved": res.tokens_saved_estimate,
                },
            )
        raise PreGateValidationError(f"Pre-gate validation failed: {'; '.join(res.errors)}")
