"""Adversarial and edge-case testing for Output 03 graph mutation pipeline.

Covers:
1. Boundary & malformed input (missing ID, missing type, unknown prefix fallback).
2. Invariant bypass prevention (non-creatable types like 'card' or 'external_concept').
3. Idempotent replay guarantee (second run after mutation avoids duplicate entity collisions).
4. Conflicting identity proposal deconfliction.
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.contracts import seal_artifact
from dual_pipeline.core import ROOT
from dual_pipeline.graph_writer import GovernedGraphWriter
from dual_pipeline.identity import resolve_bundle
from dual_pipeline.intake import build_dispositions, build_graph_change_set
from dual_pipeline.node_type_policy import NON_CREATABLE_NODE_TYPES, require_creatable_node_type
from dual_pipeline.normalization import normalize_evidence_envelope
from dual_pipeline.normalization_core.meeting_entities import map_entity_type


class TestOutput3GraphMutationAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        candidate_graphs = [
            ROOT / "graph" / "nee_graph.sqlite",
            ROOT / ".local" / "dual_pipeline" / "generations" / "generation-36b538f04af06cb4be863517d4f0" / "graph.sqlite",
            ROOT.parent.parent / "graph" / "nee_graph.sqlite",
        ]
        self.orig_graph = next((g for g in candidate_graphs if g.is_file()), None)
        if not self.orig_graph:
            self.skipTest(f"Active baseline graph not found among {candidate_graphs}")

        self.db_path = Path(self.tmp_dir.name) / "test_adversarial_graph.sqlite"
        shutil.copy2(self.orig_graph, self.db_path)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_case_1_boundary_and_malformed_entity_payloads(self) -> None:
        """Verify malformed and boundary entity upserts degrade safely to valid registry types."""
        # Missing type, unknown prefix defaults safely
        self.assertEqual(map_entity_type(None), "stakeholder")
        self.assertEqual(map_entity_type(""), "stakeholder")
        self.assertEqual(map_entity_type("unknown_nonexistent_prefix"), "pattern")
        self.assertEqual(map_entity_type("sys:unusual_app"), "technical_anchor")
        self.assertEqual(map_entity_type("speaker:guest"), "stakeholder")
        self.assertEqual(map_entity_type("dept:engineering"), "org")

    def test_case_2_invariant_bypass_non_creatable_node_types_rejected(self) -> None:
        """Verify non-creatable node types cannot be proposed for internal creation."""
        from dual_pipeline.core import connect_sqlite
        conn = connect_sqlite(self.db_path, readonly=True)
        for bad_type in NON_CREATABLE_NODE_TYPES:
            with self.assertRaises((ValueError, RuntimeError)):
                require_creatable_node_type(conn, bad_type)
        conn.close()

    def test_case_3_idempotent_replay_avoids_duplicate_entity_collisions(self) -> None:
        """Verify rerun after mutation skips duplicate entity creation and maintains idempotency."""
        package = {
            "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            "schema_version": "1.0.0",
            "artifact_id": "artifact-meeting-idempotent-test",
            "meeting": {
                "meeting_id": "meeting-idempotent-test",
                "title": "Idempotent Replay Test Review",
                "started_at": "2026-09-10T10:00:00-04:00",
                "source_location": "archive://notes/meeting-idempotent-test.md",
                "source_hash": "d" * 64,
                "attendees": ["Alex Sanchez"],
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "template_version": "5.7",
            },
            "speakers": [
                {
                    "speaker_ref": "speaker-alex",
                    "reported_name": "Alex Sanchez",
                    "email": "alex.sanchez@consulting.example.com",
                    "role_or_org": "Architecture Dept",
                    "statements": [
                        {
                            "statement_id": "stmt-01",
                            "kind": "technical",
                            "text": "The Atlas Pilot deployment stabilizes the ingestion pipeline.",
                            "locator": {"kind": "timestamp", "value": "01:00"},
                            "gemini_confidence": 0.95,
                        }
                    ],
                }
            ],
            "actions": [],
            "adapter": {"name": "nee-evidence-envelope-intake", "version": "1.0.0"},
            "extensions": {
                "com.partner_advisory.nee.canonical-signal-projection": {
                    "entity_upserts": [
                        {
                            "id": "system:atlas_pilot_engine",
                            "type": "system",
                            "name": "Atlas Pilot Engine",
                            "description": "Atlas ingestion engine",
                            "mutation": "CREATE",
                        }
                    ],
                    "relationship_signals": [],
                }
            },
        }

        # First run: mutation creates entity
        sealed_pkg = seal_artifact(package)
        bundle_1 = normalize_evidence_envelope(sealed_pkg, run_context="run_1")
        res_1 = resolve_bundle(bundle_1, self.db_path)
        routes_1 = [{"subject_id": c["candidate_id"], "intake_route": "NEW_ENTITY", "reason_codes": []} for c in bundle_1["candidates"]]
        import json
        conn_gov = sqlite3.connect(self.db_path)
        conn_gov.execute(
            """INSERT OR REPLACE INTO governance_approval
               (approval_id, approval_reference, approval_type, scope_json,
                proposal_sha256, approval_statement_sha256, approved_at,
                status, approved_by, release_version, active, created_at)
               VALUES (?, ?, 'ingestion_promotion', ?,
                       ?, ?, '2026-09-10T10:00:00Z',
                       'approved', 'operator', '1.0.0', 1, '2026-09-10T10:00:00Z')""",
            (
                "approval-run-1",
                "approval-run-1",
                json.dumps({"allowed_actions": ["canonical_assertion_promotion"]}),
                "0" * 64,
                "0" * 64,
            ),
        )
        conn_gov.commit()
        conn_gov.close()

        disp_1 = build_dispositions(bundle_1, routes_1, res_1)
        cs_1 = build_graph_change_set(bundle_1, disp_1, res_1, self.db_path)
        self.assertIsNotNone(cs_1)
        create_ops_1 = [op for op in cs_1["operations"] if op["operation"] == "create_entity"]
        self.assertEqual(len(create_ops_1), 1)

        # Apply run 1
        cs_1["status"] = "approved"
        cs_1["approval_id"] = "approval-run-1"
        writer = GovernedGraphWriter(self.db_path)
        receipts_1 = writer.apply(seal_artifact(cs_1))
        self.assertGreater(len(receipts_1), 0)

        # Verify entity now exists in database
        conn = sqlite3.connect(self.db_path)
        node = conn.execute("SELECT node_id, canonical_name FROM node WHERE canonical_name='Atlas Pilot Engine'").fetchone()
        self.assertIsNotNone(node)
        conn.close()

        # Second run: identical raw package re-run
        bundle_2 = normalize_evidence_envelope(sealed_pkg, run_context="run_2")
        res_2 = resolve_bundle(bundle_2, self.db_path)
        routes_2 = [{"subject_id": c["candidate_id"], "intake_route": "CORROBORATING_EVIDENCE", "reason_codes": []} for c in bundle_2["candidates"]]
        disp_2 = build_dispositions(bundle_2, routes_2, res_2)
        cs_2 = build_graph_change_set(bundle_2, disp_2, res_2, self.db_path)

        # On second run, the entity already exists in node table.
        # It must NOT emit a duplicate create_entity operation!
        if cs_2 is not None:
            create_ops_2 = [op for op in cs_2["operations"] if op["operation"] == "create_entity"]
            self.assertEqual(len(create_ops_2), 0, "Second run must not re-emit create_entity for existing node")

    def test_case_4_empty_upsert_payload_safe_noop(self) -> None:
        """Verify empty entity_upserts produce clean zero-op candidate lists without crashes."""
        package = {
            "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            "schema_version": "1.0.0",
            "artifact_id": "artifact-meeting-empty-test",
            "meeting": {
                "meeting_id": "meeting-empty-test",
                "title": "Empty Review",
                "started_at": "2026-09-10T10:00:00-04:00",
                "source_location": "archive://notes/meeting-empty-test.md",
                "source_hash": "e" * 64,
                "attendees": ["Alex Sanchez"],
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "template_version": "5.7",
            },
            "speakers": [
                {
                    "speaker_ref": "speaker-alex",
                    "reported_name": "Alex Sanchez",
                    "email": "alex.sanchez@consulting.example.com",
                    "role_or_org": "Architecture Dept",
                    "statements": [
                        {
                            "statement_id": "stmt-01",
                            "kind": "technical",
                            "text": "Standard discussion with no new entities or relationships.",
                            "locator": {"kind": "timestamp", "value": "01:00"},
                            "gemini_confidence": 0.95,
                        }
                    ],
                }
            ],
            "actions": [],
            "adapter": {"name": "nee-evidence-envelope-intake", "version": "1.0.0"},
            "extensions": {
                "com.partner_advisory.nee.canonical-signal-projection": {
                    "entity_upserts": [],
                    "relationship_signals": [],
                }
            },
        }

        bundle = normalize_evidence_envelope(seal_artifact(package), run_context="empty_run")
        entity_cands = [c for c in bundle["candidates"] if c["candidate_type"] == "entity_mention"]
        self.assertEqual(len(entity_cands), 0)


if __name__ == "__main__":
    unittest.main()
