"""
tests/adversarial/test_selective_regeneration_adversarial.py

Adversarial and boundary stress tests for selective output regeneration:
- Malformed deliverable strings and delimiter edge cases
- Corrupt and truncated JSON release decisions
- Disjoint speaker attacks
- Missing artifact files with positive decision claims
- Invariant enforcement against bypassing frozen gates
Compatible with both pytest and unittest.
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


class TestSelectiveRegenerationAdversarial(unittest.TestCase):
    """Adversarial security and edge-case test suite for selective regeneration."""

    def test_adversarial_deliverable_specs(self):
        """Verify malformed or out-of-range deliverable specs fail closed."""
        bad_specs = [
            "04",
            "-1",
            "00",
            "output_4",
            "random_text",
            "01,02,99",
        ]
        for spec in bad_specs:
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                parse_regenerate_spec(spec)

        # Empty and comma-only specs fallback cleanly to all outputs
        self.assertEqual(parse_regenerate_spec(",,"), {"output_1", "output_2", "output_3"})
        self.assertEqual(parse_regenerate_spec("  "), {"output_1", "output_2", "output_3"})

    def test_adversarial_corrupt_json_decision(self):
        """Verify corrupted final_release_decision.json fails closed without crashing."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            (tmp_path / "final_release_decision.json").write_text("{corrupt: json truncated...", encoding="utf-8")
            (tmp_path / "01_sample.md").write_text("sample content", encoding="utf-8")

            res = validate_frozen_artifact_gate(tmp_path, "output_1", {})
            self.assertFalse(res.valid)
            self.assertIn("Corrupt final_release_decision.json", res.reason)

    def test_adversarial_missing_artifact_file_with_passing_decision(self):
        """Verify that a claiming pass in final_release_decision fails if artifact file is missing."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            dec = {"output_1_pass": True}
            (tmp_path / "final_release_decision.json").write_text(json.dumps(dec), encoding="utf-8")
            # Notice: No 01_*.md file written!

            res = validate_frozen_artifact_gate(tmp_path, "output_1", {})
            self.assertFalse(res.valid)
            self.assertIn("not found", res.reason)

    def test_adversarial_disjoint_speakers_attack(self):
        """Verify adversarial speaker spoofing is rejected by frozen gate."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            dec = {"output_1_pass": True}
            (tmp_path / "final_release_decision.json").write_text(json.dumps(dec), encoding="utf-8")
            (tmp_path / "01_malicious_transcript.md").write_text(
                "| 00:00:10 | Mallory (Attacker) |\n| 00:00:20 | Eve (Eavesdropper) |",
                encoding="utf-8",
            )

            metadata = {"attendees": ["Alice Smith", "Bob Jones"]}
            res = validate_frozen_artifact_gate(tmp_path, "output_1", metadata)
            self.assertFalse(res.valid)
            self.assertIn("Speaker roster mismatch", res.reason)

    def test_adversarial_seed_raises_on_invalid_frozen_candidate(self):
        """Verify state machine raises ValueError if an excluded artifact fails validation."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            dec = {"output_1_pass": False, "output_2_pass": True}
            (tmp_path / "final_release_decision.json").write_text(json.dumps(dec), encoding="utf-8")
            (tmp_path / "01_failed.md").write_text("failed transcript", encoding="utf-8")

            sm = SelectiveRemediationStateMachine(max_attempts=2)
            with self.assertRaises(ValueError):
                sm.seed_for_selective_regeneration(tmp_path, "02", {})


    def test_adversarial_cross_meeting_pinned_evaluation_leak_fails_closed(self):
        """Verify _resolve_pinned_evaluation does not fall back to hardcoded PartnerAdvisory meetings."""
        from dual_pipeline.canonical_pipeline.evaluator import _resolve_pinned_evaluation
        meta = {
            "project": "project_lens",
            # No meeting_slug, filename_slug, date_filename, date_dirname provided
        }
        res = _resolve_pinned_evaluation(meta, attempt=1)
        self.assertIsNone(res)

    def test_adversarial_failing_cross_artifact_reconciliation_rejects_publication(self):
        """Verify failing cross-artifact reconciliation blocks authoritative write authorization."""
        from dual_pipeline.transcript_qa_core.evaluator import _build_final_decision
        qa_report = {
            "output_01_forensic_transcript": {"passed": True, "status": "PASS"},
            "output_02_executive_recap": {"passed": True, "status": "PASS"},
            "output_03_canonical_signal_projection": {"passed": True, "status": "PASS"},
            "cross_artifact_consistency": {"status": "FAIL"},  # Failing cross consistency
        }
        decision = _build_final_decision(
            qa_report=qa_report,
            j1_pass=True,
            j2_pass=True,
            j3_pass=True,
            recon_pass=False,  # Failing reconciliation
            is_advisory=False,
        )
        self.assertFalse(decision["final_pass"])
        self.assertFalse(decision["authoritative_write_authorized"])
        self.assertEqual(decision["status"], "FAIL")

    def test_adversarial_high_composite_with_per_dimension_floor_failure_blocks_release(self):
        """Verify _eval_pass_flags rejects release if an output failed dimension floor."""
        from dual_pipeline.transcript_qa_core.evaluator import _eval_pass_flags
        out1_eval = {
            "verdict": "PASS",
            "composite_score": 95,
            "status": "COMPLETE",
            "dimension_1": 24,
            "dimension_2": 24,
            "dimension_3": 24,
            "dimension_4": 23,
        }
        out2_eval = {
            "verdict": "PASS",
            "composite_score": 95,
            "status": "COMPLETE",
            "section_scores": {
                "header_and_overview": 10,
                "key_updates_technical_substance": 34,
                "conditional_extensions": 18,
                "appendix_stakeholder_perspectives": 33,
            },
        }
        out3_eval = {
            "verdict": "FAIL",
            "composite_score": 96,
            "status": "FAIL",
            "dimension_1": 15,
            "dimension_2": 15,
            "dimension_3": 20,
            "dimension_4": 15,
            "dimension_5": 15,
            "dimension_6": 16,
            "reason": "Per-dimension floor failure (Rubric §5.3): graph_provenance=5 (floor=12)",
        }
        recon = {"passed": True, "status": "SUCCESS"}
        j1, j2, j3, judge_passed, is_advisory = _eval_pass_flags(out1_eval, out2_eval, out3_eval, recon)
        self.assertTrue(j1)
        self.assertTrue(j2)
        self.assertFalse(j3)
        self.assertFalse(judge_passed)
        self.assertFalse(is_advisory)


if __name__ == "__main__":
    unittest.main()
