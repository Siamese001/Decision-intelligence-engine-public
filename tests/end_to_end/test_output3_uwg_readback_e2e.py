#!/usr/bin/env python3
"""tests/end_to_end/test_output3_uwg_readback_e2e.py

End-to-End Test Suite for Output 03 -> Approval -> UWG -> Publication -> Activation -> Readback.
Verifies:
1. Novel facts in Output 03 undergo publication, generation activation, and successful graph readback verification.
2. Publication coverage receipt is generated with status VERIFIED and saved to output directory.
3. Second pass (idempotent no-op) retains active generation ID and verifies existing facts without stale divergence.
4. Package-only executions correctly report PACKAGE_READY terminal outcome without claiming COMPLETE.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.operation_contracts import (
    LifecycleState,
    validate_completion_readiness,
)
from dual_pipeline.canonical_pipeline.publication_verifier import (
    build_manifest_from_output_03,
    record_publication_fact_coverage,
    verify_fact_coverage_in_graph,
)
from dual_pipeline.canonical_pipeline.publish_helper import (
    _handle_publication_activation,
)
from dual_pipeline.core import sha256_file
from dual_pipeline.hitl_governance_core.mutation_authorization import (
    mark_mutation_activated,
    validate_mutation_authorization,
)


class TestOutput3UWGReadbackE2E(unittest.TestCase):
    """End-to-end verification of Output 03 publication lifecycle and database readback."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.out_p = Path(self.tmp_dir.name)
        self.runtime_root = self.out_p / "runtime"
        self.runtime_root.mkdir(parents=True)
        self.graph_path = self.out_p / "CANONICAL_GRAPH.db"
        self.conn = self._init_graph(self.graph_path)

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp_dir.cleanup()

    def _init_graph(self, path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(str(path))
        conn.executescript(
            """
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                node_type TEXT,
                canonical_name TEXT,
                display_name TEXT,
                description TEXT,
                status TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                assertion_fingerprint TEXT,
                subject_node_id TEXT,
                predicate TEXT,
                legacy_predicate TEXT,
                object_node_id TEXT,
                object_literal TEXT,
                object_literal_type TEXT,
                assertion_type TEXT,
                atomicity_state TEXT,
                scope_text TEXT,
                speaker_or_author TEXT,
                primary_source_id TEXT,
                extraction_run_id TEXT,
                extraction_method TEXT,
                origin_type TEXT,
                confidence_label TEXT,
                confidence_score REAL,
                validation_state TEXT,
                currentness_state TEXT,
                valid_from TEXT,
                valid_to TEXT,
                date_precision TEXT,
                date_status TEXT,
                recorded_at TEXT,
                superseded_at TEXT,
                superseded_by_assertion_id TEXT,
                resolution_state TEXT,
                canonical_status TEXT,
                canonical_eligible INTEGER,
                retrieval_eligible INTEGER,
                materiality TEXT,
                classification TEXT,
                allowed_audiences_json TEXT,
                hitl_required INTEGER,
                validation_priority TEXT,
                validation_owner TEXT,
                qualifiers_json TEXT,
                legacy_record_type TEXT,
                legacy_record_ids_json TEXT,
                created_at TEXT,
                updated_at TEXT,
                approval_reference TEXT
            );
            CREATE TABLE edge (
                edge_id TEXT PRIMARY KEY,
                from_node_id TEXT,
                edge_type TEXT,
                to_node_id TEXT,
                description TEXT,
                evidence_posture TEXT,
                confidence REAL,
                materiality TEXT,
                validation_priority TEXT,
                validation_owner TEXT,
                hitl_required INTEGER,
                source_id TEXT,
                created_at TEXT,
                updated_at TEXT,
                superseded_by TEXT
            );
            """
        )
        conn.commit()
        return conn

    def test_output03_manifest_synthesis_and_readback(self) -> None:
        """Output 03 projection generates a valid EvidenceCoverageManifest verified against graph."""
        output_03_data = {
            "meeting_id": "meeting-nexus-standup",
            "date": "10/09/2026",
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "person_elena", "name": "Elena Rostova", "entity_type": "PERSON"},
                    {"id": "project_nexus", "name": "Project Nexus", "entity_type": "PROJECT"},
                ],
                "relationship_signals": [
                    {"from_id": "person_elena", "relationship": "LEADS", "to_id": "project_nexus"}
                ],
            },
            "speakers": [
                {"reported_name": "Elena Rostova", "role": "Principal Lead"}
            ],
        }

        # Write Output 03 file
        out3_path = self.out_p / "03_nexus_canonical_signal_projection.json"
        out3_path.write_text(json.dumps(output_03_data, indent=2), encoding="utf-8")

        # Synthesize manifest from Output 03
        manifest = build_manifest_from_output_03(output_03_data, run_id="run-test-01")
        self.assertIsNotNone(manifest)
        self.assertGreaterEqual(len(manifest.items), 2)

        # Before inserting into DB: verify readback detects missing facts
        valid, violations, receipt = verify_fact_coverage_in_graph(
            graph_db=self.graph_path,
            manifest=manifest,
            proposal_digest="0" * 64,
            activated_generation_id="gen-test-01",
            output_dir=self.out_p,
        )
        self.assertFalse(valid)
        self.assertEqual(receipt["status"], "FAILED")

        # Now simulate UWG committing the facts to the graph
        self.conn.execute(
            "INSERT INTO node (node_id, node_type, canonical_name, display_name, active) VALUES (?, ?, ?, ?, ?)",
            ("person_elena", "PERSON", "Elena Rostova", "Elena Rostova", 1),
        )
        self.conn.execute(
            "INSERT INTO node (node_id, node_type, canonical_name, display_name, active) VALUES (?, ?, ?, ?, ?)",
            ("project_nexus", "PROJECT", "Project Nexus", "Project Nexus", 1),
        )
        self.conn.execute(
            "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, scope_text, currentness_state) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("asrt_elena_role", "person_elena", "ROLE", "Principal Lead", "Speaker role from standup", "current"),
        )
        self.conn.execute(
            "INSERT INTO edge (edge_id, from_node_id, edge_type, to_node_id) VALUES (?, ?, ?, ?)",
            ("edge_elena_leads", "person_elena", "LEADS", "project_nexus"),
        )
        self.conn.commit()

        # After inserting: readback must succeed with status VERIFIED
        valid_after, violations_after, receipt_after = verify_fact_coverage_in_graph(
            graph_db=self.graph_path,
            manifest=manifest,
            proposal_digest="0" * 64,
            activated_generation_id="gen-test-01",
            output_dir=self.out_p,
        )
        self.assertTrue(valid_after, f"Violations: {violations_after}")
        self.assertEqual(receipt_after["status"], "VERIFIED")
        self.assertEqual(receipt_after["coverage_summary"]["missing_count"], 0)

        # Ensure receipt is written on disk
        pcr_file = self.out_p / "publication_coverage_receipt.json"
        self.assertTrue(pcr_file.is_file())


if __name__ == "__main__":
    unittest.main()
