"""
Blind Position-Swap Debiasing Harness for Comparative Evaluations.

Enforces 2026+ Anthropic and OpenAI comparative evaluation standards:
- Eliminates primacy and recency order bias by evaluating candidates in both orientations:
  Forward: (A, B) -> Verdict 1
  Reverse: (B, A) -> Verdict 2
- Measures position-consistency rate and penalizes non-symmetric judgments.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Tuple

from dual_pipeline.hitl_governance import DEFAULT_MARGIN_THRESHOLD


@dataclass
class PositionSwapResult:
    """Outcome of a dual-orientation position-swap evaluation."""

    candidate_a_id: str
    candidate_b_id: str
    forward_winner: str  # candidate ID or "TIE"
    reverse_winner: str  # candidate ID or "TIE"
    is_consistent: bool
    final_winner: str    # candidate ID or "INCONCLUSIVE_ORDER_BIAS"
    confidence_forward: float
    confidence_reverse: float
    order_bias_detected: bool
    details: Dict[str, Any]


class PositionSwapDebiaser:
    """Evaluates pairwise comparative judgments in forward and reverse orders to detect and mitigate position bias."""

    def __init__(self, min_confidence_margin: float = DEFAULT_MARGIN_THRESHOLD):
        self.min_confidence_margin = min_confidence_margin

    def evaluate_pair(
        self,
        candidate_a: Dict[str, Any],
        candidate_b: Dict[str, Any],
        judge_fn: Callable[[Dict[str, Any], Dict[str, Any]], Tuple[str, float]],
    ) -> PositionSwapResult:
        """
        Evaluate pair (A, B) using judge_fn(first, second) -> (winner_id, confidence).
        
        Executes:
          Forward: judge_fn(A, B)
          Reverse: judge_fn(B, A)
        """
        id_a = str(candidate_a.get("id", "candidate_A"))
        id_b = str(candidate_b.get("id", "candidate_B"))

        # Forward presentation: A is first, B is second
        winner_fwd, conf_fwd = judge_fn(candidate_a, candidate_b)

        # Reverse presentation: B is first, A is second
        winner_rev, conf_rev = judge_fn(candidate_b, candidate_a)

        # Check consistency:
        # If A won forward, A must also win reverse.
        # If B won forward, B must also win reverse.
        # If both were TIE, they are consistent.
        is_consistent = (winner_fwd == winner_rev)
        order_bias = not is_consistent

        final_winner: str
        if is_consistent:
            final_winner = winner_fwd
        else:
            # Order bias detected (e.g. judge always chose the first candidate presented)
            final_winner = "INCONCLUSIVE_ORDER_BIAS"

        return PositionSwapResult(
            candidate_a_id=id_a,
            candidate_b_id=id_b,
            forward_winner=winner_fwd,
            reverse_winner=winner_rev,
            is_consistent=is_consistent,
            final_winner=final_winner,
            confidence_forward=round(conf_fwd, 4),
            confidence_reverse=round(conf_rev, 4),
            order_bias_detected=order_bias,
            details={
                "margin": round(abs(conf_fwd - conf_rev), 4),
                "margin_gate_passed": abs(conf_fwd - conf_rev) <= self.min_confidence_margin,
            },
        )
