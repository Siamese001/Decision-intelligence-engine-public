"""
dual_pipeline/canonical_pipeline/quality_aggregates.py

Quality aggregate calculations and score extraction for multi-modality judge evaluations.
Guarantees that SKIPPED or NOT_APPLICABLE deliverable stubs are strictly excluded from
aggregate calculations and never artificially raise or distort average scores.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def is_deliverable_skipped_or_not_applicable(eval_card: Optional[Dict[str, Any]]) -> bool:
    """Determine whether an evaluation card represents a skipped or not-applicable deliverable."""
    if not isinstance(eval_card, dict):
        return True
    if eval_card.get("status") in ("SKIPPED", "NOT_APPLICABLE"):
        return True
    reason = str(eval_card.get("reason", ""))
    if "NOT_APPLICABLE" in reason or "SKIPPED" in reason:
        return True
    return False


def get_evaluated_composite_scores(judge_report: Dict[str, Any]) -> Dict[str, float]:
    """
    Extract composite scores only for actually evaluated deliverables.
    Skipped or not-applicable deliverables are excluded.
    Invalid or malformed scorecards are rejected and excluded.
    """
    scores: Dict[str, float] = {}
    stage_map = {
        "output_1": ("output_1_evaluation", 1),
        "output_2": ("output_2_evaluation", 2),
        "output_3": ("output_3_evaluation", 3),
    }
    from dual_pipeline.release_policy import validate_micro_judge_scorecard
    for out_key, (eval_key, out_num) in stage_map.items():
        card = judge_report.get(eval_key)
        if not isinstance(card, dict):
            continue
        if is_deliverable_skipped_or_not_applicable(card):
            continue
        if card.get("is_valid_scorecard") is False:
            continue
        is_valid, _ = validate_micro_judge_scorecard(card, output_num=out_num)
        if not is_valid:
            continue
        sc = card.get("composite_score")
        if sc is not None and isinstance(sc, (int, float)) and not isinstance(sc, bool):
            scores[out_key] = float(sc)
    return scores


def compute_quality_aggregate_score(judge_report: Dict[str, Any]) -> Optional[float]:
    """
    Computes the arithmetic mean composite score across all evaluated deliverables.
    Returns None if no deliverables were evaluated.
    Skipped and not-applicable deliverables never contribute to or raise the average.
    """
    scores = get_evaluated_composite_scores(judge_report)
    if not scores:
        return None
    return sum(scores.values()) / len(scores)


class ModalityQualificationReport:
    """Encapsulates modality-specific benchmark qualification and outcome accounting."""

    def __init__(
        self,
        modality: Any,
        benchmark_id: str,
        sample_count: int,
        passed_count: int,
        failed_count: int = 0,
        timeout_count: int = 0,
        inconclusive_count: int = 0,
        token_savings_pct: float = 0.0,
        quality_score: float = 0.0,
    ) -> None:
        from dual_pipeline.canonical_pipeline.operation_contracts import resolve_canonical_modality
        self.modality = resolve_canonical_modality(modality)
        self.benchmark_id = benchmark_id
        self.sample_count = sample_count
        self.passed_count = passed_count
        self.failed_count = failed_count
        self.timeout_count = timeout_count
        self.inconclusive_count = inconclusive_count
        self.token_savings_pct = token_savings_pct
        self.quality_score = quality_score

    @property
    def is_qualified(self) -> bool:
        if self.sample_count <= 0:
            return False
        if self.failed_count > 0 or self.timeout_count > 0 or self.inconclusive_count > 0:
            return False
        return self.passed_count == self.sample_count and self.quality_score >= 0.85


def validate_modality_qualification_isolation(
    reports: Dict[Any, ModalityQualificationReport],
    target_modality: Any,
) -> Tuple[bool, str]:
    """Ensures a modality is qualified only by its own independent benchmark and not by proxy."""
    from dual_pipeline.canonical_pipeline.operation_contracts import resolve_canonical_modality
    target_c = resolve_canonical_modality(target_modality)

    if target_c not in reports:
        return False, f"Missing qualification report for target modality: {target_c.name}"

    target_rep = reports[target_c]
    if not target_rep.is_qualified:
        return (
            False,
            f"Target modality {target_c.name} is not qualified: "
            f"(passed={target_rep.passed_count}/{target_rep.sample_count}, "
            f"failed={target_rep.failed_count}, timeouts={target_rep.timeout_count}, "
            f"inconclusive={target_rep.inconclusive_count}, score={target_rep.quality_score:.2f})",
        )

    return True, f"Modality {target_c.name} independently qualified"


def assert_savings_metrics_modality_scoped(
    reported_metrics: Dict[str, Any],
    modality: Any,
) -> None:
    """Enforces that token/cost savings metrics remain strictly modality-scoped and never claim global parity."""
    from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
    from dual_pipeline.canonical_pipeline.operation_contracts import resolve_canonical_modality
    c_mod = resolve_canonical_modality(modality)

    for key in reported_metrics.keys():
        kl = str(key).lower()
        if "global" in kl or "host_savings" in kl or "universal_savings" in kl:
            raise ValueError(
                f"Savings metric '{key}' improperly claims global/host scope; "
                f"savings must remain strictly scoped to {c_mod.name}"
            )
        if c_mod != CanonicalModality.MEETING_TRANSCRIPT and "transcript" in kl:
            raise ValueError(
                f"Modality {c_mod.name} savings cannot claim transcript parity or transcript savings: '{key}'"
            )


def verify_rollback_safety(
    flags: Dict[str, Any],
    deliverables: Dict[str, Any],
    requires_approval: bool = True,
) -> Tuple[bool, str]:
    """Ensures disabling optimization features or rolling back maintains artifact integrity and approval gates."""
    from pathlib import Path
    for k, v in deliverables.items():
        p = Path(v)
        if not p.exists():
            return False, f"ROLLBACK_ARTIFACT_LOST: Deliverable '{k}' ({p}) missing after rollback"

    if not requires_approval:
        return False, "ROLLBACK_GATE_BYPASS: Rollback or feature-off cannot bypass mutation approval"

    return True, "Rollback verified safe: deliverables preserved and approvals enforced"

