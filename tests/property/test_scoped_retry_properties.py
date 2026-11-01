"""
tests/property/test_scoped_retry_properties.py

Hypothesis Property-Based Verification Suite for Scoped Retries and Parallel Judging.
Governed under Decision H-8 and Post-Recording Ingestion SLA (Wave 4).
Focuses on:
  - Deliverable unfreezing & preservation under retry_scope
  - Timeout scorecard synthesis & validation invariance
  - Diagnostics collector retry_scope deduction under arbitrary failure distributions
"""
from __future__ import annotations

import concurrent.futures
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import hypothesis.strategies as st
from hypothesis import given, settings

from dual_pipeline.canonical_pipeline.checkpoint_memoizer import _should_regenerate_output
from dual_pipeline.release_policy import validate_micro_judge_scorecard
from dual_pipeline.transcript_qa_core.evaluator import _await_micro_judge, _record_diagnostics_if_needed


class MockStateMachine:
    """Mock state machine for tracking frozen and unfrozen artifacts."""

    def __init__(self, initial_frozen: Optional[List[str]] = None):
        self.frozen: Dict[str, Any] = {k: "content" for k in (initial_frozen or [])}
        self.unfrozen_calls: List[str] = []

    def is_frozen(self, key: str) -> bool:
        return key in self.frozen

    def unfreeze_artifact(self, key: str, force: bool = False) -> None:
        self.unfrozen_calls.append(key)
        self.frozen.pop(key, None)

    def freeze_artifact(self, key: str, content: Any) -> None:
        self.frozen[key] = content


class TestScopedRetryProperties(unittest.TestCase):
    """Property-based verification of scoped retries and parallel judge safety."""

    # ── Property 1: retry_scope isolation and unfreeze targeting ──

    @settings(max_examples=50, deadline=None)
    @given(
        target_output=st.sampled_from(["1", "2", "3"]),
        other_outputs=st.lists(st.sampled_from(["1", "2", "3"]), max_size=2),
    )
    def test_scoped_retry_isolation_property(
        self, target_output: str, other_outputs: List[str]
    ) -> None:
        """When retry_scope targets a specific output, only that output is regenerated and unfrozen."""
        sm = MockStateMachine(initial_frozen=["output_1", "output_2", "output_3"])
        metadata = {"retry_scope": f"output_{target_output}"}

        # The targeted output must regenerate
        should_regen_target = _should_regenerate_output(sm, target_output, "", metadata)
        self.assertTrue(should_regen_target)
        self.assertIn(f"output_{target_output}", sm.unfrozen_calls)

        # Other non-targeted outputs must NOT regenerate under single-target scope
        for other in ["1", "2", "3"]:
            if other != target_output:
                sm_other = MockStateMachine(initial_frozen=["output_1", "output_2", "output_3"])
                should_regen_other = _should_regenerate_output(sm_other, other, "", metadata)
                self.assertFalse(should_regen_other)
                self.assertNotIn(f"output_{other}", sm_other.unfrozen_calls)

    # ── Property 2: Arbitrary retry_scope robust parsing ──

    @settings(max_examples=50, deadline=None)
    @given(
        scope_str=st.text(max_size=40),
        regen_spec=st.text(max_size=40),
        num=st.sampled_from(["1", "2", "3"]),
    )
    def test_arbitrary_retry_scope_no_crash(
        self, scope_str: str, regen_spec: str, num: str
    ) -> None:
        """_should_regenerate_output handles arbitrary messy scope strings without throwing."""
        sm = MockStateMachine(initial_frozen=["output_1", "output_2", "output_3"])
        metadata = {"retry_scope": scope_str}
        try:
            result = _should_regenerate_output(sm, num, regen_spec, metadata)
            self.assertIsInstance(result, bool)
        except Exception as exc:
            self.fail(f"_should_regenerate_output crashed on scope={scope_str!r}: {exc}")

    # ── Property 3: Judge timeout returns compliant failing scorecard ──

    @settings(max_examples=25, deadline=None)
    @given(
        key=st.sampled_from(["out1", "out2", "out3"]),
        out_num=st.sampled_from([1, 2, 3]),
    )
    def test_await_micro_judge_timeout_scorecard_property(
        self, key: str, out_num: int
    ) -> None:
        """When a judge future times out, _await_micro_judge returns a valid FAIL scorecard."""
        hanging_future: concurrent.futures.Future[Dict[str, Any]] = concurrent.futures.Future()
        mock_fut = MagicMock()
        mock_fut.result.side_effect = concurrent.futures.TimeoutError("Judge timeout")

        card = _await_micro_judge(mock_fut, key, out_num)
        self.assertEqual(card.get("status"), "FAIL")
        self.assertEqual(card.get("verdict"), "FAIL")
        self.assertEqual(card.get("composite_score"), 0)
        self.assertEqual(card.get("failure_type"), "TIMEOUT")

        # Must pass schema scorecard validation cleanly
        is_valid, errors = validate_micro_judge_scorecard(card, output_num=out_num)
        self.assertTrue(is_valid, f"Scorecard should be valid under timeout exception: {errors}")

    # ── Property 4: Diagnostics retry_scope deduplication and consistency ──

    @settings(max_examples=50, deadline=None)
    @given(
        failing_outputs=st.lists(st.sampled_from(["output_1", "output_2", "output_3"]), min_size=0, max_size=3),
    )
    def test_diagnostics_retry_scope_aggregation(
        self, failing_outputs: List[str]
    ) -> None:
        """_record_diagnostics_if_needed aggregates and sorts failing output scopes deterministically."""
        meta: Dict[str, Any] = {}
        qa_report = {
            "passed": len(failing_outputs) == 0,
            "output_01_forensic_transcript": {"passed": "output_1" not in failing_outputs, "gates": {}},
            "output_02_executive_recap": {"passed": "output_2" not in failing_outputs, "gates": {}},
            "output_03_canonical_signal_projection": {"passed": "output_3" not in failing_outputs, "gates": {}},
        }
        judge_report: Dict[str, Any] = {}
        final_decision: Dict[str, Any] = {
            "status": "PASS" if len(failing_outputs) == 0 else "FAIL",
            "output_1_pass": "output_1" not in failing_outputs,
            "output_2_pass": "output_2" not in failing_outputs,
            "output_3_pass": "output_3" not in failing_outputs,
        }

        _record_diagnostics_if_needed(qa_report, judge_report, final_decision, meta)

        if failing_outputs:
            unique_failures = sorted(set(failing_outputs))
            expected_scope = ",".join(unique_failures)
            self.assertEqual(meta.get("retry_scope"), expected_scope)
            self.assertEqual(final_decision.get("retry_scope"), expected_scope)
            self.assertEqual(judge_report.get("retry_scope"), expected_scope)
        else:
            self.assertNotIn("retry_scope", meta)


if __name__ == "__main__":
    unittest.main()
