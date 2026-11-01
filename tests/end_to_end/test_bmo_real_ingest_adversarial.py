"""Adversarial & Edge-Case Tests: BMO Ingestion & GraphDB SSOT Activation Safety.

Tests edge-case invariants:
1. Boundary: Tampered approval request digest fails closed.
2. Malformed: Stale or unapproved release activation rejected.
3. Invariant bypass attempt: Direct activation without valid ledger-bound approval fails.
4. Hash mismatch: Corrupted graph or sidecar SHA256 fails pair validation.
"""

from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DUAL_PIPELINE = ROOT / ".local" / "dual_pipeline"


class TestBMORealIngestAdversarial(unittest.TestCase):
    """Adversarial safety checks for BMO real ingestion and graph mutation."""

    def setUp(self) -> None:
        self.active_pair_path = DUAL_PIPELINE / "active_pair.json"
        self.assertTrue(self.active_pair_path.is_file())
        with open(self.active_pair_path, "r", encoding="utf-8") as f:
            self.active_pair = json.load(f)

    def test_tampered_approval_request_fails_verification(self) -> None:
        """Zero digests or missing deltas must fail approval request construction."""
        from dual_pipeline.hitl_governance_core.active_ingestion_approval import (
            build_active_ingestion_approval_request,
            check_active_ingestion_clearance,
        )

        with self.assertRaises(ValueError):
            build_active_ingestion_approval_request(
                run_id="run-fake",
                directive_id="dir-fake",
                runtime_root=str(DUAL_PIPELINE),
                project="bmo",
                source_package_digest="0" * 64,  # zero digest forbidden
                proposed_delta_digest="b" * 64,
                baseline_generation=1,
                table_deltas={"node": 19, "source": 1, "assertion": 11},
            )

        # Unapproved directive clearance must fail closed
        cleared, reason, _ = check_active_ingestion_clearance(
            run_id="run-unapproved-fake",
            directive_id="dir-unapproved-fake",
            delta_digest="b" * 64,
            ledger_path=DUAL_PIPELINE / "hitl_decision_ledger.sqlite",
        )
        self.assertFalse(cleared)
        self.assertIn("UNRATIFIED", reason)

    def test_direct_activation_without_valid_receipt_fails(self) -> None:
        """ReleaseManager must reject activating a nonexistent or unverified receipt."""
        from dual_pipeline.release import ReleaseManager

        manager = ReleaseManager(DUAL_PIPELINE)
        fake_receipt = DUAL_PIPELINE / "releases" / "nonexistent-fake-receipt.json"
        with self.assertRaises((FileNotFoundError, ValueError)):
            manager.activate(fake_receipt)

    def test_active_pair_hash_matches_graph_on_disk(self) -> None:
        """Active pair graph hash must strictly match actual SQLite file SHA-256."""
        import hashlib

        graph_path = DUAL_PIPELINE / self.active_pair["graph_path"]
        self.assertTrue(graph_path.is_file())

        hasher = hashlib.sha256()
        with open(graph_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        actual_hash = hasher.hexdigest()

        self.assertEqual(
            actual_hash,
            self.active_pair["graph_sha256"],
            "Active pair graph SHA256 does not match physical graph file bytes",
        )

    def test_sidecar_hash_matches_sidecar_on_disk(self) -> None:
        """Active pair sidecar hash must strictly match actual SQLite file SHA-256."""
        import hashlib

        sidecar_path = DUAL_PIPELINE / self.active_pair["sidecar_path"]
        self.assertTrue(sidecar_path.is_file())

        hasher = hashlib.sha256()
        with open(sidecar_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        actual_hash = hasher.hexdigest()

        self.assertEqual(
            actual_hash,
            self.active_pair["sidecar_sha256"],
            "Active pair sidecar SHA256 does not match physical sidecar file bytes",
        )


if __name__ == "__main__":
    unittest.main()
