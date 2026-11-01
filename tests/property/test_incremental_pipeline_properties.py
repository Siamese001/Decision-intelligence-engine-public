"""
tests/property/test_incremental_pipeline_properties.py

Hypothesis Property-Based Verification Suite for Incremental Functionality Testing.
Governed under Decision H-8 and Finding X-32.
Focuses on boundary testing, malformed inputs, overflow conditions, and sanitizer invariants
WITHOUT requiring receipt generation.
"""
from __future__ import annotations

import unittest
from typing import Any, Dict, List
import hypothesis.strategies as st
from hypothesis import given, settings

from dual_pipeline.core import digest
from dual_pipeline.llm_judge_core.judges.leverage import AgenticLeverageJudge
from dual_pipeline.sanitizer import (
    EgressSanitizationError,
    LocalPreEgressSanitizer,
)
from dual_pipeline.signal_extraction import (
    ALLOWED_ENTITY_PREFIXES,
    score_candidate_signal,
)


class TestIncrementalPipelineProperties(unittest.TestCase):
    """Property-based verification of incremental pipeline functions."""

    # ── Property 1: Signal Extraction Robustness under Malformed/Boundary Payloads ──

    @settings(max_examples=50, deadline=None)
    @given(
        signal_dict=st.dictionaries(
            keys=st.text(min_size=0, max_size=30),
            values=st.one_of(
                st.text(max_size=200),
                st.integers(min_value=-1000, max_value=1000),
                st.booleans(),
                st.none(),
                st.lists(st.text(max_size=50), max_size=5),
            ),
            max_size=10,
        ),
        signal_type=st.sampled_from([
            "entity_upsert",
            "relationship_signal",
            "decision_update",
            "action_update",
            "risk_update",
            "proposal_update",
            "strategic_intent_signal",
            "unknown_signal_type",
        ]),
    )
    def test_score_candidate_signal_invariants(
        self, signal_dict: Dict[str, Any], signal_type: str
    ) -> None:
        """Signal extraction never crashes on arbitrary malformed inputs and bounds scores."""
        result = score_candidate_signal(signal_dict, signal_type)
        self.assertIn("epistemic_confidence", result)
        self.assertIn("materiality_score", result)
        self.assertIn("promotion_status", result)

        conf = result["epistemic_confidence"]
        self.assertGreaterEqual(conf, 0.0)
        self.assertLessEqual(conf, 1.0)
        self.assertIn(result["promotion_status"], {"PROMOTED", "QUARANTINED"})

    # ── Property 2: Pre-Egress Sanitizer Boundary & Leak Immunity ────────────────

    @settings(max_examples=50, deadline=None)
    @given(
        clean_text=st.text(
            alphabet=st.characters(blacklist_categories=("Cs",)),
            min_size=1,
            max_size=200,
        ).filter(lambda s: "sk-" not in s and "ya29" not in s and "/hr" not in s.lower() and "hourly rate" not in s.lower() and "c-19" not in s.lower()),
    )
    def test_sanitizer_passes_clean_messages(self, clean_text: str) -> None:
        """Clean messages pass sanitization without errors."""
        messages = [{"role": "user", "content": clean_text}]
        # Should execute cleanly without raising EgressSanitizationError
        LocalPreEgressSanitizer.sanitize_messages(messages)

    @settings(max_examples=50, deadline=None)
    @given(
        secret_suffix=st.text(
            alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            min_size=22,
            max_size=32,
        ),
        prefix=st.text(max_size=50),
        suffix=st.text(max_size=50),
    )
    def test_sanitizer_blocks_secrets_fail_closed(
        self, secret_suffix: str, prefix: str, suffix: str
    ) -> None:
        """Any payload containing a secret pattern is blocked fail-closed."""
        dirty_content = f"{prefix} sk-{secret_suffix} {suffix}"
        messages = [{"role": "user", "content": dirty_content}]
        with self.assertRaises(EgressSanitizationError):
            LocalPreEgressSanitizer.sanitize_messages(messages)

    # ── Property 3: Calibrated Judge Monotonicity & Route Boundary ───────────────

    @settings(max_examples=50, deadline=None)
    @given(
        has_target=st.booleans(),
        has_asymmetry=st.booleans(),
        is_executive=st.booleans(),
    )
    def test_judge_calibration_route_properties(
        self, has_target: bool, has_asymmetry: bool, is_executive: bool
    ) -> None:
        """Verifies that calibrated judge correctly differentiates standard (6) and executive (8) routes."""
        judge = AgenticLeverageJudge(mock=True)
        node = {
            "speaker": "Operator",
            "key_players": ["Director Tech Lead"] if has_target else [],
            "political_rationale": "Extended strategic political rationale exceeding thirty characters" if has_asymmetry else "brief",
            "networking_wedge": "Extended asymmetric career wedge exceeding thirty characters" if has_asymmetry else "wedge",
            "route": "executive" if is_executive else "standard",
        }

        result = judge.evaluate_agentic_leverage(node)

        if not has_target:
            self.assertFalse(result.pass_gate)
            self.assertIn("Target Test", result.rejection_reason or "")
        elif is_executive:
            # Executive route strictly requires materiality >= 8 (which needs has_target AND has_asymmetry)
            if has_asymmetry:
                self.assertTrue(result.pass_gate)
                self.assertGreaterEqual(result.political_materiality, 8)
            else:
                self.assertFalse(result.pass_gate)
                self.assertIn("below SVP threshold of 8", result.rejection_reason or "")
        else:
            # Standard route allows materiality 6 to pass
            self.assertTrue(result.pass_gate)
            self.assertGreaterEqual(result.political_materiality, 6)

    # ── Property 4: Cryptographic Digest Idempotency & Stability ─────────────────

    @settings(max_examples=50, deadline=None)
    @given(payload=st.text(max_size=10000))
    def test_digest_deterministic_property(self, payload: str) -> None:
        """Digest is strictly deterministic, 64-char hexadecimal, and idempotent."""
        h1 = digest(payload)
        h2 = digest(payload)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in h1))


if __name__ == "__main__":
    unittest.main()
