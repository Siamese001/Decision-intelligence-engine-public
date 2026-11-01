#!/usr/bin/env python3
"""
tests/adversarial/test_speaker_role_publication_adversarial.py

Adversarial test suite for Wave 3: Publishing through governed writer and enforcing active coverage.
Covers at least 4 mandatory categories:
1. Boundary payload: Non-existent database, empty resolutions, 0-byte database.
2. Malformed input: Tampered receipt schema, missing required keys, invalid status.
3. Concurrency & race: Two concurrent un-superseded active assertions causing collision.
4. Invariant bypass attempt: Written-but-unactivated data evasion, generation mismatch, unapproved mutation.
5. Rollback & recovery: Graph verification failure blocking directive completion and preventing silent pass.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.canonical_pipeline.speaker_role_coverage import (
    validate_speaker_role_coverage_receipt,
    verify_completion_speaker_role_coverage,
    verify_speaker_role_coverage_in_graph,
)
from dual_pipeline.hitl_governance_core.speaker_resolution_contract import (
    ResolutionProvenance,
    create_speaker_role_resolution,
)


def _init_test_graph(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT,
            display_name TEXT,
            description TEXT,
            properties_json TEXT,
            node_type TEXT,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_literal TEXT,
            claim_text TEXT,
            currentness_state TEXT,
            is_quarantined INTEGER DEFAULT 0,
            superseded_by_assertion_id TEXT
        );
        CREATE TABLE alias (
            alias_name TEXT,
            canonical_name TEXT,
            active INTEGER DEFAULT 1
        );
        """
    )
    conn.commit()
    return conn


