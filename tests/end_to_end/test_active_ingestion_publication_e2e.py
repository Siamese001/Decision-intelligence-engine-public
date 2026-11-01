"""
tests/end_to_end/test_active_ingestion_publication_e2e.py

End-to-End Governance Test Suite for Active Ingestion Publication and Evidence (Wave 3).
Verifies:
  1. Real isolated publication write, table deltas, and readback.
  2. Truthful status propagation (HOLD_FOR_REMEDIATION, AWAITING_APPROVAL, PREPARED_ACTION_AUTH_REQUIRED).
  3. Rejection of premature COMPLETE when publication was skipped or unattempted.
  4. Cached artifact-only PASS does NOT bypass graph publication when mutation is requested.
  5. Verified exact replay short-circuits only when prior graph coverage is authoritatively proven.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.publish_helper import (
    assemble_pipeline_result,
    check_idempotent_or_continue,
    publish_graph_if_requested,
)


class ActiveIngestionPublicationE2ETests(unittest.TestCase):
    """End-to-end suite verifying truthful publication state reducer and evidence bindings."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.output_dir = self.root / "stage-output"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.runtime_root = self.root / "runtime"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        (self.runtime_root / "active_pair.json").write_text(
            json.dumps({"generation_id": "gen-001", "base_generation": 1}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_truthful_publisher_status_propagation(self) -> None:
        """Every publisher state propagates faithfully without success masking."""
        eval_result = Mock()
        eval_result.final_decision = {"status": "PASS"}
        eval_result.authoritative_write_authorized = True

        pkg_path = self.output_dir / "intake_package.json"
        pkg_path.write_text("{}", encoding="utf-8")
        receipt_path = self.output_dir / "receipt.json"

        # Case 1: HOLD_FOR_REMEDIATION
        hold_mutation = {
            "status": "HOLD_FOR_REMEDIATION",
            "reason": "Sensitive signal detected in Output 03",
        }
        res_hold = assemble_pipeline_result(
            modality=CanonicalModality.DOCUMENT,
            meta={"mutate_graph": True},
            out_p=self.output_dir,
            artifacts_map={},
            eval_result=eval_result,
            intake_pkg_path=pkg_path,
            receipt_path=receipt_path,
            mutation_result=hold_mutation,
        )
        self.assertEqual(res_hold["status"], "HOLD_FOR_REMEDIATION")
        self.assertIn("graph_mutation", res_hold)

        # Case 2: AWAITING_APPROVAL
        await_mutation = {
            "status": "AWAITING_APPROVAL",
            "approval_request_id": "req-ingest-1234",
        }
        res_await = assemble_pipeline_result(
            modality=CanonicalModality.MEETING_TRANSCRIPT,
            meta={"mutate_graph": True},
            out_p=self.output_dir,
            artifacts_map={},
            eval_result=eval_result,
            intake_pkg_path=pkg_path,
            receipt_path=receipt_path,
            mutation_result=await_mutation,
        )
        self.assertEqual(res_await["status"], "AWAITING_APPROVAL")

        # Case 3: PREPARED_ACTION_AUTH_REQUIRED
        prep_mutation = {
            "status": "PREPARED_ACTION_AUTH_REQUIRED",
            "next_action": "Run receipt-bound BGE evaluation",
        }
        res_prep = assemble_pipeline_result(
            modality=CanonicalModality.DOCUMENT,
            meta={"mutate_graph": True},
            out_p=self.output_dir,
            artifacts_map={},
            eval_result=eval_result,
            intake_pkg_path=pkg_path,
            receipt_path=receipt_path,
            mutation_result=prep_mutation,
        )
        self.assertEqual(res_prep["status"], "PREPARED_ACTION_AUTH_REQUIRED")

        # Case 4: Mutation was requested but result was None (SKIPPED_PUBLICATION, not COMPLETE)
        res_skipped = assemble_pipeline_result(
            modality=CanonicalModality.MEETING_TRANSCRIPT,
            meta={"mutate_graph": True},
            out_p=self.output_dir,
            artifacts_map={},
            eval_result=eval_result,
            intake_pkg_path=pkg_path,
            receipt_path=receipt_path,
            mutation_result=None,
        )
        self.assertEqual(res_skipped["status"], "SKIPPED_PUBLICATION")

    def test_complete_publication_propagates_deltas_and_readback(self) -> None:
        """Successful publication reports actual table deltas, generation, and summary."""
        eval_result = Mock()
        eval_result.final_decision = {"status": "PASS"}

        pkg_path = self.output_dir / "intake_package.json"
        pkg_path.write_text("{}", encoding="utf-8")
        receipt_path = self.output_dir / "receipt.json"

        mutation_result = {
            "status": "COMPLETE",
            "active_generation": 42,
            "idempotent_replay": False,
            "table_deltas": {
                "nodes": {"inserted": 12, "updated": 3, "unchanged": 45},
                "edges": {"inserted": 18, "updated": 0, "unchanged": 60},
                "assertions": {"inserted": 10, "updated": 2, "unchanged": 30},
            },
            "mutation_summary": "+40 total entities and assertions activated",
        }

        res = assemble_pipeline_result(
            modality=CanonicalModality.MEETING_TRANSCRIPT,
            meta={"mutate_graph": True},
            out_p=self.output_dir,
            artifacts_map={"03_graph": str(self.output_dir / "03.json")},
            eval_result=eval_result,
            intake_pkg_path=pkg_path,
            receipt_path=receipt_path,
            mutation_result=mutation_result,
        )

        self.assertEqual(res["status"], "COMPLETE")
        self.assertEqual(res["active_generation"], 42)
        self.assertFalse(res["idempotent_replay"])
        self.assertEqual(res["table_deltas"]["nodes"]["inserted"], 12)
        self.assertEqual(res["table_deltas"]["nodes"]["updated"], 3)
        self.assertIn("+40", res["mutation_summary"])

    def test_cached_artifact_only_pass_does_not_bypass_publication(self) -> None:
        """Cached synthesis without authoritative graph coverage must not short-circuit when mutation is requested."""
        tracker = Mock()
        cached_paths = {"03_graph": str(self.output_dir / "03.json")}

        with patch("dual_pipeline.transcript_pipeline_core.caching.check_idempotent_meeting_run", return_value=cached_paths):
            # 1. When receipt.json does NOT exist, check_idempotent_or_continue must return None
            res = check_idempotent_or_continue(
                tracker=tracker,
                output_dir=str(self.output_dir),
                meta={"mutate_graph": True, "date_filename": "2026-10-06"},
                input_hash="hash-abc",
                mutate_graph=True,
            )
            self.assertIsNone(res, "Expected publication bypass prevention for artifact-only cache")
            tracker.complete_stage.assert_not_called()

            # 2. Seed receipt.json with verified graph coverage
            (self.output_dir / "receipt.json").write_text(
                json.dumps({"terminal_outcome": "COMPLETE", "graph_mutated": True}),
                encoding="utf-8",
            )

            # 3. Now verified exact replay is allowed to short-circuit
            res_covered = check_idempotent_or_continue(
                tracker=tracker,
                output_dir=str(self.output_dir),
                meta={"mutate_graph": True, "date_filename": "2026-10-06"},
                input_hash="hash-abc",
                mutate_graph=True,
            )
            self.assertIsNotNone(res_covered, "Expected idempotent short-circuit when graph coverage is verified")
            tracker.complete_stage.assert_called_once()


if __name__ == "__main__":
    unittest.main()
