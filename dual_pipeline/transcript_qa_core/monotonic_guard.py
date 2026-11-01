"""
Monotonic Progress Guard for Pipeline Artifacts.

Prevents quality regressions during multi-turn LLM remediation by tracking
candidate checkpoints and rejecting/rolling back any mutation that scores lower
than a previously established score (S_{t+1} >= S_t invariant).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class CandidateCheckpoint:
    """Immutable checkpoint of an evaluated artifact candidate."""
    attempt: int
    composite_score: int
    artifact_text: str
    scorecard: Dict[str, Any]
    verdict: str


class MonotonicGuard:
    """Enforces monotonic progress invariant: Score(t+1) >= Score(t)."""

    def __init__(self, artifact_name: str = "OUTPUT02", target_score: Optional[int] = None) -> None:
        if target_score is None:
            try:
                from dual_pipeline.release_thresholds import CANONICAL_RELEASE_POLICY
                if artifact_name == "OUTPUT01":
                    target_score = CANONICAL_RELEASE_POLICY.output1_pass_threshold
                elif artifact_name == "OUTPUT02":
                    target_score = CANONICAL_RELEASE_POLICY.output2_pass_threshold
                elif artifact_name == "OUTPUT03":
                    target_score = CANONICAL_RELEASE_POLICY.output3_pass_threshold
                else:
                    target_score = 90
            except Exception:
                target_score = 90
        self.artifact_name = artifact_name
        self.target_score = target_score
        self.checkpoints: List[CandidateCheckpoint] = []
        self.best_checkpoint: Optional[CandidateCheckpoint] = None

    @property
    def best_score(self) -> int:
        """Returns the highest score achieved so far."""
        return self.best_checkpoint.composite_score if self.best_checkpoint else 0

    def evaluate_candidate(
        self,
        attempt: int,
        composite_score: int,
        artifact_text: str,
        scorecard: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any], bool]:
        """
        Evaluates a new candidate against the monotonic progress invariant.

        Returns:
            (effective_text, effective_scorecard, is_improvement)
            If the new candidate regressed, effective_text and effective_scorecard
            roll back to the best prior checkpoint, and is_improvement is False.
        """
        output_num = 1 if "1" in self.artifact_name else (2 if "2" in self.artifact_name else 3)
        from dual_pipeline.release_policy import validate_micro_judge_scorecard
        is_valid, validation_errors = validate_micro_judge_scorecard(scorecard, output_num=output_num)

        verdict = scorecard.get("verdict", "FAIL") if is_valid else "FAIL"
        checkpoint = CandidateCheckpoint(
            attempt=attempt,
            composite_score=composite_score if is_valid else 0,
            artifact_text=artifact_text,
            scorecard=scorecard,
            verdict=verdict,
        )
        self.checkpoints.append(checkpoint)

        if not is_valid:
            logger.warning(
                "[%s] Candidate at attempt %d rejected: invalid scorecard (%s)",
                self.artifact_name, attempt, validation_errors,
            )
            if self.best_checkpoint is not None:
                return (
                    self.best_checkpoint.artifact_text,
                    self.best_checkpoint.scorecard,
                    False,
                )
            return artifact_text, scorecard, False

        if self.best_checkpoint is None:
            self.best_checkpoint = checkpoint
            logger.info(
                "[%s] Initial baseline established at attempt %d: score=%d (%s)",
                self.artifact_name, attempt, composite_score, verdict,
            )
            return artifact_text, scorecard, True

        if composite_score >= self.best_checkpoint.composite_score:
            improved = composite_score > self.best_checkpoint.composite_score
            self.best_checkpoint = checkpoint
            logger.info(
                "[%s] Monotonic progress verified at attempt %d: score=%d >= best=%d (%s)",
                self.artifact_name, attempt, composite_score, self.best_score,
                "IMPROVEMENT" if improved else "PARITY",
            )
            return artifact_text, scorecard, True

        # Regression detected: Roll back to best checkpoint
        regressed_diff = self.best_checkpoint.composite_score - composite_score
        logger.warning(
            "[%s] REGRESSION DETECTED at attempt %d: new score=%d < best score=%d (delta -%d). "
            "Rolling back to attempt %d checkpoint!",
            self.artifact_name, attempt, composite_score, self.best_checkpoint.composite_score,
            regressed_diff, self.best_checkpoint.attempt,
        )
        return (
            self.best_checkpoint.artifact_text,
            self.best_checkpoint.scorecard,
            False,
        )
