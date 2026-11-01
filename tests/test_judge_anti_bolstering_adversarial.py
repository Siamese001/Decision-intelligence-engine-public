"""
Adversarial and edge-case test suite for Wave 1: Anti-Bolstering & Authentic HITL Judge Governance.

Covers mandatory edge-case categories:
1. Boundary payload (84.9 vs 85.0 clarification bound, 94.0 vs 95.0 Output 1 pass threshold, 25.0 max D1)
2. Malformed input (boolean/missing-SHA pinned evaluations, empty Mapping approval origin)
3. Concurrency & race (parallel clarification and origin validation across threads)
4. Overflow & exhaustion (attempt < max_attempts vs final attempt exhaustion)
5. Invariant bypass attempt (prior_user_command auto-affirm, synthetic approval tokens, floor-breach affirm, DETERMINISTIC_QA_FAILED overwrite, U0 HITL bypass)
6. Rollback & recovery (artifact SHA mismatch invalidating stale pinned evaluation)
7. Exact production regression (Run #20 Output 1 D1=31/25 padding and Run #3 Output 2 84->90 floor-breach bolstering)
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pytest

from dual_pipeline.canonical_pipeline.evaluator import (
    _merge_pinned_into_judge_report,
    _resolve_pinned_evaluation,
    _sync_and_reseal_evaluation,
)
from dual_pipeline.canonical_pipeline.strategies.synthesis import SynthesisResult
from dual_pipeline.cli.intake_approval_helper import attach_cli_judge_resolver
from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.hitl_governance_core.surfacing import validate_approval_origin
from dual_pipeline.llm_judge_core.forensic_checks import assign_forensic_verdict
from dual_pipeline.llm_judge_core.judge_clarification import evaluate_judge_clarification
from dual_pipeline.transcript_pipeline_core.qa_runner import _freeze_passing_outputs


def _make_out1_card(score: float = 89.0, d1: float = 23.0, d2: float = 22.0, d3: float = 22.0, d4: float = 22.0, verdict: str = "PASS_ADVISORY") -> dict:
    return {
        "composite_score": score,
        "verdict": verdict,
        "status": verdict,
        "dimension_1_forensic_grounding_and_fidelity": d1,
        "dimension_2_attribution_and_room_mic_hygiene": d2,
        "dimension_3_chronological_and_timestamp_integrity": d3,
        "dimension_4_asr_cleanup_and_domain_preservation": d4,
        "forensic_rationale": "Minor stylistic deduction on transition phrasing.",
    }


def _make_out2_card(score: float = 88.0, d6: float = 14.0, verdict: str = "FAIL") -> dict:
    # Output 3 / Output 2 contract-valid scorecard
    return {
        "composite_score": score,
        "verdict": verdict,
        "status": verdict,
        "section_scores": {
            "header_and_overview": 9.0,
            "key_updates_technical_substance": 32.0,
            "conditional_extensions": 17.0,
            "appendix_stakeholder_perspectives": score - 58.0,
        },
        "executive_rationale": "Minor narrative framing preference.",
    }


def _make_out3_card(score: float = 88.0, d6: float = 14.0, verdict: str = "FAIL") -> dict:
    return {
        "composite_score": score,
        "verdict": verdict,
        "status": verdict,
        "dimension_1_strategic_edge": 14.0,
        "dimension_2_knowledge_layer_density": 14.0,
        "dimension_3_semantic_atomicity": 19.0,
        "dimension_4_predicate_specificity": 14.0,
        "dimension_5_exclusion_of_action_items": score - 61.0 - d6,
        "dimension_6_evidence_grounding_and_schema": d6,
        "graph_rationale": "Minor edge density preference.",
    }


class TestProductionRegressionAntiBolstering(unittest.TestCase):
    """Category 7: Exact production regressions from the 20-run forensic audit."""

    def test_regression_run20_no_score_or_d1_inflation_on_affirm_valid(self) -> None:
        """Run #20 regression: raw 89 on Output 1 must NOT inflate composite_score to 95 or D1 from 25 to 31."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = HITLDecisionStore(Path(tmpdir) / "ledger.json")
            card = _make_out1_card(score=89.0, d1=25.0, d2=22.0, d3=21.0, d4=21.0)
            j_rep = {"output_1_evaluation": card}
            final_dec = {
                "attempt": 3,
                "max_attempts": 3,
                "output_1_pass": False,
                "output_1_deterministic_gates_pass": True,
            }
            meta = {"judge_clarification_resolver": lambda env: "AFFIRM_VALID"}

            res = evaluate_judge_clarification(j_rep, final_dec, metadata=meta, store=store)
            assert res is True
            assert final_dec["judge_clarification_triggered"] is True
            out1 = j_rep["output_1_evaluation"]
            # Raw score and raw dimensions MUST be preserved without mutation
            assert out1["composite_score"] == 89.0
            assert out1["dimension_1_forensic_grounding_and_fidelity"] == 25.0
            assert out1["verdict"] == "PASS_AUTHORIZED_EXCEPTION"
            assert out1["status"] == "PASS_AUTHORIZED_EXCEPTION"
            assert out1["authorized_exception"] is True
            assert final_dec["output_1_pass"] is True

    def test_regression_run3_floor_breach_blocks_affirm_valid(self) -> None:
        """Run #3 regression: Output 2 with score 84 (below 85 bound) or Output 3 with D6=13 (<14 floor) cannot affirm."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = HITLDecisionStore(Path(tmpdir) / "ledger.json")
            # Case A: score 84.0 is below 85.0 lower bound -> never even prompts
            card_84 = _make_out2_card(score=84.0)
            j_rep_84 = {"output_2_evaluation": card_84}
            final_dec_84 = {
                "attempt": 3,
                "max_attempts": 3,
                "output_2_pass": False,
                "output_2_deterministic_gates_pass": True,
            }
            called = []
            meta = {"judge_clarification_resolver": lambda env: (called.append(env), "AFFIRM_VALID")[1]}
            res_84 = evaluate_judge_clarification(j_rep_84, final_dec_84, metadata=meta, store=store)
            assert res_84 is False
            assert len(called) == 0
            assert j_rep_84["output_2_evaluation"]["composite_score"] == 84.0
            assert final_dec_84["output_2_pass"] is False

            # Case B: score 88.0 on Output 3 with D6=13.0 (<14.0 floor) -> even if resolver returns AFFIRM_VALID, it is blocked
            card_flr = _make_out3_card(score=88.0, d6=13.0)
            j_rep_flr = {"output_3_evaluation": card_flr}
            final_dec_flr = {
                "attempt": 3,
                "max_attempts": 3,
                "output_3_pass": False,
                "output_3_deterministic_gates_pass": True,
            }
            res_flr = evaluate_judge_clarification(j_rep_flr, final_dec_flr, metadata=meta, store=store)
            assert res_flr is False
            assert j_rep_flr["output_3_evaluation"]["verdict"] == "FAIL"
            assert j_rep_flr["output_3_evaluation"].get("clarification_floor_blocked") is True
            assert final_dec_flr["output_3_pass"] is False


class TestAttemptAndBoundaryGates(unittest.TestCase):
    """Categories 1 & 4: Boundary payloads and attempt exhaustion gates."""

    def test_clarification_suppressed_before_final_attempt(self) -> None:
        """Clarification must NOT trigger on attempt 1 of 3 or 2 of 3 so remediation loop runs first."""
        card = _make_out1_card(score=92.0)
        j_rep = {"output_1_evaluation": card}
        final_dec = {
            "attempt": 1,
            "max_attempts": 3,
            "output_1_pass": False,
            "output_1_deterministic_gates_pass": True,
        }
        meta = {"judge_clarification_resolver": lambda env: "AFFIRM_VALID"}
        res = evaluate_judge_clarification(j_rep, final_dec, metadata=meta)
        assert res is False
        assert final_dec["output_1_pass"] is False

    def test_output1_pass_threshold_enforces_95_not_90(self) -> None:
        """Output 1 with score 92.0 and verdict PASS must fail _sync_and_reseal_evaluation (< 95.0)."""
        card1 = _make_out1_card(score=92.0, d1=23.0, d2=23.0, d3=23.0, d4=23.0, verdict="PASS")
        card2 = _make_out2_card(score=92.0, verdict="PASS")
        card3 = _make_out3_card(score=93.0, d6=18.0, verdict="PASS")
        j_rep = {
            "output_1_evaluation": card1,
            "output_2_evaluation": card2,
            "output_3_evaluation": card3,
            "cross_output_reconciliation": {"status": "PASS"},
        }
        final_dec = {
            "output_1_deterministic_gates_pass": True,
            "output_2_deterministic_gates_pass": True,
            "output_3_deterministic_gates_pass": True,
        }
        qa_rep = {"passed": True}
        syn = SynthesisResult(
            output_1="Transcript body",
            output_2="Recap body",
            output_3='{"nodes": []}',
        )
        auth = _sync_and_reseal_evaluation(
            judge_rep=j_rep,
            final_dec=final_dec,
            qa_rep=qa_rep,
            synthesis_result=syn,
            source_text="Source",
            meta={},
            judge_model="test-model",
            reasoning_effort="medium",
            is_notes=False,
            is_document=False,
        )
        assert auth is False
        assert final_dec["output_1_pass"] is False
        assert final_dec["output_1_llm_score_pass"] is False
        assert final_dec["final_pass"] is False


class TestInvariantBypassAndOriginHardening(unittest.TestCase):
    """Categories 2, 5, 6: Malformed pinned dicts, synthetic HITL origin bypasses, and SHA rollback."""

    def test_cli_judge_resolver_blocks_prior_command_and_defaults_to_uphold(self) -> None:
        """attach_cli_judge_resolver must never auto-affirm from prior_user_command or empty input."""
        meta = {"prior_user_command": "ingest-meeting --input foo.mp4", "interactive_hitl": True}
        attach_cli_judge_resolver(meta)
        resolver = meta["hitl_judge_resolver"]

        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", return_value=""):
            choice = resolver({"question": "Clarify?", "context": {"output_num": 1, "composite_score": 91}})
        assert choice == "UPHOLD_DEDUCTION"

    def test_validate_approval_origin_blocks_synthetic_and_prior_command(self) -> None:
        """validate_approval_origin must reject prior_user_command bypasses and synthetic markers."""
        ok1, _ = validate_approval_origin({"prior_user_command": "ingest-meeting --approve"})
        assert ok1 is False

        ok2, _ = validate_approval_origin({"source": "interactive_operator", "raw_text": "auto_approve_hitl"})
        assert ok2 is False

        ok3, _ = validate_approval_origin({})
        assert ok3 is False

        ok4, _ = validate_approval_origin({"source": "interactive_operator", "raw_text": "Confirmed speaker is David."})
        assert ok4 is True

    def test_pinned_evaluation_rejects_boolean_and_missing_or_mismatched_sha(self) -> None:
        """_resolve_pinned_evaluation and _merge_pinned_into_judge_report must reject fabricated booleans or SHA mismatches."""
        meta_bool = {"pinned_evaluations": {"output_1": True, "output_2": {"composite_score": 95, "verdict": "PASS"}}}
        resolved = _resolve_pinned_evaluation(meta_bool, attempt=2)
        assert resolved is None

        text_v1 = "Transcript Version 1"
        sha_v1 = hashlib.sha256(text_v1.encode("utf-8")).hexdigest()
        valid_card = _make_out1_card(score=96.0, d1=24.0, d2=24.0, d3=24.0, d4=24.0, verdict="PASS")
        valid_card["artifact_sha256"] = sha_v1

        meta_valid = {"pinned_evaluations": {"output_1": valid_card}}
        resolved_valid = _resolve_pinned_evaluation(meta_valid, attempt=2)
        assert resolved_valid is not None and "output_1" in resolved_valid

        # Attempt to merge when artifact text mutated to Version 2 -> must reject stale pin
        j_rep = {"output_1_evaluation": _make_out1_card(score=82.0, verdict="FAIL")}
        syn_mutated = SynthesisResult(
            output_1="Transcript Version 2 (mutated)",
            output_2="Recap",
            output_3="{}",
        )
        _merge_pinned_into_judge_report(j_rep, resolved_valid, meta=meta_valid, synthesis_result=syn_mutated)
        assert j_rep["output_1_evaluation"]["composite_score"] == 82.0

        # Attempt to merge onto DETERMINISTIC_QA_FAILED stub -> must never overwrite
        j_rep_det_fail = {
            "output_1_evaluation": {
                "status": "SKIPPED",
                "verdict": "FAIL",
                "skip_reason": "DETERMINISTIC_QA_FAILED",
                "composite_score": 0,
            }
        }
        syn_v1 = SynthesisResult(
            output_1=text_v1,
            output_2="Recap",
            output_3="{}",
        )
        _merge_pinned_into_judge_report(j_rep_det_fail, resolved_valid, meta=meta_valid, synthesis_result=syn_v1)
        assert j_rep_det_fail["output_1_evaluation"]["skip_reason"] == "DETERMINISTIC_QA_FAILED"
        assert j_rep_det_fail["output_1_evaluation"]["composite_score"] == 0

    def test_freeze_passing_outputs_refuses_advisory_and_skipped(self) -> None:
        """_freeze_passing_outputs must not freeze Output 1 with score 89 PASS_ADVISORY or SKIPPED status."""
        frozen = {}

        class DummyStateMachine:
            def freeze_artifact(self, key: str, val: str) -> None:
                frozen[key] = val

        sm = DummyStateMachine()
        meta: dict = {}
        j_rep = {
            "output_1_evaluation": _make_out1_card(score=89.0, verdict="PASS_ADVISORY"),
            "output_2_evaluation": {"status": "SKIPPED", "verdict": "PASS", "composite_score": 95.0},
            "output_3_evaluation": {"status": "SKIPPED", "verdict": "PASS", "composite_score": 95.0},
        }
        final_dec = {
            "output_1_pass": False,
            "output_1_deterministic_gates_pass": True,
            "output_2_pass": False,
            "output_2_deterministic_gates_pass": True,
            "output_3_pass": False,
            "output_3_deterministic_gates_pass": True,
        }
        _freeze_passing_outputs(sm, final_dec, "T1", "R2", "{}", judge_report=j_rep, qa_report={}, metadata=meta)
        assert frozen == {}
        assert meta.get("pinned_evaluations") == {}

    def test_assign_forensic_verdict_syncs_status_and_enforces_hitl_guard(self) -> None:
        """assign_forensic_verdict must synchronize status == verdict and reject U0 HITL bypass."""
        parsed = _make_out1_card(score=96.0, d1=24.0, d2=24.0, d3=24.0, d4=24.0, verdict="PASS")
        parsed["status"] = "STALE_STATUS"
        res = assign_forensic_verdict(parsed, f1_text=None, metadata={})
        assert res["status"] == res["verdict"]

        with pytest.raises(ValueError, match="HITL cannot be removed, disabled, or bypassed"):
            assign_forensic_verdict(parsed, f1_text=None, metadata={"bypass_hitl": True})


class TestConcurrencySafety(unittest.TestCase):
    """Category 3: Concurrent clarification & origin checks across threads."""

    def test_concurrent_clarification_evaluations_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            def _worker(idx: int) -> tuple[float, str]:
                store = HITLDecisionStore(Path(tmpdir) / f"ledger_{idx}.json")
                score = 86.0 + (idx % 8)
                card = _make_out1_card(score=score, d1=23.0, d2=23.0, d3=23.0, d4=score - 69.0)
                j_rep = {"output_1_evaluation": card}
                f_dec = {
                    "attempt": 3,
                    "max_attempts": 3,
                    "output_1_pass": False,
                    "output_1_deterministic_gates_pass": True,
                }
                meta = {"judge_clarification_resolver": lambda env: "AFFIRM_VALID" if idx % 2 == 0 else "UPHOLD_DEDUCTION"}
                evaluate_judge_clarification(j_rep, f_dec, metadata=meta, store=store)
                return j_rep["output_1_evaluation"]["composite_score"], j_rep["output_1_evaluation"]["verdict"]

            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(_worker, range(16)))

            for idx, (final_score, verdict) in enumerate(results):
                expected_raw = 86.0 + (idx % 8)
                assert final_score == expected_raw
                if idx % 2 == 0:
                    assert verdict == "PASS_AUTHORIZED_EXCEPTION"
                else:
                    assert verdict == "PASS_ADVISORY"
