"""
Property-based tests for Wave 3: Judge Convergence, QA Consistency & Evaluation Caching.
Verifies caching by digest, scorecard validation, and diagnostics attempt tracking.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest
from hypothesis import given, settings, strategies as st

from dual_pipeline.release_policy import validate_micro_judge_scorecard
from dual_pipeline.transcript_qa_core.eval_cache import (
    clear_evaluation_cache,
    resolve_pins_and_cache,
    update_evaluation_cache,
    build_short_circuit_eval,
)
from dual_pipeline.transcript_qa_core.judge_failure_diagnostics import (
    JudgeFailureDiagnosticsCollector,
    JudgeFailureRecord,
)


class TestJudgeConvergenceProperties:
    def setup_method(self):
        clear_evaluation_cache()

    def teardown_method(self):
        clear_evaluation_cache()

    @given(
        is_pass=st.booleans(),
        score_offset=st.integers(min_value=0, max_value=8),
    )
    @settings(max_examples=15, deadline=None)
    def test_property_unchanged_digest_never_rejudged(self, is_pass, score_offset):
        """Invariant: An unchanged digest in cache returns the cached scorecard without re-evaluating."""
        clear_evaluation_cache()
        if is_pass:
            score = 90 + score_offset  # >= 90 is PASS for Output 1
            verdict = "PASS"
        else:
            score = 60 + score_offset  # < 80 is FAIL for Output 1
            verdict = "FAIL"

        f1 = f"Speaker A: Testing judge caching property {score} {verdict}.\nSpeaker B: Understood."
        f2 = f"# Executive Summary\nProperty test recap {score}."
        f3 = json.dumps({"entities": [{"id": f"t_{score}"}], "decisions": []})
        model = "gpt-6-luna"
        effort = "high"

        # Initial lookup misses
        o1, o2, o3, k1, k2, k3 = resolve_pins_and_cache(
            f1, f2, f3, model, effort, pinned={}, allow_failure=True
        )
        assert o1 is None

        # Cache a valid scorecard for output 1 (composite sum matching dimensions)
        dim = score / 4.0
        e1 = {
            "composite_score": score,
            "verdict": verdict,
            "dimension_1": dim,
            "dimension_2": dim,
            "dimension_3": dim,
            "dimension_4": dim,
            "is_valid_scorecard": True,
        }
        update_evaluation_cache(k1, e1, None, None, None, None)

        # Second lookup with identical text hits cache even on failure
        o1_cached, _, _, _, _, _ = resolve_pins_and_cache(
            f1, f2, f3, model, effort, pinned={}, allow_failure=True
        )
        assert o1_cached is not None
        assert o1_cached["source"] == "CONTENT_CACHE"
        assert o1_cached["composite_score"] == score
        assert o1_cached["verdict"] == verdict

    @given(
        dim_sum_diff=st.floats(min_value=0.5, max_value=50.0),
    )
    @settings(max_examples=20)
    def test_property_malformed_scorecard_rejected_by_validator(self, dim_sum_diff):
        """Invariant: Scorecards where dimensions do not sum to composite score fail schema validation."""
        composite = 90
        # Dimensions summing to composite + dim_sum_diff
        dim = (composite + dim_sum_diff) / 4.0
        scorecard = {
            "composite_score": composite,
            "verdict": "PASS",
            "dimension_1": dim,
            "dimension_2": dim,
            "dimension_3": dim,
            "dimension_4": dim,
        }
        is_valid, errs = validate_micro_judge_scorecard(scorecard, output_num=1)
        assert not is_valid
        assert len(errs) > 0
        assert any("does not match dimension sum" in e or "out of bounds" in e for e in errs)

    @given(
        num_failures=st.integers(min_value=1, max_value=10),
        attempt=st.integers(min_value=1, max_value=5),
    )
    @settings(max_examples=20)
    def test_property_diagnostics_attempt_tracking_never_zero(self, num_failures, attempt):
        """Invariant: Diagnostics payload total_attempts accurately reflects recorded attempts, never 0."""
        collector = JudgeFailureDiagnosticsCollector()
        for i in range(num_failures):
            rec = JudgeFailureRecord(
                output="02",
                attempt=attempt,
                judge="ExecutiveRecapJudge",
                gate="Gate_2_1",
                criterion="Test criterion",
                status="FAIL",
                score=50,
                threshold=85,
                failure_type="CONTENT",
                expected="Pass",
                observed="Fail",
                evidence_ids=None,
                root_cause="Test root cause",
                remediation="Test remediation",
                retry_scope="output_2",
                llm_required=False,
                retry_count=0,
                elapsed_ms=10.0,
            )
            collector.record_failure(rec)

        payload = collector.to_diagnostics_payload()
        assert payload["metadata"]["total_failures"] == num_failures
        assert payload["metadata"]["total_attempts"] >= attempt
        assert payload["metadata"]["total_attempts"] > 0

    def test_property_short_circuit_preserves_frozen_artifact_pass(self):
        """Invariant: QA short-circuiting on Output 2 does not demote frozen Output 1."""
        qa_report = {
            "passed": False,
            "output_01_forensic_transcript": {
                "passed": True,
                "status": "PASS",
                "frozen": True,
                "gates": {"Gate_1_1": {"passed": True}},
            },
            "output_02_executive_recap": {
                "passed": False,
                "status": "FAIL",
                "failures": ["Missing Action & Governance Ledger"],
                "gates": {"Gate_2_1": {"passed": False}},
            },
            "output_03_canonical_signal_projection": {
                "passed": True,
                "status": "PASS",
                "gates": {"Gate_3_1": {"passed": True}},
            },
            "cross_artifact_consistency": {"passed": True, "status": "PASS"},
        }

        meta = {"attempt": 2, "max_attempts": 3}
        _, _, final_decision = build_short_circuit_eval(
            qa_report, "f1", "f2", "f3", "gpt-6-luna", metadata=meta
        )

        assert final_decision["attempt"] == 2
        assert final_decision["max_attempts"] == 3
        assert final_decision["short_circuit"] is True
        # Output 1 was frozen and passed QA, so its pass status is preserved
        assert final_decision["output_1_deterministic_gates_pass"] is True
        assert final_decision["output_1_pass"] is True
        # Output 2 failed QA
        assert final_decision["output_2_deterministic_gates_pass"] is False
        assert final_decision["output_2_pass"] is False
        # Overall run failed
        assert final_decision["final_pass"] is False
        assert final_decision["authoritative_write_authorized"] is False
