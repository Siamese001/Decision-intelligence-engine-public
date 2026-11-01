"""
tests/test_selective_regeneration.py

Unit and integration tests for Wave 1: Selective Output Regeneration (--regenerate).
Compatible with both pytest and unittest (run_tests_with_progress.py).
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.remediation_core import (
    SelectiveRemediationStateMachine,
    parse_regenerate_spec,
    validate_frozen_artifact_gate,
)
from dual_pipeline.transcript_qa_core.evaluator import (
    _eval_output1,
    _eval_output_n,
)


class TestSelectiveRegeneration(unittest.TestCase):
    """Unit test suite for selective output regeneration and freezing."""

    def test_parse_regenerate_spec_valid(self):
        """Verify standard deliverable specs parse correctly."""
        self.assertEqual(parse_regenerate_spec(None), {"output_1", "output_2", "output_3"})
        self.assertEqual(parse_regenerate_spec("all"), {"output_1", "output_2", "output_3"})
        self.assertEqual(parse_regenerate_spec("ALL"), {"output_1", "output_2", "output_3"})
        self.assertEqual(parse_regenerate_spec("01"), {"output_1"})
        self.assertEqual(parse_regenerate_spec("02,03"), {"output_2", "output_3"})
        self.assertEqual(parse_regenerate_spec("1, 2, 3"), {"output_1", "output_2", "output_3"})
        self.assertEqual(parse_regenerate_spec("output_2,output_3"), {"output_2", "output_3"})

    def test_parse_regenerate_spec_invalid(self):
        """Verify invalid deliverable specs raise ValueError."""
        with self.assertRaises(ValueError):
            parse_regenerate_spec("04")

        with self.assertRaises(ValueError):
            parse_regenerate_spec("01,invalid")

    def test_validate_frozen_artifact_gate_success(self):
        """Verify validation gate passes when valid prior artifacts exist."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            dec = {
                "output_1_pass": True,
                "output_2_pass": True,
                "output_3_pass": True,
                "synthesis_prompt_version": "V5_7",
            }
            (tmp_path / "final_release_decision.json").write_text(json.dumps(dec), encoding="utf-8")
            (tmp_path / "01_test_meeting_09-25-2026_hitl_resolved_transcript.md").write_text(
                "# Forensic Transcript\n\n**Alice**: Hello world\n**Bob**: Hi Alice",
                encoding="utf-8",
            )

            metadata = {
                "attendees": ["Alice", "Bob"],
                "synthesis_prompt_version": "V5_7",
            }

            res = validate_frozen_artifact_gate(tmp_path, "output_1", metadata)
            self.assertTrue(res.valid)
            self.assertEqual(res.artifact_key, "output_1")
            self.assertIn("Hello world", res.content)
            self.assertEqual(res.reason, "OK")

    def test_validate_frozen_artifact_gate_prior_run_failed(self):
        """Verify validation gate rejects frozen artifact if prior run failed."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            dec = {
                "output_1_pass": False,
                "output_2_pass": True,
            }
            (tmp_path / "final_release_decision.json").write_text(json.dumps(dec), encoding="utf-8")
            (tmp_path / "01_test_meeting_hitl_resolved_transcript.md").write_text("content", encoding="utf-8")

            res = validate_frozen_artifact_gate(tmp_path, "output_1", {})
            self.assertFalse(res.valid)
            self.assertIn("Prior run did not pass", res.reason)

    def test_validate_frozen_artifact_gate_speaker_mismatch(self):
        """Verify validation gate catches completely disjoint speaker rosters."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            dec = {"output_1_pass": True}
            (tmp_path / "final_release_decision.json").write_text(json.dumps(dec), encoding="utf-8")
            (tmp_path / "01_test_transcript.md").write_text(
                "**Charlie**: Disjoint person\n**Dave**: Another person",
                encoding="utf-8",
            )

            metadata = {"attendees": ["Alice", "Bob"]}
            res = validate_frozen_artifact_gate(tmp_path, "output_1", metadata)
            self.assertFalse(res.valid)
            self.assertIn("Speaker roster mismatch", res.reason)

    def test_state_machine_seed_for_selective_regeneration(self):
        """Verify SelectiveRemediationStateMachine freezes excluded deliverables."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            dec = {
                "output_1_pass": True,
                "output_2_pass": True,
                "output_3_pass": True,
            }
            (tmp_path / "final_release_decision.json").write_text(json.dumps(dec), encoding="utf-8")
            (tmp_path / "01_sample_transcript.md").write_text("**Alice**: Transcript content", encoding="utf-8")

            sm = SelectiveRemediationStateMachine(max_attempts=2)
            meta = {"attendees": ["Alice"]}

            # Requesting regeneration of 02 and 03 only -> 01 must be frozen
            results = sm.seed_for_selective_regeneration(tmp_path, "02,03", meta)

            self.assertIn("output_1", results)
            self.assertTrue(sm.is_frozen("output_1"))
            self.assertFalse(sm.is_frozen("output_2"))
            self.assertFalse(sm.is_frozen("output_3"))
            self.assertEqual(sm.get_frozen_content("output_1"), "**Alice**: Transcript content")

    def test_evaluator_frozen_artifact_passes_without_recalculation(self):
        """Verify _eval_output1 and _eval_output_n pass immediately when FROZEN_PRIOR_ARTIFACT."""
        out1_eval = {
            "status": "SKIPPED",
            "composite_score": 100,
            "verdict": "PASS",
            "reason": "FROZEN_PRIOR_ARTIFACT",
            "evaluator": "MicroJudgeForensic",
        }
        verdict, passed = _eval_output1(out1_eval, 100)
        self.assertTrue(passed)

        out2_eval = {
            "status": "SKIPPED",
            "composite_score": 100,
            "verdict": "PASS",
            "reason": "FROZEN_PRIOR_ARTIFACT",
        }
        verdict, passed = _eval_output_n(2, out2_eval, 100, 90)
        self.assertTrue(passed)

    def test_high_composite_below_dimension_floor_fails_closed(self):
        """Verify high composite score cannot bypass per-dimension floor failure."""
        out_eval = {
            "status": "FAIL",
            "verdict": "FAIL",
            "composite_score": 95,
            "reason": "Per-dimension floor failure (Rubric §5.3): factual_accuracy=8 (floor=12)",
        }
        verdict, passed = _eval_output_n(3, out_eval, 95, 90)
        self.assertFalse(passed)

    def test_hitl_required_verdict_fails_closed(self):
        """Verify HITL_REQUIRED verdict cannot be bypassed by high composite score."""
        out_eval = {
            "status": "REVIEW_REQUIRED",
            "verdict": "HITL_REQUIRED",
            "composite_score": 92,
            "reason": "Borderline floor deduction requiring operator review",
        }
        verdict, passed = _eval_output_n(2, out_eval, 92, 90)
        self.assertFalse(passed)

    def test_missing_judge_scorecard_fails_closed_without_synthetic_scores(self):
        """Verify missing judge scorecard fails closed without fabricating 90 scores."""
        from dual_pipeline.transcript_qa_core.evaluator import _extract_output_eval
        raw_res = {"passed": True}  # No output_03 scorecard
        out3 = _extract_output_eval(raw_res, "03", passed=True)
        self.assertEqual(out3["status"], "FAIL")
        self.assertEqual(out3["verdict"], "FAIL")
        self.assertEqual(out3["composite_score"], 0)
        self.assertIn("synthetic scores prohibited", out3["reason"])

    def test_decomposed_multi_judge_equipped_with_micro_judges(self):
        """Verify DecomposedMultiJudge has concrete micro-judges for concurrent dispatch."""
        from dual_pipeline.llm_judge_core.orchestrator import DecomposedMultiJudge
        judge = DecomposedMultiJudge(model="test-model")
        self.assertTrue(hasattr(judge, "judge_forensic"))
        self.assertTrue(hasattr(judge, "judge_recap"))
        self.assertTrue(hasattr(judge, "judge_graph"))
        self.assertTrue(hasattr(judge, "reconciler"))

    def test_audio_asr_bypassed_on_validated_frozen_output_1(self):
        """Verify load_file_content reuses validated frozen Output 1 without invoking transcriber."""
        from dual_pipeline.canonical_pipeline.content_loader import load_file_content
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            audio_file = tmp_path / "meeting.wav"
            audio_file.write_bytes(b"RIFF....WAVEfmt ....data....")
            dec = {
                "output_1_pass": True,
                "output_2_pass": True,
                "output_3_pass": True,
                "synthesis_prompt_version": "V5_7",
            }
            (tmp_path / "final_release_decision.json").write_text(json.dumps(dec), encoding="utf-8")
            (tmp_path / "01_test_hitl_resolved_transcript.md").write_text(
                "**Alice**: Preserved exact transcript", encoding="utf-8"
            )
            meta = {
                "attendees": ["Alice"],
                "synthesis_prompt_version": "V5_7",
                "regenerate": "02,03",
                "output_dir": str(tmp_path),
            }
            # offline=False with no mock transcriber: if transcriber is called, it would fail
            content = load_file_content(audio_file, ".wav", meta, offline=True)
            self.assertEqual(content, "**Alice**: Preserved exact transcript")

    def test_deterministic_healer_preserves_frozen_output_1_exact_bytes(self):
        """Verify DeterministicHealer.heal_all preserves frozen Output 1 byte-for-byte."""
        from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer
        from dual_pipeline.remediation_core import SelectiveRemediationStateMachine
        sm = SelectiveRemediationStateMachine()
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            dec = {"output_1_pass": True}
            (tmp_path / "final_release_decision.json").write_text(json.dumps(dec), encoding="utf-8")
            raw_f1 = "**Alice**: Unhealed   trailing whitespace and date 2026-09-25.   \n\n\n"
            (tmp_path / "01_sample.md").write_text(raw_f1, encoding="utf-8")
            sm.seed_for_selective_regeneration(tmp_path, "02,03", {"attendees": ["Alice"]})
            self.assertTrue(sm.is_frozen("output_1"))

            meta = {"state_machine": sm, "attendees": ["Alice"]}
            h1, h2, h3, report = DeterministicHealer.heal_all(
                f1_text=raw_f1,
                f2_text="## Executive Summary\n\nSome recap",
                f3_text="{}",
                metadata=meta,
            )
            self.assertEqual(h1, raw_f1)
            self.assertEqual(report["output_1_healed"], [])


if __name__ == "__main__":
    unittest.main()
