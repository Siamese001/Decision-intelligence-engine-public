"""
dual_pipeline/system_learning/holdout_replay_evaluator.py

Holdout Replay Evaluator for candidate learning change proposals.
Evaluates proposed alias, prompt, routing, or quarantine adjustments against
historical holdout cases with ground truth labels to ensure accuracy gains
without false merge or false positive regressions.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from dual_pipeline.contract_validation import canonical_json_bytes
from dual_pipeline.core import stable_id

logger = logging.getLogger("dual_pipeline.system_learning.holdout_replay")


@dataclass(frozen=True)
class HoldoutCase:
    """Historical test case with known ground-truth outcome."""

    case_id: str
    category: str
    input_text: str
    raw_label: str
    ground_truth_label: str
    negative_labels: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def unlabeled_view(self) -> Dict[str, str]:
        """Provides an unlabeled input view preventing access to ground-truth labels during prediction."""
        return {
            "case_id": self.case_id,
            "category": self.category,
            "input_text": self.input_text,
            "raw_label": self.raw_label,
        }


@dataclass(frozen=True)
class HoldoutEvaluationReport:
    """Evaluation receipt documenting holdout replay results."""

    report_id: str
    proposal_id: str
    category: str
    total_cases: int
    accuracy_before: float
    accuracy_after: float
    accuracy_delta: float
    false_merge_rate_before: float
    false_merge_rate_after: float
    false_merge_regression: bool
    verdict: str
    rejection_reasons: List[str]
    evaluated_at: str
    dataset_digest: str = ""
    proposal_digest: str = ""
    evaluator_version: str = "1.0.0"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _resolve_candidate_label(input_text: str, raw_label: str, from_id: Optional[str], to_id: Optional[str]) -> str:
    """Applies candidate alias mapping strictly on unlabeled inputs without access to ground truth."""
    if not (from_id and to_id):
        return raw_label
    if raw_label == from_id or input_text == from_id or from_id.lower() in input_text.lower():
        return to_id
    return raw_label


def _evaluate_baseline(cases: Sequence[HoldoutCase]) -> Tuple[float, float, int]:
    """Computes baseline accuracy and false merge rate."""
    correct = 0
    false_merges = 0
    total_negatives = 0
    for c in cases:
        if c.raw_label == c.ground_truth_label:
            correct += 1
        for neg in c.negative_labels:
            total_negatives += 1
            if c.raw_label == neg:
                false_merges += 1
    acc = round(correct / len(cases), 4)
    fm = round(false_merges / max(1, total_negatives), 4)
    return acc, fm, total_negatives


def _evaluate_simulated(
    cases: Sequence[HoldoutCase],
    from_id: Optional[str],
    to_id: Optional[str],
    total_negatives: int,
) -> Tuple[float, float]:
    """Computes simulated accuracy and false merge rate under candidate proposal.
    Prediction strictly executes against unlabeled input views without ground truth access.
    """
    correct = 0
    false_merges = 0
    for c in cases:
        view = c.unlabeled_view
        resolved = _resolve_candidate_label(view["input_text"], view["raw_label"], from_id, to_id)
        if resolved == c.ground_truth_label:
            correct += 1
        for neg in c.negative_labels:
            if resolved == neg:
                false_merges += 1
    acc = round(correct / len(cases), 4)
    fm = round(false_merges / max(1, total_negatives), 4)
    return acc, fm


class HoldoutReplayEvaluator:
    """Evaluates candidate learning proposals against historical holdout cases."""

    def __init__(self, holdout_cases: Optional[Sequence[HoldoutCase]] = None) -> None:
        self.holdout_cases = list(holdout_cases or [])

    def add_case(self, case: HoldoutCase) -> None:
        self.holdout_cases.append(case)

    def evaluate_proposal(
        self,
        proposal: Mapping[str, Any],
        holdout_cases: Optional[Sequence[HoldoutCase]] = None,
    ) -> HoldoutEvaluationReport:
        """Evaluates candidate proposal against holdout cases."""
        cases = list(holdout_cases or self.holdout_cases)
        p_id = str(proposal.get("proposal_id", "unknown_proposal"))
        ptype = str(proposal.get("proposal_type", "unknown"))
        ext = proposal.get("extensions", {}).get("com.partner_advisory.nee.learning", {})
        category = ext.get("category") or ext.get("target_type") or ptype
        now = datetime.now(timezone.utc).isoformat()

        raw_digest = proposal.get("canonical_digest", {}).get("value")
        if not raw_digest:
            prop_bytes = canonical_json_bytes(dict(proposal))
            proposal_digest = hashlib.sha256(prop_bytes).hexdigest()
        else:
            proposal_digest = str(raw_digest)

        cases_dicts = [c.to_dict() for c in cases]
        dataset_digest = hashlib.sha256(canonical_json_bytes(cases_dicts)).hexdigest() if cases else ""

        if not cases:
            return HoldoutEvaluationReport(
                report_id=stable_id("rpt-holdout", p_id, length=24),
                proposal_id=p_id,
                category=category,
                total_cases=0,
                accuracy_before=0.0,
                accuracy_after=0.0,
                accuracy_delta=0.0,
                false_merge_rate_before=0.0,
                false_merge_rate_after=0.0,
                false_merge_regression=False,
                verdict="REJECT",
                rejection_reasons=["No holdout cases provided for evaluation."],
                evaluated_at=now,
                dataset_digest="",
                proposal_digest=proposal_digest,
            )

        acc_before, fm_before, total_neg = _evaluate_baseline(cases)
        from_id = ext.get("from_id")
        to_id = ext.get("to_id")
        acc_after, fm_after = _evaluate_simulated(cases, from_id, to_id, total_neg)

        acc_delta = round(acc_after - acc_before, 4)
        fm_regression = fm_after > fm_before

        rejection_reasons: List[str] = []
        if fm_regression:
            rejection_reasons.append(
                f"False merge rate regressed from {fm_before:.4f} to {fm_after:.4f}."
            )
        if acc_delta <= 0:
            rejection_reasons.append(
                f"Holdout accuracy delta {acc_delta:.4f} <= 0 did not improve baseline ({acc_before:.4f} -> {acc_after:.4f})."
            )

        verdict = "PASS" if not rejection_reasons else "REJECT"

        return HoldoutEvaluationReport(
            report_id=stable_id("rpt-holdout", p_id, length=24),
            proposal_id=p_id,
            category=category,
            total_cases=len(cases),
            accuracy_before=acc_before,
            accuracy_after=acc_after,
            accuracy_delta=acc_delta,
            false_merge_rate_before=fm_before,
            false_merge_rate_after=fm_after,
            false_merge_regression=fm_regression,
            verdict=verdict,
            rejection_reasons=rejection_reasons,
            evaluated_at=now,
            dataset_digest=dataset_digest,
            proposal_digest=proposal_digest,
        )

    def evaluate_audio_voice_proposal(
        self,
        proposal: Mapping[str, Any],
        holdout_cases: Optional[Sequence[Mapping[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Evaluates audio voice recognition proposal against acoustic/attribution holdout cases."""
        p_id = str(proposal.get("proposal_id", "unknown_proposal"))
        ext = proposal.get("extensions", {}).get("com.partner_advisory.nee.learning", {})
        cases = list(holdout_cases if holdout_cases is not None else (ext.get("holdout_cases") or []))
        proposed_val = float(ext.get("proposed_value", 0.80))
        metrics = _tally_audio_holdout_cases(cases, proposed_val, ext)
        reasons = _build_audio_holdout_reasons(cases, metrics, ext)
        vetoed = len(reasons) > 0
        return {
            "report_id": stable_id("rpt-audio-holdout", p_id, length=24),
            "proposal_id": p_id,
            "verdict": "VETOED" if vetoed else "PASS",
            "vetoed": vetoed,
            **metrics,
            "rejection_reasons": reasons,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        }


