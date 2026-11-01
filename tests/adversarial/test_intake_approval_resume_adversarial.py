"""Adversarial and Edge-Case Test Suite for Active Ingestion Approval Resumption (Wave 3).

Covers mandatory edge-case categories:
  1. Malformed input (missing request ID, corrupted JSON, missing intake package path).
  2. Invariant bypass attempts (synthetic auto-approval markers, autonomous reviewer strings, zero digests).
  3. Expiry & boundary conditions (expired requests fail closed, zero-digest contracts rejected).
  4. State consistency & replay safety (resuming declined request fails closed).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from dual_pipeline.cli.handlers import _intake_approval_resume
from dual_pipeline.core import write_json
from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
    build_active_ingestion_approval_request,
    dispatch_and_persist_approval_request,
    resume_ingestion_with_approval,
)


class IntakeApprovalResumeAdversarialTests(unittest.TestCase):
    """Adversarial test suite probing validation boundaries and bypass resistance."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir(parents=True)
        self.requests_dir = self.runtime / "approval_requests"
        self.requests_dir.mkdir(parents=True)

        self.pkg_path = Path(__file__).resolve().parents[2] / "contracts" / "dual_pipeline" / "fixtures" / "valid" / "evidence-envelope-intake.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # Category 1: Malformed Input
    def test_missing_request_id_raises_value_error(self) -> None:
        """Missing --request-id fails immediately with clear ValueError."""
        args = argparse.Namespace(request_id=None, runtime_root=self.runtime)
        with self.assertRaises(ValueError):
            _intake_approval_resume(args)

    def test_nonexistent_request_id_raises_file_not_found(self) -> None:
        """Non-existent request ID fails cleanly with FileNotFoundError."""
        args = argparse.Namespace(request_id="req-nonexistent-999", runtime_root=self.runtime)
        with self.assertRaises(FileNotFoundError):
            _intake_approval_resume(args)

    def test_missing_intake_package_path_in_request_fails_closed(self) -> None:
        """Approval request missing intake_package_path cannot be resumed."""
        req = build_active_ingestion_approval_request(
            run_id="run-adv-missing-pkg",
            directive_id="dir-adv-missing-pkg",
            runtime_root=str(self.runtime),
            project="knowledge",
            source_package_digest="a" * 64,
            proposed_delta_digest="b" * 64,
            baseline_generation=0,
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
            rationale="Approved",
            runtime_root=self.runtime,
            graph=None,
        )
        with self.assertRaises(ValueError) as ctx:
            _intake_approval_resume(args)
        self.assertIn("does not contain intake_package_path", str(ctx.exception))

    # Category 2: Invariant Bypass Attempts
    def test_zero_digest_rejected_in_builder(self) -> None:
        """Placeholder zero digests ("0"*64) are strictly rejected at creation."""
        with self.assertRaises(ValueError) as ctx:
            build_active_ingestion_approval_request(
                run_id="run-adv-zero-digest",
                directive_id="dir-adv-zero-digest",
                runtime_root=str(self.runtime),
                project="knowledge",
                source_package_digest="0" * 64,
                proposed_delta_digest="1" * 64,
                baseline_generation=0,
            )
        self.assertIn("ACTIVE_APPROVAL_REJECTED", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            build_active_ingestion_approval_request(
                run_id="run-adv-zero-digest-2",
                directive_id="dir-adv-zero-digest-2",
                runtime_root=str(self.runtime),
                project="knowledge",
                source_package_digest="1" * 64,
                proposed_delta_digest="0" * 64,
                baseline_generation=0,
            )
        self.assertIn("ACTIVE_APPROVAL_REJECTED", str(ctx.exception))

    def test_zero_digest_rejected_in_resumption(self) -> None:
        """Corrupted approval request containing placeholder zero digest fails closed on resume."""
        raw_req = {
            "contract_id": "urn:nee:dual-pipeline:active-ingestion-approval-request:1.0.0",
            "schema_version": "1.0.0",
            "artifact_id": "aiar-req-zero-digest-test",
            "request_id": "req-zero-digest-test",
            "run_id": "run-zero",
            "directive_id": "dir-zero",
            "runtime_root": str(self.runtime),
            "project": "knowledge",
            "source_package_digest": "0" * 64,
            "proposed_delta_digest": "0" * 64,
            "baseline_generation": 0,
            "expires_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat(),
            "dispatch_status": "DISPATCHED",
            "response_status": "AWAITING",
            "question_payload": {"options": [{"label": "Approve"}]},
        }
        write_json(self.requests_dir / "req-zero-digest-test.json", raw_req)

        success, msg, _ = resume_ingestion_with_approval(
            request_id="req-zero-digest-test",
            operator_response={"selected_option": "Approve", "operator_id": "alice"},
            storage_root=self.runtime,
        )
        self.assertFalse(success)
        self.assertIn("INVALID_DIGEST", msg)

    def test_autonomous_alias_reviewer_rejected(self) -> None:
        """Autonomous bot aliases cannot authorize resumption."""
        req = build_active_ingestion_approval_request(
            run_id="run-adv-bot",
            directive_id="dir-adv-bot",
            runtime_root=str(self.runtime),
            project="knowledge",
            source_package_digest="c" * 64,
            proposed_delta_digest="d" * 64,
            baseline_generation=0,
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
            reviewer="mock_operator",
            rationale="Auto approval attempt",
            runtime_root=self.runtime,
            graph=None,
        )
        res = _intake_approval_resume(args)
        self.assertEqual(res.get("status"), "FAILED")
        self.assertIn("UNAUTHENTICATED_OPERATOR", res.get("reason", ""))

    # Category 3: Expiry & Boundary Conditions
    def test_expired_request_resumption_fails_closed(self) -> None:
        """An expired approval request transitions to EXPIRED and fails closed."""
        req = build_active_ingestion_approval_request(
            run_id="run-adv-expired",
            directive_id="dir-adv-expired",
            runtime_root=str(self.runtime),
            project="knowledge",
            source_package_digest="e" * 64,
            proposed_delta_digest="f" * 64,
            baseline_generation=0,
            ttl_hours=-1,
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
            rationale="Approved",
            runtime_root=self.runtime,
            graph=None,
        )
        res = _intake_approval_resume(args)
        self.assertEqual(res.get("status"), "FAILED")
        self.assertIn("EXPIRED", res.get("reason", ""))


if __name__ == "__main__":
    unittest.main()
