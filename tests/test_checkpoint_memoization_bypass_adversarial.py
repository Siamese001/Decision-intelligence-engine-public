"""
tests/test_checkpoint_memoization_bypass_adversarial.py

Adversarial and boundary test suite verifying stage seal precedence,
short-circuit failure recovery, stale manifest reconciliation, and
healed Output 01 memoization across resume attempts.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.canonical_pipeline.checkpoint_memoizer import (
    _find_passing_checkpoint_file,
    _is_decision_passing,
    _is_decision_valid_and_matching,
    _load_attempt_metadata,
    _read_deployed_checkpoint,
    memoize_substage_checkpoints,
)
from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.remediation_core.artifact_validator import (
    _check_prior_decision,
    validate_frozen_artifact_gate,
)
from dual_pipeline.remediation_core.stage_sealer import (
    persist_stage_checkpoint_seal,
    verify_stage_checkpoint_seal,
)
from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine


class TestCheckpointMemoizationBypassAdversarial(unittest.TestCase):
    """Adversarial and edge-case testing for stage seal precedence and memoization."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="test_memoize_adv_")
        self.out_p = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_category_1_boundary_payload_empty_and_corrupt_files(self) -> None:
        """Category 1 (Boundary payload): Handle 0-byte or corrupt decisions gracefully without crash."""
        (self.out_p / "final_release_decision.json").write_text("", encoding="utf-8")
        (self.out_p / "stage_seals.json").write_text("{corrupt-json", encoding="utf-8")

        res_ok, err, dec = _check_prior_decision(self.out_p, "output_1")
        self.assertFalse(res_ok)
        self.assertIn("Corrupt", str(err))

        sm = SelectiveRemediationStateMachine(max_attempts=3)
        memo = memoize_substage_checkpoints(self.out_p, sm, {}, CanonicalModality.MEETING_TRANSCRIPT)
        self.assertFalse(memo["output_1"])

    def test_category_2_malformed_input_stale_failed_release_decision_with_valid_seals(self) -> None:
        """Category 2 (Malformed/Stale input): final_release_decision has output_1_pass: False, but stage_seals has seal."""
        healed_content = "# Healed Transcript\n[00:01:00] Speaker 1: Verified text.\n[00:21:59] Amit Ayer: Sounds good. Any." + ("\nBody: dialogue" * 40)
        healed_sha = hashlib.sha256(healed_content.encode("utf-8")).hexdigest()

        # Write stale failed release decision in attempt_1
        att_dir = self.out_p / "attempts" / "attempt_1"
        att_dir.mkdir(parents=True, exist_ok=True)
        (att_dir / "output_1.ckpt.md").write_text(healed_content, encoding="utf-8")

        stale_decision = {
            "status": "FAIL",
            "authoritative_write_authorized": False,
            "output_1_pass": False,
            "output_1_deterministic_gates_pass": True,
            "output_1_llm_score_pass": False,
            "artifact_manifest": {
                "output_1_sha256": "stale_hash_from_pre_healed_run",
            },
        }
        (att_dir / "final_release_decision.json").write_text(json.dumps(stale_decision), encoding="utf-8")

        # Persist stage seal for output_1 in att_dir
        persist_stage_checkpoint_seal(att_dir, "output_1", healed_content)
        ok_seal, _ = verify_stage_checkpoint_seal(att_dir, "output_1")
        self.assertTrue(ok_seal)

        # Verify _load_attempt_metadata overrides stale output_1_pass with stage seal
        meta_dec = _load_attempt_metadata(att_dir / "final_release_decision.json", att_dir / "stage_seals.json")
        self.assertIsNotNone(meta_dec)
        self.assertTrue(meta_dec.get("output_1_pass"))
        self.assertTrue(meta_dec.get("output_1_seal"))
        self.assertEqual(meta_dec.get("output_1_sha256"), healed_sha)

        # Verify _is_decision_passing succeeds
        self.assertTrue(_is_decision_passing(meta_dec, "output_1_pass"))

        # Verify memoization picks up the healed checkpoint
        sm = SelectiveRemediationStateMachine(max_attempts=3)
        memo = memoize_substage_checkpoints(self.out_p, sm, {}, CanonicalModality.MEETING_TRANSCRIPT)
        self.assertTrue(memo["output_1"])
        self.assertTrue(sm.is_frozen("output_1"))
        self.assertEqual(sm.get_frozen_content("output_1"), healed_content)

    def test_category_3_rollback_and_recovery_short_circuit_qa_collapse_healing(self) -> None:
        """Category 3 (Rollback & recovery): Output 1 passed but run short-circuited due to Output 2 deterministic failure."""
        valid_transcript = "# Forensic Transcript\n[00:00:15] Speaker 1: Initial agenda.\n" + ("\nTurn: content" * 50)
        t_sha = hashlib.sha256(valid_transcript.encode("utf-8")).hexdigest()

        # Deployed top-level files
        t_file = self.out_p / "01_Test_Meeting_09-14-2026_0300PM_0330PM_ET_hitl_resolved_transcript.md"
        t_file.write_text(valid_transcript, encoding="utf-8")

        # Top-level final_release_decision from short circuit (eval_cache)
        short_circuit_decision = {
            "status": "FAIL",
            "short_circuit": True,
            "short_circuit_reason": "DETERMINISTIC_QA_COLLAPSE",
            "output_1_deterministic_gates_pass": True,
            "output_1_llm_score_pass": False,
            "output_1_pass": False,
            "output_2_pass": False,
            "output_3_pass": False,
        }
        (self.out_p / "final_release_decision.json").write_text(json.dumps(short_circuit_decision), encoding="utf-8")

        # Persist stage seal for output_1 in top-level output_p
        persist_stage_checkpoint_seal(self.out_p, "output_1", valid_transcript)

        # Validate frozen artifact gate passes
        res = validate_frozen_artifact_gate(self.out_p, "output_1", {})
        self.assertTrue(res.valid, f"Validation failed: {res.reason}")

        # Verify _read_deployed_checkpoint succeeds
        read_back = _read_deployed_checkpoint(
            self.out_p,
            "01_*_hitl_resolved_transcript.md",
            "output_1_pass",
        )
        self.assertEqual(read_back, valid_transcript)

    def test_category_4_exact_production_regression_mitchell_kannan_incident(self) -> None:
        """Category 4 (Exact production regression): Capped 87/100 attempt 2 healed to 95+ and frozen on --resume."""
        healed_t = (
            "# NEA Candidate Interview Prep & Debrief -- Kannan Chandraprakash\n"
            "**Date:** 09/14/2026\n"
            "**Participants:** Drew Oakley, Cameron Mercer, Amit Ayer, Tyler McCord, Kannan Chandraprakash.\n\n"
            "[00:21:59] **Amit Ayer:** Sounds good. Any.\n\n"
            "[00:21:59] **Kannan Chandraprakash:** That was, I was trying to look for the link.\n"
        ) + ("\nUtterance: valid reference dialogue" * 60)
        h_sha = hashlib.sha256(healed_t.encode("utf-8")).hexdigest()

        # Deployed artifact in output_p
        cand_p = self.out_p / "01_NEA_CI_Prep_Kannan_Debrief_09-14-2026_0300PM_0330PM_ET_hitl_resolved_transcript.md"
        cand_p.write_text(healed_t, encoding="utf-8")

        # Attempt 2 failed with 87/100
        att_dir = self.out_p / "attempts" / "attempt_2"
        att_dir.mkdir(parents=True, exist_ok=True)
        (att_dir / "output_1.ckpt.md").write_text(healed_t, encoding="utf-8")

        attempt_decision = {
            "attempt": 2,
            "status": "FAIL",
            "authoritative_write_authorized": False,
            "output_1_pass": False,
            "output_1_deterministic_gates_pass": True,
            "output_1_llm_score_pass": False,
            "artifact_manifest": {
                "output_1_sha256": h_sha,
                "decision_seal_sha256": "sample_decision_seal",
            },
        }
        (att_dir / "final_release_decision.json").write_text(json.dumps(attempt_decision), encoding="utf-8")
        persist_stage_checkpoint_seal(att_dir, "output_1", healed_t)

        sm = SelectiveRemediationStateMachine(max_attempts=3, checkpoint_dir=self.out_p)
        metadata = {
            "project": "nea",
            "meeting_name": "Meeting_1_Kannan_Debrief",
            "resume": True,
        }
        memo = memoize_substage_checkpoints(self.out_p, sm, metadata, CanonicalModality.MEETING_TRANSCRIPT)

        self.assertTrue(memo["output_1"])
        self.assertTrue(sm.is_frozen("output_1"))
        self.assertEqual(sm.get_frozen_content("output_1"), healed_t)


if __name__ == "__main__":
    unittest.main()
