"""
judge.py — Dialectical LLM Judge & Ambiguity Gating Engine.

Enforces:
1. 20% Ambiguity Margin Gate: Only surfaces HITL decision when margin between top
   two options Delta = c_top - c_second < 20% (0.20).
2. Autonomous Progression: When Delta >= 20%, top option is decisively qualified and
   proceeds autonomously under an immutable audit receipt.
3. Atomic Presentation: One decision at a time, mutually exclusive options,
   recommended option marked '(Recommended)', and calibrated confidence level assigned.
4. Bayesian Prior Calibration: Updates priors via HITLDecisionStore (+0.15 on affirmed,
   -0.10 on rejected) to eliminate repetitive operator prompts.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from dual_pipeline.hitl_governance import (
    DEFAULT_CONFIDENCE_FLOOR,
    DEFAULT_MARGIN_THRESHOLD,
    HITLDecisionStore,
    evaluate_hitl_surfacing_gate,
)

logger = logging.getLogger(__name__)


def utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DialecticalOption:
    """A structured candidate option in a dialectical decision."""
    option_id: str
    label: str
    raw_confidence: float
    calibrated_confidence: float = 0.0
    confidence_level: str = "MEDIUM"
    rationale: str = ""
    is_recommended: bool = False
    evidence_refs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DialecticalEvaluationResult:
    """Evaluation result for dialectical perspectives under 20% ambiguity margin gating."""
    decision_id: str
    question: str
    status: str  # "AUTONOMOUS_RESOLUTION" | "HITL_REQUIRED"
    should_surface: bool
    confidence_margin_delta: float
    margin_threshold: float
    top_option: Optional[Dict[str, Any]]
    runner_up_option: Optional[Dict[str, Any]]
    options: List[Dict[str, Any]]
    reason: str
    evaluated_at: str = field(default_factory=utc_iso_now)
    audit_receipt_digest: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DialecticalJudge:
    """
    Dialectical LLM Judge evaluating competing perspectives with unambiguous
    confidence scoring margins (>= 20%) and atomic presentation governance.
    """

    def __init__(
        self,
        margin_threshold: float = DEFAULT_MARGIN_THRESHOLD,
        confidence_floor: float = DEFAULT_CONFIDENCE_FLOOR,
        ledger_path: Optional[Path | str] = None,
    ) -> None:
        self.margin_threshold = margin_threshold
        self.confidence_floor = confidence_floor
        self.store = HITLDecisionStore(ledger_path=ledger_path)

    def calibrate_options(
        self,
        options: Sequence[Mapping[str, Any] | DialecticalOption],
        category: str = "dialectical_evaluation",
    ) -> List[Dict[str, Any]]:
        """Calibrates option confidence scores against historical priors in HITL store."""
        raw_options: List[Dict[str, Any]] = []
        for opt in options:
            opt_dict = opt.to_dict() if isinstance(opt, DialecticalOption) else dict(opt)
            if "key" not in opt_dict:
                opt_dict["key"] = opt_dict.get("option_id") or opt_dict.get("label", "")
            raw_options.append(opt_dict)
        return self.store.calibrate_options(raw_options, category=category)

    def evaluate_perspectives(
        self,
        decision_id: str,
        question: str,
        options: Sequence[Mapping[str, Any] | DialecticalOption],
        category: str = "dialectical_evaluation",
        context: Optional[Mapping[str, Any]] = None,
    ) -> DialecticalEvaluationResult:
        """
        Evaluates dialectical options, enforcing the 20% margin ambiguity gate.
        Delta = c_top - c_second >= 0.20 -> Autonomous progression.
        Delta < 0.20 -> Surface atomic HITL decision card.
        """
        calibrated = self.calibrate_options(options, category=category)
        gate_res = evaluate_hitl_surfacing_gate(
            options=calibrated,
            margin_threshold=self.margin_threshold,
            min_confidence_floor=self.confidence_floor,
            context=context,
        )

        should_surface = bool(gate_res.get("should_surface", False))
        margin_delta = float(gate_res.get("margin", 0.0))
        top_opt = gate_res.get("top_option")
        runner_up = gate_res.get("runner_up_option")
        reason = str(gate_res.get("reason", ""))
        status = "HITL_REQUIRED" if should_surface else "AUTONOMOUS_RESOLUTION"

        # Format options atomically: mark recommended option
        formatted_options: List[Dict[str, Any]] = []
        sorted_opts = sorted(
            calibrated,
            key=lambda o: float(o.get("confidence_score", 0.0)),
            reverse=True,
        )
        for i, opt in enumerate(sorted_opts):
            is_rec = (i == 0) and not should_surface
            label = str(opt.get("label") or opt.get("title") or opt.get("option_id") or "")
            if is_rec and not label.startswith("(Recommended)"):
                label = f"(Recommended) {label}"
            f_opt = dict(opt)
            f_opt["label"] = label
            f_opt["is_recommended"] = is_rec
            formatted_options.append(f_opt)

        import hashlib
        import json
        raw_receipt = json.dumps(
            {
                "decision_id": decision_id,
                "status": status,
                "margin_delta": margin_delta,
                "threshold": self.margin_threshold,
                "evaluated_at": utc_iso_now(),
            },
            sort_keys=True,
        )
        digest = hashlib.sha256(raw_receipt.encode("utf-8")).hexdigest()

        return DialecticalEvaluationResult(
            decision_id=decision_id,
            question=question,
            status=status,
            should_surface=should_surface,
            confidence_margin_delta=margin_delta,
            margin_threshold=self.margin_threshold,
            top_option=top_opt,
            runner_up_option=runner_up,
            options=formatted_options,
            reason=reason,
            audit_receipt_digest=digest,
        )


def evaluate_dialectical_decision(
    decision_id: str,
    question: str,
    options: Sequence[Mapping[str, Any]],
    margin_threshold: float = DEFAULT_MARGIN_THRESHOLD,
    ledger_path: Optional[Path | str] = None,
) -> DialecticalEvaluationResult:
    """Convenience functional interface for 20% margin dialectical decision evaluation."""
    judge = DialecticalJudge(margin_threshold=margin_threshold, ledger_path=ledger_path)
    return judge.evaluate_perspectives(decision_id, question, options)


__all__ = [
    "DialecticalJudge",
    "DialecticalOption",
    "DialecticalEvaluationResult",
    "evaluate_dialectical_decision",
]
