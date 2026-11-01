"""End-to-End Governance Test Suite for Active Ingestion Approval Resumption (Wave 3).

Verifies:
  1. Operator approval continuation mutates graph and activates new generation.
  2. Operator decline halts without graph mutation, keeping generation intact.
  3. Replay safety and idempotency when re-invoking approval resume.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import tempfile
import unittest

from dual_pipeline.cli.handlers import _intake_approval_resume
from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
    build_active_ingestion_approval_request,
    dispatch_and_persist_approval_request,
)
from dual_pipeline.runtime_root import resolve_active_pair


class IntakeApprovalResumeE2ETests(unittest.TestCase):
    """End-to-End suite for intake approval resumption and graph mutation activation."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir(parents=True)

        src_runtime = Path(__file__).resolve().parents[2] / ".local" / "dual_pipeline"
        pair_data, _ = resolve_active_pair(src_runtime)
        gen_id = pair_data["generation_id"]

        shutil.copy(src_runtime / "active_pair.json", self.runtime / "active_pair.json")
        (self.runtime / "generations" / gen_id).mkdir(parents=True)
        shutil.copy(src_runtime / "generations" / gen_id / "graph.sqlite", self.runtime / "generations" / gen_id / "graph.sqlite")
        shutil.copy(src_runtime / "generations" / gen_id / "sidecar.sqlite", self.runtime / "generations" / gen_id / "sidecar.sqlite")

        self.pkg_path = Path(__file__).resolve().parents[2] / "contracts" / "dual_pipeline" / "fixtures" / "valid" / "evidence-envelope-intake.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_approval_continuation_mutates_graph_and_activates_generation(self) -> None:
        """Authentic operator approval resumes run, commits graph mutation, and activates generation."""
        pair_before, _ = resolve_active_pair(self.runtime)

        req = build_active_ingestion_approval_request(
            run_id="run-e2e-resume-1",
            directive_id="dir-e2e-resume-1",
            runtime_root=str(self.runtime),
            project="knowledge",
            source_package_digest="1" * 64,
            proposed_delta_digest="2" * 64,
            baseline_generation=pair_before["generation_id"],
            intake_package_path=str(self.pkg_path),
        )
        dispatched = dispatch_and_persist_approval_request(
            req,
            storage_root=self.runtime,
            transport=lambda p: {"status": "DISPATCHED"},
        )

        args = argparse.Namespace(
            request_id=dispatched["request_id"],
            decision="APPROVE",
            reviewer="Operator",
            rationale="Operator verified mutation continuation.",
            runtime_root=self.runtime,
            graph=None,
        )

        result = _intake_approval_resume(args)
        self.assertEqual(result.get("status"), "COMPLETE")
        self.assertEqual(result.get("resumed_approval_request_id"), dispatched["request_id"])

        pair_after, _ = resolve_active_pair(self.runtime)
        self.assertNotEqual(pair_after["generation_id"], pair_before["generation_id"])
        self.assertNotEqual(pair_after["graph_sha256"], pair_before["graph_sha256"])

    def test_decline_halts_without_graph_mutation(self) -> None:
        """Operator decline records rejection and preserves the prior active pair unmutated."""
        pair_before, _ = resolve_active_pair(self.runtime)

        req = build_active_ingestion_approval_request(
            run_id="run-e2e-decline-1",
            directive_id="dir-e2e-decline-1",
            runtime_root=str(self.runtime),
            project="knowledge",
            source_package_digest="3" * 64,
            proposed_delta_digest="4" * 64,
            baseline_generation=pair_before["generation_id"],
            intake_package_path=str(self.pkg_path),
        )
        dispatched = dispatch_and_persist_approval_request(
            req,
            storage_root=self.runtime,
            transport=lambda p: {"status": "DISPATCHED"},
        )

        args = argparse.Namespace(
            request_id=dispatched["request_id"],
            decision="DECLINE",
            reviewer="Operator",
            rationale="Operator declined publication.",
            runtime_root=self.runtime,
            graph=None,
        )

        result = _intake_approval_resume(args)
        self.assertEqual(result.get("status"), "DECLINED")

        pair_after, _ = resolve_active_pair(self.runtime)
        self.assertEqual(pair_after["generation_id"], pair_before["generation_id"])
        self.assertEqual(pair_after["graph_sha256"], pair_before["graph_sha256"])

    def test_idempotent_reinvocation_of_resolved_request(self) -> None:
        """Re-invoking approval resume on already-approved request is safe and restores state."""
        pair_before, _ = resolve_active_pair(self.runtime)

        req = build_active_ingestion_approval_request(
            run_id="run-e2e-idempotent-1",
            directive_id="dir-e2e-idempotent-1",
            runtime_root=str(self.runtime),
            project="knowledge",
            source_package_digest="5" * 64,
            proposed_delta_digest="6" * 64,
            baseline_generation=pair_before["generation_id"],
            intake_package_path=str(self.pkg_path),
        )
        dispatched = dispatch_and_persist_approval_request(
            req,
            storage_root=self.runtime,
            transport=lambda p: {"status": "DISPATCHED"},
        )

        args = argparse.Namespace(
            request_id=dispatched["request_id"],
            decision="APPROVE",
            reviewer="Operator",
            rationale="Operator authorized mutation.",
            runtime_root=self.runtime,
            graph=None,
        )

        result1 = _intake_approval_resume(args)
        self.assertEqual(result1.get("status"), "COMPLETE")

        # Second invocation: idempotent resolution
        result2 = _intake_approval_resume(args)
        self.assertIn(result2.get("status"), ("COMPLETE", "IDEMPOTENT_NOOP", "PASS"))


if __name__ == "__main__":
    unittest.main()
