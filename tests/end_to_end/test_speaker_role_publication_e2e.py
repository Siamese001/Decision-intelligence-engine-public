#!/usr/bin/env python3
"""
tests/end_to_end/test_speaker_role_publication_e2e.py

End-to-End Test Suite for Wave 3: Publishing through governed writer and enforcing active coverage.
Verifies the complete lifecycle:
  authentic human resolution -> Output 03 -> publication activation ->
  independent read-only graph readback -> receipt sealing -> directive completion verification.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.canonical_pipeline.publish_helper import _handle_publication_activation
from dual_pipeline.canonical_pipeline.speaker_role_coverage import (
    record_speaker_role_coverage,
    validate_speaker_role_coverage_receipt,
    verify_completion_speaker_role_coverage,
    verify_speaker_role_coverage_in_graph,
)
from dual_pipeline.core import sha256_file
from dual_pipeline.hitl_governance_core.speaker_resolution_contract import (
    ResolutionProvenance,
    create_speaker_role_resolution,
)


def _init_canonical_test_graph(db_path: Path) -> sqlite3.Connection:
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


@pytest.mark.e2e
class TestSpeakerRolePublicationE2E(unittest.TestCase):
    """End-to-End lifecycle tests for speaker role publication and active graph verification."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.graph_db = self.work_dir / "CANONICAL_GRAPH.db"
        _init_canonical_test_graph(self.graph_db)
        self.sqlite_ledger = self.work_dir / "hitl_decision_ledger.sqlite"
        self.json_ledger = self.work_dir / "hitl_decision_ledger.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_e2e_publication_activation_and_active_readback_lifecycle(self) -> None:
        """Full lifecycle: captures resolutions, applies updates to graph, activates publication,

        runs independent readback, seals coverage receipt, and validates completion.
        """
        run_id = "run-e2e-pub-001"
        generation_id = "gen-20261008-001"

        # Step 1: Pre-populate graph with an existing person having an old role
        with sqlite3.connect(str(self.graph_db)) as conn:
            conn.execute(
                "INSERT INTO node (node_id, canonical_name, properties_json, node_type, active) "
                "VALUES ('person:tenika_johnson', 'Jordan Taylor', '{\"role\": \"Enterprise Architect\"}', 'person', 1)"
            )
            conn.execute(
                "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, currentness_state, is_quarantined) "
                "VALUES ('ass-prev-1', 'person:tenika_johnson', 'HAS_ROLE_IN', 'Enterprise Architect', 'CURRENT', 0)"
            )
            conn.commit()

        base_snapshot = {
            "row_counts": {"node": 1, "assertion": 1, "evidence": 0},
            "nodes": {
                "person:tenika_johnson": {"canonical_name": "Jordan Taylor", "role": "Enterprise Architect"}
            },
        }
        sha_before = sha256_file(self.graph_db)

        # Step 2: Capture authentic human resolutions for two individuals:
        # - Alex Rivera: Novel person ("VP of Architecture")
        # - Jordan Taylor: Existing person correction ("Principal Architect")
        res_mitchell = create_speaker_role_resolution(
            person_name="Alex Rivera",
            resolved_role="VP of Architecture",
            organization="PartnerAdvisory",
            provenance=ResolutionProvenance.HUMAN_OPERATOR,
            is_human_confirmed=True,
            operator_origin={"source": "operator", "text": "Confirmed by operator"},
            sqlite_path=self.sqlite_ledger,
            json_path=self.json_ledger,
        )
        res_tenika = create_speaker_role_resolution(
            person_name="Jordan Taylor",
            resolved_role="Principal Architect",
            organization="PartnerAdvisory",
            provenance=ResolutionProvenance.HUMAN_OPERATOR,
            is_human_confirmed=True,
            operator_origin={"source": "operator", "text": "Promoted to Principal"},
            sqlite_path=self.sqlite_ledger,
            json_path=self.json_ledger,
        )

        resolutions = {
            "Alex Rivera": res_mitchell,
            "Jordan Taylor": res_tenika,
        }

        # Step 3: Governed writer applies mutations to the database
        with sqlite3.connect(str(self.graph_db)) as conn:
            # Insert novel person node & assertion
            conn.execute(
                "INSERT INTO node (node_id, canonical_name, properties_json, node_type, active) "
                "VALUES ('person:mitchell_simmons', 'Alex Rivera', '{\"role\": \"VP of Architecture\"}', 'person', 1)"
            )
            conn.execute(
                "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, currentness_state, is_quarantined) "
                "VALUES ('ass-mitch-1', 'person:mitchell_simmons', 'HAS_ROLE_IN', 'VP of Architecture', 'CURRENT', 0)"
            )
            # Update existing person: supersede previous assertion, insert new active assertion, update node
            conn.execute(
                "UPDATE node SET properties_json = '{\"role\": \"Principal Architect\"}' WHERE node_id = 'person:tenika_johnson'"
            )
            conn.execute(
                "UPDATE assertion SET currentness_state = 'SUPERSEDED', superseded_by_assertion_id = 'ass-tenika-2' WHERE assertion_id = 'ass-prev-1'"
            )
            conn.execute(
                "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, currentness_state, is_quarantined) "
                "VALUES ('ass-tenika-2', 'person:tenika_johnson', 'HAS_ROLE_IN', 'Principal Architect', 'CURRENT', 0)"
            )
            conn.commit()

        # Step 4: Run independent verification of speaker role coverage in the database
        ok, violations, receipt = verify_speaker_role_coverage_in_graph(
            graph_db=self.graph_db,
            resolutions=resolutions,
            activated_generation_id=generation_id,
            proposal_digest="p" * 64,
            approval_digest="a" * 64,
            run_id=run_id,
            output_dir=self.work_dir,
            base_snapshot=base_snapshot,
            graph_sha256_before=sha_before,
        )

        self.assertTrue(ok, f"Verification failed with violations: {violations}")
        self.assertEqual(receipt["status"], "VERIFIED")
        self.assertEqual(receipt["coverage_summary"]["committed_count"], 1)
        self.assertEqual(receipt["coverage_summary"]["superseded_count"], 1)
        self.assertEqual(receipt["coverage_summary"]["missing_count"], 0)
        self.assertEqual(receipt["coverage_summary"]["rejected_count"], 0)

        # Check row deltas
        deltas = receipt["row_deltas"]
        self.assertEqual(deltas["node_delta"], 1)  # 1 new node added
        self.assertEqual(deltas["assertion_delta"], 2)  # 2 new assertions added

        # Check receipt schema
        is_valid_schema, schema_errs = validate_speaker_role_coverage_receipt(receipt)
        self.assertTrue(is_valid_schema, f"Receipt schema invalid: {schema_errs}")

        # Check that receipt was written to output dir
        receipt_file = self.work_dir / "speaker_role_coverage_receipt.json"
        self.assertTrue(receipt_file.is_file(), "Coverage receipt was not written to disk")
        persisted = json.loads(receipt_file.read_text(encoding="utf-8"))
        self.assertEqual(persisted["receipt_id"], receipt["receipt_id"])

        # Step 5: Test publication activation hook integration
        meta = {
            "run_id": run_id,
            "hitl_role_resolutions": resolutions,
            "graph_path": str(self.graph_db),
            "runtime_root": str(self.work_dir),
            "graph_sha256_before": sha_before,
        }
        auth_info = {
            "request_id": "req-e2e-001",
            "proposal_digest": "p" * 64,
            "approval_digest": "a" * 64,
        }
        pub_res = {
            "generation_id": generation_id,
            "status": "SUCCESS",
        }
        activated_res = _handle_publication_activation(
            pub_res=pub_res,
            written={},
            auth_info=auth_info,
            meta=meta,
            out_p=self.work_dir,
        )
        self.assertIn("speaker_role_coverage_receipt", activated_res)
        hook_receipt = activated_res["speaker_role_coverage_receipt"]
        self.assertEqual(hook_receipt["status"], "VERIFIED")

        # Step 6: Test directive completion verification
        completion = {
            "generation_after": generation_id,
            "speaker_role_coverage_receipt": hook_receipt,
        }
        # Enforce truthful completion verification succeeds
        verify_completion_speaker_role_coverage({}, completion)

    def test_e2e_idempotent_republication_disposition(self) -> None:
        """Idempotent republication of identical role facts results in EXISTING_FACT_NOOP and 0 deltas."""
        with sqlite3.connect(str(self.graph_db)) as conn:
            conn.execute(
                "INSERT INTO node (node_id, canonical_name, properties_json, node_type, active) "
                "VALUES ('person:brien_vance', 'Speaker_Alpha Vance', '{\"role\": \"Managing Director\"}', 'person', 1)"
            )
            conn.execute(
                "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, currentness_state, is_quarantined) "
                "VALUES ('ass-bv-1', 'person:brien_vance', 'HAS_ROLE_IN', 'Managing Director', 'CURRENT', 0)"
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
            "row_counts": {"node": 1, "assertion": 1, "evidence": 0},
            "nodes": {"person:brien_vance": {"canonical_name": "Speaker_Alpha Vance", "role": "Managing Director"}},
        }

        ok, violations, receipt = verify_speaker_role_coverage_in_graph(
            graph_db=self.graph_db,
            resolutions={"Speaker_Alpha Vance": res},
            activated_generation_id="gen-noop-e2e",
            proposal_digest="p" * 64,
            approval_digest="a" * 64,
            run_id="run-noop-e2e",
            output_dir=self.work_dir,
            base_snapshot=base_snapshot,
        )
        self.assertTrue(ok)
        self.assertEqual(receipt["coverage_summary"]["existing_noop_count"], 1)
        self.assertEqual(receipt["row_deltas"]["node_delta"], 0)
        self.assertEqual(receipt["row_deltas"]["assertion_delta"], 0)

    def test_e2e_unactivated_crash_state_blocks_completion(self) -> None:
        """If a crash occurs before activation, written-but-unactivated data blocks completion."""
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

        # Attempt verification with unactivated generation
        ok, violations, receipt = verify_speaker_role_coverage_in_graph(
            graph_db=self.graph_db,
            resolutions={"Alex Rivera": res},
            activated_generation_id="",
            proposal_digest="p" * 64,
            approval_digest="a" * 64,
            run_id="run-crash-e2e",
            output_dir=self.work_dir,
        )
        self.assertFalse(ok)
        self.assertEqual(receipt["status"], "REJECTED")

        # Directive completion verification with unactivated / rejected receipt must fail closed
        bad_completion = {
            "generation_after": "gen-attempted-but-unactivated",
            "speaker_role_coverage_receipt": receipt,
        }
        with self.assertRaises(ValueError) as ctx:
            verify_completion_speaker_role_coverage({}, bad_completion)
        self.assertIn("speaker role coverage receipt status must be VERIFIED", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
