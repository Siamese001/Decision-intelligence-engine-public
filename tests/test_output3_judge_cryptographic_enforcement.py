"""
tests/test_output3_judge_cryptographic_enforcement.py

Cryptographic and architectural red-team tests verifying that Output 03
cannot mutate the knowledge graph without a valid, un-tampered, and sealed
LLM-as-a-Judge score (>= 90/100) and cryptographic receipt digest.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.cli.handlers import _intake_publish
from dual_pipeline.document_pipeline_core.evaluator import evaluate_document_judge_and_release
from dual_pipeline.release_policy import (
    build_artifact_manifest,
    compute_release_evaluation_digest,
    verify_decision_seal,
    verify_release_evaluation_digest,
)


class Output3JudgeEnforcementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="test_output3_judge_")
        self.dir_path = Path(self.temp_dir.name)

        # Create valid source and Deliverable 03
        self.source_text = "Role description for PartnerAdvisory Agile delivery."
        self.f3_content = json.dumps({
            "elements": [
                {
                    "id": "role_partner_advisory_product_owner",
                    "type": "role",
                    "name": "Product Owner",
                }
            ],
            "relations": [],
        }, indent=2)

        self.f3_path = self.dir_path / "03_test_canonical_signal_projection.json"
        self.f3_path.write_text(self.f3_content, encoding="utf-8")

        # Create evidence-envelope intake package
        self.pkg_path = self.dir_path / "intake_package.json"
        self.pkg_data = {
            "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            "schema_version": "1.0.0",
            "meeting": {"title": "Test PartnerAdvisory Meeting"},
        }
        self.pkg_path.write_text(json.dumps(self.pkg_data, indent=2), encoding="utf-8")

        # Build valid judge report
        self.judge_report = {
            "rubric_version": "2.0",
            "mode": "document",
            "evaluator_model": "gpt-5.6-luna",
            "reasoning_effort": "high",
            "status": "PASS",
            "output_3_evaluation": {
                "composite_score": 94,
                "verdict": "PASS",
                "forensic_rationale": "High quality role grounding.",
            },
        }
        self.eval_digest = compute_release_evaluation_digest(self.judge_report)
        self.judge_report["evaluation_receipt_digest"] = self.eval_digest
        self.judge_path = self.dir_path / "llm_judge_evaluation.json"
        self.judge_path.write_text(json.dumps(self.judge_report, indent=2), encoding="utf-8")

        # Build valid artifact manifest & decision
        self.manifest = build_artifact_manifest(
            f1_text="",
            f2_text="",
            f3_text=self.f3_content,
            model="gpt-5.6-luna",
            reasoning_effort="high",
            judge_eval_digest=self.eval_digest,
        )
        self.final_decision = {
            "mode": "document",
            "authoritative_write_authorized": True,
            "output_3_llm_score_pass": True,
            "output_3_pass": True,
            "final_pass": True,
            "status": "PASS",
            "artifact_manifest": self.manifest,
        }
        self.decision_path = self.dir_path / "final_release_decision.json"
        self.decision_path.write_text(json.dumps(self.final_decision, indent=2), encoding="utf-8")

        self.publish_args = argparse.Namespace(
            package=[self.pkg_path],
            reviewer="operator",
            rationale="Test publication",
            directive_id="DIR-TEST-001",
            scope="Testing",
            runtime_root=self.dir_path / "runtime",
            graph=self.dir_path / "test_graph.sqlite",
            batch_manifest=None,
            a2a_finalization_receipt=None,
            allow_unjudged_package=False,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_verify_release_evaluation_digest_helper(self) -> None:
        """Verify the helper accurately validates unmodified reports and catches tampered content."""
        self.assertTrue(verify_release_evaluation_digest(self.judge_report))

        tampered = dict(self.judge_report)
        tampered["output_3_evaluation"] = {"composite_score": 50, "verdict": "FAIL"}
        self.assertFalse(verify_release_evaluation_digest(tampered))

    def test_document_pipeline_evaluator_emits_manifest_and_seal(self) -> None:
        """Verify that evaluate_document_judge_and_release emits a valid decision seal."""
        qa, judge, decision = evaluate_document_judge_and_release(
            f3_text=self.f3_content,
            source_text=self.source_text,
            mock=True,
        )
        self.assertIn("artifact_manifest", decision)
        manifest = decision["artifact_manifest"]
        self.assertIn("decision_seal_sha256", manifest)

        active_f3 = qa.get("healed_artifacts", {}).get("output_3") or self.f3_content
        valid_seal, errors = verify_decision_seal(
            decision,
            f1_text=self.source_text,
            f2_text="",
            f3_text=active_f3,
        )
        self.assertTrue(valid_seal, f"Seal verification failed: {errors}")

    def test_missing_decision_file_fails_closed(self) -> None:
        """An evidence package without final_release_decision.json must fail closed."""
        self.decision_path.unlink()
        with self.assertRaises(RuntimeError) as ctx:
            _intake_publish(self.publish_args)
        self.assertIn("Missing mandatory release authority receipt", str(ctx.exception))

    def test_missing_decision_file_allowed_with_admin_override(self) -> None:
        """When allow_unjudged_package=True, missing decision does not trigger the gate exception."""
        self.decision_path.unlink()
        self.publish_args.allow_unjudged_package = True
        # Mock downstream ReleaseManager to verify it bypassed the early gate
        with patch("dual_pipeline.cli.handlers.ReleaseManager") as mock_rm:
            mock_inst = MagicMock()
            mock_inst.active_path.is_file.return_value = True
            mock_rm.return_value = mock_inst
            with patch("dual_pipeline.cli.handlers._backend"), \
                 patch("dual_pipeline.cli.handlers._authorization"), \
                 patch("dual_pipeline.cli.handlers._bind_model_authorization"), \
                 patch("dual_pipeline.cli.handlers._assert_backend_matches_pair"):
                try:
                    _intake_publish(self.publish_args)
                except Exception as e:
                    # Gate check passed; subsequent downstream logic may raise
                    self.assertNotIn("Missing mandatory release authority receipt", str(e))

    def test_authoritative_write_authorized_false_fails_closed(self) -> None:
        """authoritative_write_authorized=False must be rejected immediately."""
        self.final_decision["authoritative_write_authorized"] = False
        self.decision_path.write_text(json.dumps(self.final_decision), encoding="utf-8")
        with self.assertRaises(RuntimeError) as ctx:
            _intake_publish(self.publish_args)
        self.assertIn("Authoritative release authorization rejected", str(ctx.exception))

    def test_output_3_llm_score_pass_false_fails_closed(self) -> None:
        """output_3_llm_score_pass=False must be rejected even if authoritative_write_authorized=True."""
        self.final_decision["output_3_llm_score_pass"] = False
        self.decision_path.write_text(json.dumps(self.final_decision), encoding="utf-8")
        with self.assertRaises(RuntimeError) as ctx:
            _intake_publish(self.publish_args)
        self.assertIn("Output 3 LLM Judge score gate rejected", str(ctx.exception))

    def test_missing_judge_report_fails_closed(self) -> None:
        """Missing llm_judge_evaluation.json must fail closed for evidence packages."""
        self.judge_path.unlink()
        with self.assertRaises(RuntimeError) as ctx:
            _intake_publish(self.publish_args)
        self.assertIn("Missing mandatory LLM Judge evaluation report", str(ctx.exception))

    def test_tampered_evaluation_receipt_digest_fails_closed(self) -> None:
        """Tampered evaluation content with stale digest must fail closed."""
        self.judge_report["output_3_evaluation"]["composite_score"] = 99
        # Do not recompute digest -> mismatch
        self.judge_path.write_text(json.dumps(self.judge_report), encoding="utf-8")
        with self.assertRaises(RuntimeError) as ctx:
            _intake_publish(self.publish_args)
        self.assertIn("Cryptographic evaluation receipt digest verification failed", str(ctx.exception))

    def test_sub_threshold_score_fails_closed(self) -> None:
        """A score below 90 must fail closed even if digest was recomputed."""
        self.judge_report["output_3_evaluation"]["composite_score"] = 89
        self.judge_report["output_3_evaluation"]["verdict"] = "FAIL"
        self.judge_report["evaluation_receipt_digest"] = compute_release_evaluation_digest(
            {k: v for k, v in self.judge_report.items() if k != "evaluation_receipt_digest"}
        )
        self.judge_path.write_text(json.dumps(self.judge_report), encoding="utf-8")
        with self.assertRaises(RuntimeError) as ctx:
            _intake_publish(self.publish_args)
        self.assertIn("does not meet production floor", str(ctx.exception))

    def test_content_drift_after_sealing_fails_closed(self) -> None:
        """If Output 03 is modified after sealing, decision seal verification must fail closed."""
        tampered_f3 = json.dumps({"elements": [], "relations": []})
        self.f3_path.write_text(tampered_f3, encoding="utf-8")
        with self.assertRaises(RuntimeError) as ctx:
            _intake_publish(self.publish_args)
        self.assertIn("Cryptographic decision seal verification failed", str(ctx.exception))
        self.assertIn("Output 3 content drifted", str(ctx.exception))

    @patch("dual_pipeline.document_pipeline_core.evaluator._execute_document_judge")
    def test_document_pipeline_terminal_failure_on_bad_score(self, mock_judge: MagicMock) -> None:
        """DocumentPipeline.run() must fail closed with RuntimeError if judge fails all attempts."""
        from dual_pipeline.document_pipeline import DocumentPipeline

        mock_judge.return_value = (
            {
                "composite_score": 65,
                "verdict": "FAIL",
                "output_3_evaluation": {"composite_score": 65, "verdict": "FAIL"},
            },
            False,
            65,
        )

        source_file = self.dir_path / "source.txt"
        source_file.write_text(self.source_text, encoding="utf-8")

        pipeline = DocumentPipeline(
            provider_type="offline",
        )
        with self.assertRaises(RuntimeError) as ctx:
            pipeline.run(
                input_path=str(source_file),
                metadata={"title": "Test Failing Doc"},
                output_dir=str(self.dir_path / "doc_out"),
                max_judge_retries=0,
                mutate_graph=False,
            )
        self.assertIn("Mandatory LLM Judge evaluation failed for Output 03", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