def _tally_audio_holdout_cases(
    cases: Sequence[Mapping[str, Any]], proposed_val: float, ext: Mapping[str, Any]
) -> Dict[str, Any]:
    reg_cnt = false_enr = unv_leak = corr_before = corr_after = total_neg = 0
    for c in cases:
        sim = float(c.get("cosine_similarity", 0.85))
        base = float(c.get("baseline_threshold", 0.82))
        unv_leak += int(bool(c.get("leaks_unverified_to_graph", False)))
        if bool(c.get("is_true_speaker", True)):
            b_ok, a_ok = sim >= base, sim >= proposed_val
            corr_before += int(b_ok)
            corr_after += int(a_ok)
            reg_cnt += int(b_ok and not a_ok)
        else:
            total_neg += 1
            false_enr += int(sim >= proposed_val)
    n_pos = max(1, len(cases) - total_neg)
    acc_before = round(corr_before / n_pos, 4) if cases else 0.0
    acc_after = round(corr_after / n_pos, 4) if cases else 0.0
    fe_rate = round(false_enr / max(1, total_neg), 4) if total_neg > 0 else float(ext.get("false_enrollment_rate", 0.0))
    return {
        "regression_count": reg_cnt + int(ext.get("regression_count", 0)),
        "false_enrollment_rate": fe_rate,
        "unverified_graph_leakage": unv_leak + int(ext.get("unverified_graph_leakage", 0)),
        "accuracy_before": acc_before,
        "accuracy_after": acc_after,
        "attribution_accuracy_delta": round(acc_after - acc_before, 4),
    }


def _build_audio_holdout_reasons(
    cases: Sequence[Mapping[str, Any]], metrics: Mapping[str, Any], ext: Mapping[str, Any]
) -> List[str]:
    reasons: List[str] = []
    if not cases:
        reasons.append("No acoustic holdout cases provided.")
    if metrics["regression_count"] > 0:
        reasons.append(f"Attribution regression detected ({metrics['regression_count']} case(s)).")
    if metrics["false_enrollment_rate"] > 0.0:
        reasons.append(f"Non-zero false enrollment rate ({metrics['false_enrollment_rate']:.4f} > 0.0).")
    if metrics["unverified_graph_leakage"] > 0:
        reasons.append(f"Unverified speaker graph leakage detected ({metrics['unverified_graph_leakage']}).")
    if metrics["attribution_accuracy_delta"] <= 0.0 and float(ext.get("der_delta", 0.0)) >= 0.0:
        reasons.append(f"Non-positive attribution accuracy delta ({metrics['attribution_accuracy_delta']:.4f}).")
    return reasons
