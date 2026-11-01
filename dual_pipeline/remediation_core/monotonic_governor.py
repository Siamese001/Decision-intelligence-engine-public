"""
dual_pipeline/remediation_core/monotonic_governor.py

Monotonic Score Governor & Best Candidate Ledger.
Enforces the Monotonic Score Progression Invariant (Score_{N+1} >= Score_N).
Guarantees:
1. Criterion-vector eligibility: no dimension or hard-gate decline permitted,
   even if overall composite score increases (e.g. [92,84,95] -> [91,88,95] is rejected).
2. Honest raw history: all raw attempts (including lower scores) are preserved.
3. Epoch invalidation: new evidence, models, or policy changes trigger a visible new epoch.
4. Atomic full-bundle rollback: restores content, full scorecard, and QA together.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class CandidateRecord:
    """Immutable metadata and content record for an artifact generated during an attempt."""

    artifact_key: str  # "output_1", "output_2", "output_3"
    attempt: int
    content: str
    composite_score: int
    verdict: str = "FAIL"
    dimension_scores: Dict[str, Any] = field(default_factory=dict)
    qa_passed: bool = True
    deterministic_gates_pass: bool = True
    regressed: bool = False
    prior_score: Optional[int] = None
    full_scorecard: Dict[str, Any] = field(default_factory=dict)
    epoch_id: str = "default_epoch"
    rejection_reason: Optional[str] = None

    def summary(self) -> Dict[str, Any]:
        return {
            "artifact_key": self.artifact_key,
            "attempt": self.attempt,
            "composite_score": self.composite_score,
            "verdict": self.verdict,
            "qa_passed": self.qa_passed,
            "deterministic_gates_pass": self.deterministic_gates_pass,
            "regressed": self.regressed,
            "prior_score": self.prior_score,
            "epoch_id": self.epoch_id,
            "rejection_reason": self.rejection_reason,
            "content_bytes": len(self.content.encode("utf-8")),
        }


def _check_hard_gates(current_best: CandidateRecord, new_record: CandidateRecord) -> Optional[str]:
    """Checks deterministic gates and QA verification status."""
    if current_best.deterministic_gates_pass and not new_record.deterministic_gates_pass:
        return "HARD_GATE_DECLINE: Candidate failed deterministic QA gates previously passing"
    if current_best.qa_passed and not new_record.qa_passed:
        return "QA_GATE_DECLINE: Candidate failed QA verification previously passing"
    return None


def _check_dimension_regression(current_best: CandidateRecord, new_record: CandidateRecord) -> Optional[str]:
    """Ensures no individual numeric dimension score declined."""
    for dim, old_val in current_best.dimension_scores.items():
        if isinstance(old_val, (int, float)):
            new_val = new_record.dimension_scores.get(dim)
            if isinstance(new_val, (int, float)) and new_val < old_val:
                return f"CRITERION_DECLINE: Dimension '{dim}' declined from {old_val} to {new_val}"
    return None


def _check_score_progression(
    current_best: CandidateRecord, new_record: CandidateRecord
) -> Tuple[bool, Optional[str]]:
    """Checks composite score decline and unchanged sampling conditions."""
    if new_record.composite_score < current_best.composite_score:
        is_authorized = (
            new_record.verdict == "PASS_AUTHORIZED_EXCEPTION"
            or bool(new_record.full_scorecard.get("authorized_exception"))
        )
        if not is_authorized:
            return (
                False,
                f"SCORE_DECLINE: Composite score declined from {current_best.composite_score} to {new_record.composite_score}",
            )

    if new_record.composite_score == current_best.composite_score and new_record.content == current_best.content:
        passing = ("PASS", "PASS_AUTHORIZED_EXCEPTION")
        if current_best.verdict in passing and new_record.verdict in passing:
            return True, None
        return False, "UNCHANGED_SAMPLING: Candidate text is identical to current best at equal score"

    return True, None


class BestCandidateLedger:
    """
    Tracks and preserves the highest-scoring candidate artifacts across retry attempts.
    Enforces monotonic non-decreasing score progression (Score_{N+1} >= Score_N).
    If a subsequent attempt regresses or suffers a criterion decline, the ledger
    intercepts the regression, preserves raw history, and protects the baseline.
    """

    def __init__(self, current_epoch: str = "default_epoch") -> None:
        self.current_epoch = current_epoch
        self._best: Dict[str, CandidateRecord] = {}
        self._history: Dict[str, List[CandidateRecord]] = {
            "output_1": [],
            "output_2": [],
            "output_3": [],
        }

    def check_candidate_eligibility(
        self, new_record: CandidateRecord, current_best: CandidateRecord
    ) -> Tuple[bool, str]:
        """Verifies that a new candidate dominates or equals current best across all criteria."""
        gate_err = _check_hard_gates(current_best, new_record)
        if gate_err:
            return False, gate_err

        dim_err = _check_dimension_regression(current_best, new_record)
        if dim_err:
            return False, dim_err

        eligible, score_err = _check_score_progression(current_best, new_record)
        if not eligible and score_err:
            return False, score_err

        return True, "ELIGIBLE"

    def record_candidate(
        self,
        artifact_key: str,
        attempt: int,
        content: str,
        composite_score: int,
        verdict: str = "FAIL",
        dimension_scores: Optional[Dict[str, Any]] = None,
        qa_passed: bool = True,
        deterministic_gates_pass: bool = True,
        full_scorecard: Optional[Dict[str, Any]] = None,
        epoch_id: Optional[str] = None,
    ) -> CandidateRecord:
        """Registers a candidate artifact from an attempt and updates the accepted high-water mark."""
        eff_epoch = epoch_id or self.current_epoch
        history = self._history.setdefault(artifact_key, [])
        prior_score: Optional[int] = history[-1].composite_score if history else None

        record = CandidateRecord(
            artifact_key=artifact_key,
            attempt=attempt,
            content=content,
            composite_score=composite_score,
            verdict=verdict,
            dimension_scores=dimension_scores or {},
            qa_passed=qa_passed,
            deterministic_gates_pass=deterministic_gates_pass,
            regressed=False,
            prior_score=prior_score,
            full_scorecard=full_scorecard or {},
            epoch_id=eff_epoch,
        )

        current_best = self._best.get(artifact_key)
        if current_best is None or current_best.epoch_id != eff_epoch:
            # First candidate in epoch becomes the initial baseline
            self._best[artifact_key] = record
            logger.info(f"Initial baseline for '{artifact_key}' in epoch '{eff_epoch}': Score={composite_score} (Attempt {attempt}).")
        else:
            eligible, reason = self.check_candidate_eligibility(record, current_best)
            if eligible:
                self._best[artifact_key] = record
                logger.info(f"Promoted new baseline for '{artifact_key}': Score={composite_score} at Attempt {attempt}.")
            else:
                record.regressed = True
                record.rejection_reason = reason
                logger.warning(
                    f"MONOTONIC INVARIANT REGRESSION for '{artifact_key}' at Attempt {attempt}: {reason}. "
                    f"Preserving baseline from Attempt {current_best.attempt} (Score={current_best.composite_score})."
                )

        history.append(record)
        return record

    def invalidate_epoch(self, new_epoch_id: str, reason: str) -> None:
        """Invalidates current epoch baseline certifications when source, model, or policy changes."""
        logger.info(f"Invalidating evaluation epoch '{self.current_epoch}' -> '{new_epoch_id}': {reason}")
        self.current_epoch = new_epoch_id
        self._best.clear()

    def get_best_candidate(self, artifact_key: str) -> Optional[CandidateRecord]:
        """Returns the accepted baseline CandidateRecord."""
        return self._best.get(artifact_key)

    def get_best_content(self, artifact_key: str) -> Optional[str]:
        """Returns the artifact content string for the accepted baseline candidate."""
        rec = self._best.get(artifact_key)
        return rec.content if rec else None

    def get_high_water_mark_score(self, artifact_key: str) -> int:
        """Returns the accepted baseline composite score for this artifact key, or 0."""
        rec = self._best.get(artifact_key)
        return rec.composite_score if rec else 0

    def get_latest_candidate(self, artifact_key: str) -> Optional[CandidateRecord]:
        """Returns the most recently recorded CandidateRecord for this artifact key."""
        history = self._history.get(artifact_key, [])
        return history[-1] if history else None

    def has_regressed(self, artifact_key: str) -> bool:
        """Returns True if the most recent attempt regressed or was rejected."""
        latest = self.get_latest_candidate(artifact_key)
        if not latest:
            return False
        return latest.regressed

    def restore_best_candidate(self, artifact_key: str) -> Optional[Tuple[str, CandidateRecord]]:
        """Restores the accepted baseline candidate content and record."""
        best = self.get_best_candidate(artifact_key)
        if not best:
            return None
        latest = self.get_latest_candidate(artifact_key)
        if latest and latest.attempt != best.attempt:
            logger.warning(
                f"RESTORING ACCEPTED BASELINE for '{artifact_key}': "
                f"Using Attempt {best.attempt} candidate (Score={best.composite_score}) "
                f"over rejected Attempt {latest.attempt} candidate (Score={latest.composite_score})."
            )
        return best.content, best

    def get_accepted_trajectory(self, artifact_key: str) -> List[int]:
        """
        Returns the accepted baseline scores trajectory across attempts.
        For raw attempts [84, 81, 88], returns [84, 84, 88].
        """
        trajectory: List[int] = []
        cur_accepted = 0
        for rec in self._history.get(artifact_key, []):
            if not rec.regressed:
                cur_accepted = rec.composite_score
            trajectory.append(cur_accepted)
        return trajectory

    def get_all_best_candidates(self) -> Dict[str, CandidateRecord]:
        """Returns a mapping of artifact_key to the best CandidateRecord achieved."""
        return dict(self._best)

    def get_history(self, artifact_key: str) -> List[CandidateRecord]:
        """Returns full candidate history for artifact_key in chronological order."""
        return list(self._history.get(artifact_key, []))

    def reset(self) -> None:
        """Resets ledger state."""
        self._best.clear()
        self._history = {"output_1": [], "output_2": [], "output_3": []}
