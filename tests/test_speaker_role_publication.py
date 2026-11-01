#!/usr/bin/env python3
"""
tests/test_speaker_role_publication.py

Unit test suite for Wave 3: Publishing through governed writer and enforcing active coverage.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

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


class TestSpeakerRolePublicationUnit(unittest.TestCase):
    """Unit tests for speaker role publication verification and graph inspection."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.db_path = self.work_dir / "canonical_graph.sqlite"
        _init_test_graph(self.db_path)
        self.sqlite_ledger = self.work_dir / "hitl_decision_ledger.sqlite"
        self.json_ledger = self.work_dir / "hitl_decision_ledger.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_schema_validation_valid_receipt(self) -> None:
        """Valid speaker role coverage receipt passes structural schema validation."""
        receipt = {
            "schema_version": "1.0.0",
            "receipt_id": "src-20261008-001",
            "run_id": "run-001",
            "proposal_digest": "a" * 64,
            "approval_digest": "b" * 64,
            "activated_generation_id": "gen-001",
            "status": "VERIFIED",
            "covered_roles": [
                {
                    "person_name": "Alex Rivera",
                    "canonical_identity": "person:mitchell_simmons",
                    "resolved_role": "VP of Architecture",
                    "organization": "PartnerAdvisory",
                    "before_value": None,
                    "after_value": "VP of Architecture",
                    "disposition": "COMMITTED",
                    "disposition_reason": "Novel person role committed.",
                    "verified_in_graph": True,
                    "graph_row_id": "ass-001",
                    "authority": "entry-001",
                }
            ],
            "coverage_summary": {
                "total_roles": 1,
                "committed_count": 1,
                "superseded_count": 0,
                "existing_noop_count": 0,
                "missing_count": 0,
                "rejected_count": 0,
            },
            "row_deltas": {
                "nodes_count": 1,
                "assertions_count": 1,
                "nodes_delta": 1,
                "assertions_delta": 1,
            },
            "verified_at": "2026-10-08T18:00:00Z",
        }
        ok, errors = validate_speaker_role_coverage_receipt(receipt)
        self.assertTrue(ok, f"Validation failed: {errors}")
        self.assertEqual(errors, [])

    def test_schema_validation_invalid_receipt(self) -> None:
        """Malformed receipt missing required fields fails validation with errors."""
        bad_receipt = {"schema_version": "1.0.0", "status": "VERIFIED"}
        ok, errors = validate_speaker_role_coverage_receipt(bad_receipt)
        self.assertFalse(ok)
        self.assertTrue(any("Missing required receipt fields" in e for e in errors))

    def test_verify_novel_person_role_committed(self) -> None:
        """Novel person role inserted into graph returns COMMITTED disposition and row deltas."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO node (node_id, canonical_name, properties_json, node_type, active) "
                "VALUES ('person:mitchell_simmons', 'Alex Rivera', '{\"role\": \"VP of Architecture\"}', 'person', 1)"
            )
            conn.execute(
                "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, currentness_state, is_quarantined) "
                "VALUES ('ass-001', 'person:mitchell_simmons', 'HAS_ROLE_IN', 'VP of Architecture', 'CURRENT', 0)"
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

        ok, violations, receipt = verify_speaker_role_coverage_in_graph(
            graph_db=self.db_path,
            resolutions={"Alex Rivera": res},
            activated_generation_id="gen-001",
            proposal_digest="p" * 64,
            approval_digest="a" * 64,
            run_id="run-novel-001",
            output_dir=self.work_dir,
        )
        self.assertTrue(ok, f"Verification failed: {violations}")
        self.assertEqual(receipt["status"], "VERIFIED")
        self.assertEqual(receipt["coverage_summary"]["committed_count"], 1)
        role_entry = receipt["covered_roles"][0]
        self.assertEqual(role_entry["disposition"], "COMMITTED")
        self.assertEqual(role_entry["after_value"], "VP of Architecture")
        self.assertIsNone(role_entry["before_value"])
        self.assertTrue(role_entry["verified_in_graph"])

    def test_verify_existing_person_role_superseded_correction(self) -> None:
        """Existing person role update produces SUPERSEDED_CORRECTION with before/after values."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO node (node_id, canonical_name, properties_json, node_type, active) "
                "VALUES ('person:tenika_johnson', 'Jordan Taylor', '{\"role\": \"Principal Architect\"}', 'person', 1)"
            )
            conn.execute(
                "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, currentness_state, is_quarantined, superseded_by_assertion_id) "
                "VALUES ('ass-old', 'person:tenika_johnson', 'HAS_ROLE_IN', 'Enterprise Architect', 'SUPERSEDED', 0, 'ass-new')"
            )
            conn.execute(
                "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, currentness_state, is_quarantined) "
                "VALUES ('ass-new', 'person:tenika_johnson', 'HAS_ROLE_IN', 'Principal Architect', 'CURRENT', 0)"
            )
            conn.commit()

        res = create_speaker_role_resolution(
            person_name="Jordan Taylor",
            resolved_role="Principal Architect",
            organization="PartnerAdvisory",
            provenance=ResolutionProvenance.HUMAN_OPERATOR,
            is_human_confirmed=True,
            operator_origin={"source": "operator", "text": "Promoted"},
            sqlite_path=self.sqlite_ledger,
            json_path=self.json_ledger,
        )

        base_snapshot = {
            "nodes": {"person:tenika_johnson": {"canonical_name": "Jordan Taylor", "role": "Enterprise Architect"}}
        }

        ok, violations, receipt = verify_speaker_role_coverage_in_graph(
            graph_db=self.db_path,
            resolutions={"Jordan Taylor": res},
            activated_generation_id="gen-002",
            proposal_digest="p" * 64,
            approval_digest="a" * 64,
            run_id="run-correct-002",
            output_dir=self.work_dir,
            base_snapshot=base_snapshot,
        )
        self.assertTrue(ok, f"Verification failed: {violations}")
        self.assertEqual(receipt["coverage_summary"]["superseded_count"], 1)
        role_entry = receipt["covered_roles"][0]
        self.assertEqual(role_entry["disposition"], "SUPERSEDED_CORRECTION")
        self.assertEqual(role_entry["before_value"], "Enterprise Architect")
        self.assertEqual(role_entry["after_value"], "Principal Architect")

    def test_verify_identical_confirmed_fact_is_noop(self) -> None:
        """Confirmed role that matches baseline value returns EXISTING_FACT_NOOP."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO node (node_id, canonical_name, properties_json, node_type, active) "
                "VALUES ('person:brien_vance', 'Speaker_Alpha Vance', '{\"role\": \"Managing Director\"}', 'person', 1)"
            )
            conn.execute(
                "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, currentness_state, is_quarantined) "
                "VALUES ('ass-bv', 'person:brien_vance', 'HAS_ROLE_IN', 'Managing Director', 'CURRENT', 0)"
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

        base_snapshot = {
            "nodes": {"person:brien_vance": {"canonical_name": "Speaker_Alpha Vance", "role": "Managing Director"}}
        }

        ok, violations, receipt = verify_speaker_role_coverage_in_graph(
            graph_db=self.db_path,
            resolutions={"Speaker_Alpha Vance": res},
            activated_generation_id="gen-003",
            proposal_digest="p" * 64,
            approval_digest="a" * 64,
            run_id="run-noop-003",
            output_dir=self.work_dir,
            base_snapshot=base_snapshot,
        )
        self.assertTrue(ok)
        self.assertEqual(receipt["coverage_summary"]["existing_noop_count"], 1)
        self.assertEqual(receipt["covered_roles"][0]["disposition"], "EXISTING_FACT_NOOP")

    def test_verify_duplicate_active_assertions_rejected(self) -> None:
        """Multiple un-superseded active assertions trigger DUPLICATE_ACTIVE_ROLE_ASSERTION rejection."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO node (node_id, canonical_name, properties_json, node_type, active) "
                "VALUES ('person:alice_smith', 'Alice Smith', '{\"role\": \"Lead Engineer\"}', 'person', 1)"
            )
            conn.execute(
                "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, currentness_state, is_quarantined) "
                "VALUES ('ass-1', 'person:alice_smith', 'HAS_ROLE_IN', 'Lead Engineer', 'CURRENT', 0)"
            )
            conn.execute(
                "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, currentness_state, is_quarantined) "
                "VALUES ('ass-2', 'person:alice_smith', 'HAS_ROLE_IN', 'Principal Engineer', 'CURRENT', 0)"
            )
            conn.commit()

        res = create_speaker_role_resolution(
            person_name="Alice Smith",
            resolved_role="Lead Engineer",
            organization="PartnerAdvisory",
            provenance=ResolutionProvenance.HUMAN_OPERATOR,
            is_human_confirmed=True,
            operator_origin={"source": "operator", "text": "Confirmed"},
            sqlite_path=self.sqlite_ledger,
            json_path=self.json_ledger,
        )

        ok, violations, receipt = verify_speaker_role_coverage_in_graph(
            graph_db=self.db_path,
            resolutions={"Alice Smith": res},
            activated_generation_id="gen-dup",
            proposal_digest="p" * 64,
            approval_digest="a" * 64,
            run_id="run-dup-001",
            output_dir=self.work_dir,
        )
        self.assertFalse(ok)
        self.assertEqual(receipt["status"], "REJECTED")
        self.assertTrue(any("DUPLICATE_ACTIVE_ROLE_ASSERTION" in v for v in violations))
        self.assertEqual(receipt["coverage_summary"]["rejected_count"], 1)

    def test_verify_missing_role_assertion_fails(self) -> None:
        """Role present in resolution but missing from graph returns MISSING disposition and fails."""
        res = create_speaker_role_resolution(
            person_name="Ghost Attendee",
            resolved_role="Observer",
            organization="PartnerAdvisory",
            provenance=ResolutionProvenance.HUMAN_OPERATOR,
            is_human_confirmed=True,
            operator_origin={"source": "operator", "text": "Ghost"},
            sqlite_path=self.sqlite_ledger,
            json_path=self.json_ledger,
        )

        ok, violations, receipt = verify_speaker_role_coverage_in_graph(
            graph_db=self.db_path,
            resolutions={"Ghost Attendee": res},
            activated_generation_id="gen-ghost",
            proposal_digest="p" * 64,
            approval_digest="a" * 64,
            run_id="run-ghost-001",
            output_dir=self.work_dir,
        )
        self.assertFalse(ok)
        self.assertEqual(receipt["coverage_summary"]["missing_count"], 1)
        self.assertEqual(receipt["covered_roles"][0]["disposition"], "MISSING")

    def test_verify_unactivated_generation_rejected(self) -> None:
        """Empty activated generation ID fails closed without inspecting the database."""
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
            graph_db=self.db_path,
            resolutions={"Alex Rivera": res},
            activated_generation_id="",
            proposal_digest="p" * 64,
            approval_digest="a" * 64,
            run_id="run-unact-001",
            output_dir=self.work_dir,
        )
        self.assertFalse(ok)
        self.assertTrue(any("UNACTIVATED_DATA_CANNOT_PASS_COMPLETION" in v for v in violations))

    def test_receipt_persisted_to_output_dir(self) -> None:
        """Passing verification writes speaker_role_coverage_receipt.json to output directory."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT INTO node (node_id, canonical_name, properties_json, node_type, active) "
                "VALUES ('person:mitchell_simmons', 'Alex Rivera', '{\"role\": \"VP of Architecture\"}', 'person', 1)"
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

        out_receipt = self.work_dir / "speaker_role_coverage_receipt.json"
        self.assertFalse(out_receipt.exists())

        ok, violations, receipt = verify_speaker_role_coverage_in_graph(
            graph_db=self.db_path,
            resolutions={"Alex Rivera": res},
            activated_generation_id="gen-write-001",
            proposal_digest="p" * 64,
            approval_digest="a" * 64,
            run_id="run-write-001",
            output_dir=self.work_dir,
        )
        self.assertTrue(ok)
        self.assertTrue(out_receipt.exists())
        saved_data = json.loads(out_receipt.read_text(encoding="utf-8"))
        self.assertEqual(saved_data["receipt_id"], receipt["receipt_id"])

    def test_directive_completion_verification_enforces_receipt(self) -> None:
        """verify_completion_speaker_role_coverage validates receipt schema and generation match."""
        receipt = {
            "schema_version": "1.0.0",
            "receipt_id": "src-comp-001",
            "run_id": "run-001",
            "proposal_digest": "a" * 64,
            "approval_digest": "b" * 64,
            "activated_generation_id": "gen-12345",
            "status": "VERIFIED",
            "covered_roles": [],
            "coverage_summary": {},
            "row_deltas": {},
            "verified_at": "2026-10-08T18:00:00Z",
        }
        completion = {
            "generation_after": "gen-12345",
            "speaker_role_coverage_receipt": receipt,
        }
        # Should pass without exception
        verify_completion_speaker_role_coverage({}, completion)

        # Mismatched generation should raise ValueError
        bad_completion = {
            "generation_after": "gen-99999",
            "speaker_role_coverage_receipt": receipt,
        }
        with self.assertRaises(ValueError) as ctx:
            verify_completion_speaker_role_coverage({}, bad_completion)
        self.assertIn("does not match activated completion generation", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