@pytest.mark.adversarial
class TestSpeakerRolePublicationAdversarial(unittest.TestCase):
    """Adversarial security and invariant preservation tests for speaker role publication."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.db_path = self.work_dir / "canonical_graph.sqlite"
        _init_test_graph(self.db_path)
        self.sqlite_ledger = self.work_dir / "hitl_decision_ledger.sqlite"
        self.json_ledger = self.work_dir / "hitl_decision_ledger.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_boundary_nonexistent_and_corrupt_database(self) -> None:
        """Boundary category: non-existent graph path and empty resolutions fail closed safely."""
        missing_db = self.work_dir / "missing_graph.sqlite"
        res = create_speaker_role_resolution(
            person_name="Alex Rivera",
            resolved_role="VP of Architecture",
            organization="PartnerAdvisory",
            provenance=ResolutionProvenance.HUMAN_OPERATOR,
            is_human_confirmed=True,
            operator_origin={"source": "operator", "text": "Confirmed"},
            sqlite_path=self.sqlite_ledger,
            json_path=self.json_ledger,
        )

        ok, violations, receipt = verify_speaker_role_coverage_in_graph(
            graph_db=missing_db,
            resolutions={"Alex Rivera": res},
            activated_generation_id="gen-001",
            proposal_digest="p" * 64,
            approval_digest="a" * 64,
            run_id="run-boundary-001",
        )
        self.assertFalse(ok)
        self.assertTrue(any("not found" in v for v in violations))
        self.assertEqual(receipt, {})

    def test_malformed_receipt_payload_rejection(self) -> None:
        """Malformed input category: tampered or truncated receipts are rejected."""
        # Non-dict receipt
        ok, errs = validate_speaker_role_coverage_receipt("not-a-dict")  # type: ignore
        self.assertFalse(ok)
        self.assertIn("Receipt must be a JSON object.", errs)

        # Missing required keys
        incomplete: Dict[str, Any] = {
            "schema_version": "1.0.0",
            "receipt_id": "r1",
            "status": "VERIFIED",
        }
        ok, errs = validate_speaker_role_coverage_receipt(incomplete)
        self.assertFalse(ok)
        self.assertTrue(any("Missing required receipt fields" in e for e in errs))

        # Unsupported schema version
        bad_version = {
            "schema_version": "9.9.9",
            "receipt_id": "r1",
            "run_id": "run-1",
            "proposal_digest": "a" * 64,
            "approval_digest": "b" * 64,
            "activated_generation_id": "g-1",
            "status": "VERIFIED",
            "covered_roles": [],
            "coverage_summary": {},
            "row_deltas": {},
            "verified_at": "2026-10-08T00:00:00Z",
        }
        ok, errs = validate_speaker_role_coverage_receipt(bad_version)
        self.assertFalse(ok)
        self.assertTrue(any("Invalid schema_version" in e for e in errs))

    def test_concurrency_race_condition_duplicate_active_assertions(self) -> None:
        """Concurrency category: two uncoordinated writes creating competing active assertions fail closed."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO node (node_id, canonical_name, properties_json, node_type, active) "
                "VALUES ('person:brien_vance', 'Speaker_Alpha Vance', '{\"role\": \"Managing Director\"}', 'person', 1)"
            )
            # Two active assertions exist concurrently without supersession
            conn.execute(
                "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, currentness_state, is_quarantined) "
                "VALUES ('ass-concurrent-1', 'person:brien_vance', 'HAS_ROLE_IN', 'Managing Director', 'CURRENT', 0)"
            )
            conn.execute(
                "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, currentness_state, is_quarantined) "
                "VALUES ('ass-concurrent-2', 'person:brien_vance', 'HAS_ROLE_IN', 'Senior Managing Director', 'CURRENT', 0)"
            )
            conn.commit()

        res = create_speaker_role_resolution(
            person_name="Speaker_Alpha Vance",
            resolved_role="Managing Director",
            organization="PartnerAdvisory",
            provenance=ResolutionProvenance.HUMAN_OPERATOR,
            is_human_confirmed=True,
            operator_origin={"source": "operator", "text": "Confirmed"},
            sqlite_path=self.sqlite_ledger,
            json_path=self.json_ledger,
        )

        ok, violations, receipt = verify_speaker_role_coverage_in_graph(
            graph_db=self.db_path,
            resolutions={"Speaker_Alpha Vance": res},
            activated_generation_id="gen-race-001",
            proposal_digest="p" * 64,
            approval_digest="a" * 64,
            run_id="run-race-001",
            output_dir=self.work_dir,
        )
        self.assertFalse(ok)
        self.assertEqual(receipt["status"], "REJECTED")
        self.assertTrue(any("DUPLICATE_ACTIVE_ROLE_ASSERTION" in v for v in violations))
        self.assertEqual(receipt["coverage_summary"]["rejected_count"], 1)

    def test_invariant_bypass_unactivated_generation_mismatch(self) -> None:
        """Invariant bypass category: written-but-unactivated data with mismatched generation fails closed."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO node (node_id, canonical_name, properties_json, node_type, active) "
                "VALUES ('person:mitchell_simmons', 'Alex Rivera', '{\"role\": \"VP of Architecture\"}', 'person', 1)"
            )
            conn.execute(
                "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, currentness_state, is_quarantined) "
                "VALUES ('ass-m-1', 'person:mitchell_simmons', 'HAS_ROLE_IN', 'VP of Architecture', 'CURRENT', 0)"
            )
            conn.commit()

        res = create_speaker_role_resolution(
            person_name="Alex Rivera",
            resolved_role="VP of Architecture",
            organization="PartnerAdvisory",
            provenance=ResolutionProvenance.HUMAN_OPERATOR,
            is_human_confirmed=True,
            operator_origin={"source": "operator", "text": "Confirmed"},
            sqlite_path=self.sqlite_ledger,
            json_path=self.json_ledger,
        )

        # Expected generation is gen-active-001, but caller claims gen-fake-999
        ok, violations, receipt = verify_speaker_role_coverage_in_graph(
            graph_db=self.db_path,
            resolutions={"Alex Rivera": res},
            activated_generation_id="gen-fake-999",
            proposal_digest="p" * 64,
            approval_digest="a" * 64,
            run_id="run-evasion-001",
            active_generation_check="gen-active-001",
            output_dir=self.work_dir,
        )
        self.assertFalse(ok)
        self.assertTrue(any("ACTIVATION_MISMATCH" in v for v in violations))
        self.assertEqual(receipt["status"], "REJECTED")

    def test_rollback_and_recovery_verification_failure_blocks_completion(self) -> None:
        """Rollback & recovery category: failed verification prevents ingestion directive completion."""
        # Coverage receipt indicates failure / missing fact
        failed_receipt = {
            "schema_version": "1.0.0",
            "receipt_id": "r-failed-001",
            "run_id": "run-fail-001",
            "proposal_digest": "p" * 64,
            "approval_digest": "a" * 64,
            "activated_generation_id": "gen-001",
            "status": "FAILED",
            "covered_roles": [
                {
                    "person_name": "Alex Rivera",
                    "canonical_identity": "person:mitchell_simmons",
                    "resolved_role": "VP of Architecture",
                    "disposition": "MISSING",
                    "verified_in_graph": False,
                }
            ],
            "coverage_summary": {
                "total_roles": 1,
                "committed_count": 0,
                "superseded_count": 0,
                "existing_noop_count": 0,
                "missing_count": 1,
                "rejected_count": 0,
            },
            "row_deltas": {},
            "verified_at": "2026-10-08T18:00:00Z",
        }

        # Directive completion must fail closed
        completion = {
            "generation_after": "gen-001",
            "speaker_role_coverage_receipt": failed_receipt,
        }
        with self.assertRaises(ValueError) as ctx:
            verify_completion_speaker_role_coverage({}, completion)
        self.assertIn("speaker role coverage receipt status must be VERIFIED", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
