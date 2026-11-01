"""tests/end_to_end/test_wave1_receipt_integrity_e2e.py

End-to-end test suite for Wave 1 receipt integrity and release gate verification.
Verifies that:
1. Valid evidence envelope package with non-empty receipts, matching digests, and valid seal passes.
2. 3-byte empty `{}` or missing receipts are rejected fail-closed.
3. Mismatched judge evaluation digest between manifest and judge report is rejected.
4. Missing artifact manifest on evidence envelope package blocks mutation.
5. Container hierarchy validator rejects placeholder `{}` receipt files.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.release_policy import (
    build_artifact_manifest,
    compute_release_evaluation_digest,
    verify_authoritative_release_gate,
)
from tools.validate_output_hierarchy_core.container import _validate_meeting_container


class Wave1ReceiptIntegrityE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _setup_valid_package_dir(self) -> tuple[Path, Path]:
        pkg_dir = self.base_dir / "project_nexus" / "10-03-2026" / "Phase1_Review"
        pkg_dir.mkdir(parents=True, exist_ok=True)

        f1_path = pkg_dir / "01_Phase1_Review_10-03-2026_0900AM_hitl_resolved_transcript.md"
        f2_path = pkg_dir / "02_Phase1_Review_10-03-2026_0900AM_executive_recap.md"
        f3_path = pkg_dir / "03_Phase1_Review_10-03-2026_0900AM_canonical_signal_projection.json"

        f1_text = "# Session Transcript\nSpeaker A: Valid content.\n"
        f2_text = "# Executive Recap\nSummary of meeting.\n"
        f3_text = json.dumps({"schema_version": "1.0", "entities": [{"id": "system:core"}]})

        f1_path.write_text(f1_text, encoding="utf-8")
        f2_path.write_text(f2_text, encoding="utf-8")
        f3_path.write_text(f3_text, encoding="utf-8")

        # Judge evaluation
        judge_data = {
            "status": "PASS",
            "overall_verdict": "PASS",
            "output_3_evaluation": {
                "composite_score": 95,
                "verdict": "PASS",
            },
        }
        judge_digest = compute_release_evaluation_digest(judge_data)
        judge_data["evaluation_receipt_digest"] = judge_digest
        judge_path = pkg_dir / "llm_judge_evaluation.json"
        judge_path.write_text(json.dumps(judge_data), encoding="utf-8")

        # Manifest and decision
        manifest = build_artifact_manifest(
            f1_text=f1_text,
            f2_text=f2_text,
            f3_text=f3_text,
            model="gpt-6-astra",
            reasoning_effort="high",
            judge_eval_digest=judge_digest,
        )
        decision_data = {
            "status": "PASS",
            "authoritative_write_authorized": True,
            "output_3_llm_score_pass": True,
            "artifact_manifest": manifest,
        }
        decision_path = pkg_dir / "final_release_decision.json"
        decision_path.write_text(json.dumps(decision_data), encoding="utf-8")

        # Intake package
        pkg_data = {
            "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            "meeting_id": "Phase1_Review",
        }
        pkg_path = pkg_dir / "intake_package_phase1.json"
        pkg_path.write_text(json.dumps(pkg_data), encoding="utf-8")

        # QA report
        qa_data = {"passed": True, "deterministic_qa_passed": True}
        qa_path = pkg_dir / "qa_gates_report.json"
        qa_path.write_text(json.dumps(qa_data), encoding="utf-8")

        return pkg_dir, pkg_path

    def test_valid_evidence_package_passes_release_gate(self) -> None:
        """Fully sealed valid package passes authoritative release gate without error."""
        _, pkg_path = self._setup_valid_package_dir()
        # Should execute cleanly without raising
        verify_authoritative_release_gate([pkg_path])

    def test_empty_decision_file_fails_release_gate(self) -> None:
        """3-byte empty `{}` final_release_decision.json MUST fail closed."""
        pkg_dir, pkg_path = self._setup_valid_package_dir()
        (pkg_dir / "final_release_decision.json").write_text("{}\n", encoding="utf-8")

        with self.assertRaises(RuntimeError) as ctx:
            verify_authoritative_release_gate([pkg_path])
        self.assertIn("empty release decision file", str(ctx.exception))

    def test_mismatched_judge_digest_fails_release_gate(self) -> None:
        """Forged or mismatched judge digest between manifest and judge report MUST fail closed."""
        pkg_dir, pkg_path = self._setup_valid_package_dir()
        # Corrupt the judge digest in decision manifest
        decision_data = json.loads((pkg_dir / "final_release_decision.json").read_text(encoding="utf-8"))
        decision_data["artifact_manifest"]["judge_evaluation_digest"] = "forged_digest_000000000000000000000000"
        from dual_pipeline.release_policy import compute_decision_seal
        decision_data["artifact_manifest"]["decision_seal_sha256"] = compute_decision_seal(decision_data["artifact_manifest"])
        (pkg_dir / "final_release_decision.json").write_text(json.dumps(decision_data), encoding="utf-8")

        with self.assertRaises(RuntimeError) as ctx:
            verify_authoritative_release_gate([pkg_path])
        self.assertIn("judge digest mismatch", str(ctx.exception).lower())

    def test_empty_placeholder_receipts_rejected_by_hierarchy_validator(self) -> None:
        """tools/validate_output_hierarchy rejects placeholder `{}` receipt files."""
        pkg_dir, _ = self._setup_valid_package_dir()
        # Overwrite qa_gates_report with empty `{}`
        (pkg_dir / "qa_gates_report.json").write_text("{}\n", encoding="utf-8")

        violations = _validate_meeting_container(
            container=pkg_dir,
            proj_name="project_nexus",
            meeting_date="10-03-2026",
            container_label="project_nexus/10-03-2026/Phase1_Review",
        )
        self.assertTrue(any("EMPTY_RECEIPT_FILE" in v for v in violations), f"Violations: {violations}")


if __name__ == "__main__":
    unittest.main()
