#!/usr/bin/env python3
"""Unit test suite for cryptographic decision seals and TOCTOU tamper-proofing."""
import unittest

from dual_pipeline.release_policy import (
    build_artifact_manifest,
    verify_decision_seal,
)
from dual_pipeline.transcript_qa_core.evaluator import evaluate_judge_and_release


class TestCryptographicDecisionSeal(unittest.TestCase):
    def setUp(self) -> None:
        self.f1_text = "### [00:00:00]\n\n**Alex Rivera:** Initial discussion."
        self.f2_text = "# Executive Recap\n\n**Date:** 08/28/2026\n\nDiscussion recap."
        self.f3_text = '{"schema_version": "5.7", "graph_mutations": {"entity_upserts": [{"id": "person:mitchell_simmons"}]}}'
        self.model = "gpt-5.6-luna"
        self.effort = "medium"

    def test_build_and_verify_decision_seal_valid(self) -> None:
        manifest = build_artifact_manifest(
            f1_text=self.f1_text,
            f2_text=self.f2_text,
            f3_text=self.f3_text,
            model=self.model,
            reasoning_effort=self.effort,
            judge_eval_digest="test_digest_12345",
        )
        self.assertIn("decision_seal_sha256", manifest)
        self.assertEqual(len(manifest["decision_seal_sha256"]), 64)

        decision = {"status": "PASS", "authoritative_write_authorized": True, "artifact_manifest": manifest}
        valid, errors = verify_decision_seal(decision, self.f1_text, self.f2_text, self.f3_text)
        self.assertTrue(valid, f"Verification failed with errors: {errors}")
        self.assertEqual(len(errors), 0)

    def test_verify_decision_seal_fails_on_output1_tampering(self) -> None:
        manifest = build_artifact_manifest(
            f1_text=self.f1_text,
            f2_text=self.f2_text,
            f3_text=self.f3_text,
            model=self.model,
            reasoning_effort=self.effort,
        )
        decision = {"status": "PASS", "authoritative_write_authorized": True, "artifact_manifest": manifest}

        # Tamper with f1_text
        tampered_f1 = self.f1_text + "\n[TAMPERED CONTENT]"
        valid, errors = verify_decision_seal(decision, tampered_f1, self.f2_text, self.f3_text)
        self.assertFalse(valid)
        self.assertTrue(any("Output 1 content drifted" in e for e in errors))

    def test_verify_decision_seal_fails_on_output2_tampering(self) -> None:
        manifest = build_artifact_manifest(
            f1_text=self.f1_text,
            f2_text=self.f2_text,
            f3_text=self.f3_text,
            model=self.model,
            reasoning_effort=self.effort,
        )
        decision = {"status": "PASS", "authoritative_write_authorized": True, "artifact_manifest": manifest}

        # Tamper with f2_text
        tampered_f2 = self.f2_text + "\nTampered notes."
        valid, errors = verify_decision_seal(decision, self.f1_text, tampered_f2, self.f3_text)
        self.assertFalse(valid)
        self.assertTrue(any("Output 2 content drifted" in e for e in errors))

    def test_verify_decision_seal_fails_on_output3_tampering(self) -> None:
        manifest = build_artifact_manifest(
            f1_text=self.f1_text,
            f2_text=self.f2_text,
            f3_text=self.f3_text,
            model=self.model,
            reasoning_effort=self.effort,
        )
        decision = {"status": "PASS", "authoritative_write_authorized": True, "artifact_manifest": manifest}

        # Tamper with f3_text
        tampered_f3 = '{"schema_version": "5.7", "graph_mutations": {"entity_upserts": [{"id": "person:rogue_node"}]}}'
        valid, errors = verify_decision_seal(decision, self.f1_text, self.f2_text, tampered_f3)
        self.assertFalse(valid)
        self.assertTrue(any("Output 3 content drifted" in e for e in errors))

    def test_verify_decision_seal_fails_on_seal_tampering(self) -> None:
        manifest = build_artifact_manifest(
            f1_text=self.f1_text,
            f2_text=self.f2_text,
            f3_text=self.f3_text,
            model=self.model,
            reasoning_effort=self.effort,
        )
        # Tamper with the seal
        manifest["decision_seal_sha256"] = "0" * 64
        decision = {"status": "PASS", "authoritative_write_authorized": True, "artifact_manifest": manifest}

        valid, errors = verify_decision_seal(decision, self.f1_text, self.f2_text, self.f3_text)
        self.assertFalse(valid)
        self.assertTrue(any("Decision seal mismatch" in e for e in errors))

    def test_evaluate_judge_and_release_embeds_verified_manifest(self) -> None:
        qa_rep, judge_rep, decision = evaluate_judge_and_release(
            f1_text=self.f1_text,
            f2_text=self.f2_text,
            f3_text=self.f3_text,
            mock=True,
            model=self.model,
            reasoning_effort=self.effort,
        )
        self.assertIn("artifact_manifest", decision)
        manifest = decision["artifact_manifest"]
        self.assertEqual(manifest["evaluator_model"], self.model)
        self.assertEqual(manifest["reasoning_effort"], self.effort)

        valid, errors = verify_decision_seal(decision, self.f1_text, self.f2_text, self.f3_text)
        self.assertTrue(valid, f"Verification failed: {errors}")


if __name__ == "__main__":
    unittest.main()
