"""Preserve evaluated attempts as complete artifact/report bundles during rollback."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Tuple

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.strategies.qa import QAEvaluationResult
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.release_policy import verify_decision_seal, verify_release_evaluation_digest

_OUTPUTS = ("output_1", "output_2", "output_3")


def verify_attempt_binding(
    synthesis: SynthesisResult, evaluation: QAEvaluationResult,
    source_text: str, modality: CanonicalModality,
    output3_only: bool = False,
) -> None:
    """Fail closed when a candidate, judge report, or release manifest is mismatched."""
    decision, judge = evaluation.final_decision, evaluation.judge_report
    f1 = source_text if modality == CanonicalModality.DOCUMENT or output3_only else synthesis.output_1 or ""
    valid, errors = verify_decision_seal(
        decision, f1_text=f1, f2_text=synthesis.output_2 or "", f3_text=synthesis.output_3 or "",
    )
    if not verify_release_evaluation_digest(judge):
        errors.append("Judge evaluation digest mismatch")
    if decision.get("artifact_manifest", {}).get("judge_evaluation_digest") != judge.get("evaluation_receipt_digest"):
        errors.append("Decision is bound to a different judge report")
    healed = evaluation.qa_report.get("healed_artifacts", {})
    for key in _OUTPUTS:
        if healed.get(key) and healed[key] != (getattr(synthesis, key) or ""):
            errors.append(f"QA report is bound to different {key} content")
    if not valid or errors:
        raise ValueError("Attempt artifact/report binding failed: " + "; ".join(errors))


def _applicable_outputs(modality: CanonicalModality, output3_only: bool = False) -> Tuple[str, ...]:
    if output3_only or modality == CanonicalModality.DOCUMENT:
        return ("output_3",)
    if modality == CanonicalModality.MEETING_NOTES:
        return ("output_2", "output_3")
    return _OUTPUTS


def _dominates(prior: QAEvaluationResult, current: QAEvaluationResult, outputs: Tuple[str, ...]) -> bool:
    """Only roll back when prior is strictly better or aggregate quality is higher without dropping passing gates."""
    if prior.authoritative_write_authorized and not current.authoritative_write_authorized:
        return True
    for key in outputs:
        old = prior.judge_report.get(f"{key}_evaluation", {}).get("composite_score") or 0
        new = current.judge_report.get(f"{key}_evaluation", {}).get("composite_score") or 0
        if old < new:
            return False
        for suffix in ("pass", "deterministic_gates_pass", "llm_score_pass"):
            field = f"{key}_{suffix}"
            if current.final_decision.get(field) is True and prior.final_decision.get(field) is not True:
                return False
    for field in ("cross_artifact_consistency_pass", "authoritative_write_authorized"):
        if current.final_decision.get(field) is True and prior.final_decision.get(field) is not True:
            return False

    prior_agg = prior.judge_report.get("aggregate_composite_score", 0) or 0
    curr_agg = current.judge_report.get("aggregate_composite_score", 0) or 0

    prior_better_count = 0
    curr_better_count = 0
    for key in outputs:
        old = prior.judge_report.get(f"{key}_evaluation", {}).get("composite_score", 0) or 0
        new = current.judge_report.get(f"{key}_evaluation", {}).get("composite_score", 0) or 0
        if old > new:
            prior_better_count += 1
        elif new > old:
            curr_better_count += 1

    return (prior_better_count > curr_better_count) or (prior_agg > curr_agg and prior_better_count >= curr_better_count)


@dataclass(frozen=True)
class _AttemptBundle:
    synthesis: SynthesisResult
    evaluation: QAEvaluationResult


class BoundAttemptLedger:
    """Own deep snapshots; never transplant a score or report onto different content."""

    def __init__(self, source_text: str, modality: CanonicalModality, output3_only: bool = False) -> None:
        self.source_text = source_text
        self.modality = modality
        self.output3_only = output3_only
        self._attempts: Dict[int, _AttemptBundle] = {}

    def record(self, attempt: int, synthesis: SynthesisResult, evaluation: QAEvaluationResult) -> None:
        verify_attempt_binding(synthesis, evaluation, self.source_text, self.modality, self.output3_only)
        self._attempts[attempt] = _AttemptBundle(deepcopy(synthesis), deepcopy(evaluation))

    def restore(
        self, attempt: int, synthesis: SynthesisResult, evaluation: QAEvaluationResult,
        state_machine: Any,
    ) -> Tuple[SynthesisResult, QAEvaluationResult]:
        outputs = _applicable_outputs(self.modality, self.output3_only)
        if not any(state_machine.has_regressed(key) for key in outputs):
            return synthesis, evaluation
        best_prior = None
        for prior_attempt in sorted(self._attempts):
            if prior_attempt >= attempt:
                continue
            prior = self._attempts[prior_attempt]
            if not _dominates(prior.evaluation, evaluation, outputs):
                continue
            if best_prior is None or _dominates(prior.evaluation, best_prior.evaluation, outputs):
                best_prior = prior

        if best_prior is not None:
            restored = deepcopy(best_prior)
            verify_attempt_binding(restored.synthesis, restored.evaluation, self.source_text, self.modality, self.output3_only)
            for key in outputs:
                if restored.evaluation.final_decision.get(f"{key}_pass") is True:
                    state_machine.freeze_artifact(key, getattr(restored.synthesis, key) or "")
                    state_machine.passing_stages.add(key)
                else:
                    state_machine.unfreeze_artifact(key)
            return restored.synthesis, restored.evaluation
        return synthesis, evaluation


UnifiedAttemptLedger = BoundAttemptLedger

