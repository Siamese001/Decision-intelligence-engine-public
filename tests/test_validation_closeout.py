from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import sha256_file
from dual_pipeline.validation_closeout import (
    apply_validation_closeout_plan,
    build_validation_closeout_plan,
)


class ValidationCloseoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.graph = Path(self.temp.name) / "graph.sqlite"
        connection = sqlite3.connect(self.graph)
        try:
            connection.executescript(
                """
                CREATE TABLE validation_queue (
                  validation_id TEXT PRIMARY KEY, node_id TEXT, edge_id TEXT,
                  claim_id TEXT, validation_question TEXT NOT NULL, owner TEXT NOT NULL,
                  priority TEXT NOT NULL, due_date TEXT, status TEXT NOT NULL,
                  resolution TEXT, resolved_at TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE claim (claim_id TEXT PRIMARY KEY, source_id TEXT);
                CREATE TABLE assertion (
                  assertion_id TEXT PRIMARY KEY, canonical_status TEXT NOT NULL,
                  currentness_state TEXT NOT NULL, canonical_eligible INTEGER NOT NULL,
                  retrieval_eligible INTEGER NOT NULL, validation_state TEXT NOT NULL,
                  primary_source_id TEXT, qualifiers_json TEXT NOT NULL,
                  legacy_record_type TEXT NOT NULL, legacy_record_ids_json TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE audit_event (
                  audit_event_id TEXT PRIMARY KEY, actor_or_process TEXT NOT NULL,
                  action TEXT NOT NULL, record_type TEXT NOT NULL, record_id TEXT NOT NULL,
                  previous_value_json TEXT, new_value_json TEXT, source_id TEXT,
                  event_timestamp TEXT NOT NULL, reason TEXT NOT NULL,
                  validation_reference TEXT, approval_reference TEXT,
                  migration_or_ingestion_run_id TEXT, release_version TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO claim VALUES ('claim-1','source-1')"
            )
            connection.execute(
                """INSERT INTO validation_queue VALUES
                   ('validation-1','node-1',NULL,'claim-1','Was this substantiated?',
                    'Owner','P0','2026-07-15','open',NULL,NULL,'2026-07-08T00:00:00Z')"""
            )
            connection.execute(
                """INSERT INTO assertion VALUES
                   ('assertion-1','validation_pending','current',0,0,'needs_review',
                    'source-1','{}','claim','["claim-1"]','2026-07-08T00:00:00Z')"""
            )
            connection.commit()
        finally:
            connection.close()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_closeout_marks_queue_closed_and_keeps_claim_as_evidence(self) -> None:
        active_pair = {
            "generation_id": "generation-test",
            "graph_sha256": sha256_file(self.graph),
        }
        plan = build_validation_closeout_plan(
            self.graph,
            active_pair=active_pair,
            validation_id="validation-1",
            resolution="Closed as not substantiated by the accountable user decision.",
        )

        self.assertEqual(plan["summary"]["validation_items_closed"], 1)
        self.assertEqual(plan["summary"]["linked_assertions_reclassified"], 1)
        receipt = apply_validation_closeout_plan(
            self.graph,
            plan=plan,
            approval_id="human-decision-test",
            run_id="run-validation-closeout-test",
        )

        self.assertEqual(receipt["operation_count"], 2)
        connection = sqlite3.connect(self.graph)
        try:
            queue = connection.execute(
                "SELECT status,resolution,resolved_at FROM validation_queue"
            ).fetchone()
            self.assertEqual(queue[0], "closed")
            self.assertIn("not substantiated", queue[1])
            self.assertIsNotNone(queue[2])
            assertion = connection.execute(
                """SELECT canonical_status,validation_state,retrieval_eligible,qualifiers_json
                     FROM assertion"""
            ).fetchone()
            self.assertEqual(assertion[:3], ("evidence_only", "unreviewed", 0))
            self.assertEqual(
                json.loads(assertion[3])["nee_validation_closeout"]["disposition"],
                "not_substantiated",
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM audit_event").fetchone()[0],
                2,
            )
        finally:
            connection.close()

    def test_confirm_mapping_promotes_only_the_named_edges(self) -> None:
        graph = Path(self.temp.name) / "confirmation.sqlite"
        connection = sqlite3.connect(graph)
        try:
            connection.executescript(
                """
                CREATE TABLE validation_queue (
                  validation_id TEXT PRIMARY KEY, node_id TEXT, edge_id TEXT,
                  claim_id TEXT, validation_question TEXT NOT NULL, owner TEXT NOT NULL,
                  priority TEXT NOT NULL, due_date TEXT, status TEXT NOT NULL,
                  resolution TEXT, resolved_at TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE claim (
                  claim_id TEXT PRIMARY KEY, subject_node_id TEXT NOT NULL,
                  source_id TEXT NOT NULL, hitl_required INTEGER NOT NULL
                );
                CREATE TABLE edge (
                  edge_id TEXT PRIMARY KEY, from_node_id TEXT NOT NULL, edge_type TEXT NOT NULL,
                  to_node_id TEXT NOT NULL, description TEXT, evidence_posture TEXT NOT NULL,
                  confidence TEXT NOT NULL, materiality TEXT, validation_priority TEXT,
                  validation_owner TEXT, hitl_required INTEGER NOT NULL, source_id TEXT NOT NULL,
                  created_at TEXT, updated_at TEXT, superseded_by TEXT
                );
                CREATE TABLE assertion (
                  assertion_id TEXT PRIMARY KEY, canonical_status TEXT NOT NULL,
                  currentness_state TEXT NOT NULL, canonical_eligible INTEGER NOT NULL,
                  retrieval_eligible INTEGER NOT NULL, validation_state TEXT NOT NULL,
                  confidence_label TEXT NOT NULL, confidence_score REAL NOT NULL,
                  hitl_required INTEGER NOT NULL, validation_priority TEXT,
                  validation_owner TEXT, approval_reference TEXT, primary_source_id TEXT,
                  qualifiers_json TEXT NOT NULL, legacy_record_type TEXT NOT NULL,
                  legacy_record_ids_json TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE assertion_evidence (
                  assertion_id TEXT NOT NULL, evidence_id TEXT NOT NULL,
                  evidence_relationship TEXT NOT NULL
                );
                CREATE TABLE governance_approval (
                  approval_id TEXT PRIMARY KEY, approval_reference TEXT NOT NULL,
                  approval_type TEXT NOT NULL, scope_json TEXT NOT NULL,
                  baseline_zip_sha256 TEXT, baseline_graph_sha256 TEXT,
                  proposal_sha256 TEXT NOT NULL, decision_record_sha256 TEXT,
                  card_proposal_sha256 TEXT, gate_o1_sha256 TEXT,
                  approval_statement_sha256 TEXT NOT NULL, approved_at TEXT NOT NULL,
                  expires_at TEXT, status TEXT NOT NULL, approved_by TEXT NOT NULL,
                  release_version TEXT NOT NULL, active INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE audit_event (
                  audit_event_id TEXT PRIMARY KEY, actor_or_process TEXT NOT NULL,
                  action TEXT NOT NULL, record_type TEXT NOT NULL, record_id TEXT NOT NULL,
                  previous_value_json TEXT, new_value_json TEXT, source_id TEXT,
                  event_timestamp TEXT NOT NULL, reason TEXT NOT NULL,
                  validation_reference TEXT, approval_reference TEXT,
                  migration_or_ingestion_run_id TEXT, release_version TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO claim VALUES ('claim-row','billing','source-roster',1)"
            )
            connection.execute(
                "INSERT INTO claim VALUES ('claim-candidate','billing','source-repair',1)"
            )
            connection.execute(
                """INSERT INTO validation_queue VALUES
                   ('validation-billing','billing','edge-row','claim-row','Confirm mapping',
                    'Owner','P1',NULL,'open',NULL,NULL,'2026-07-08T00:00:00Z')"""
            )
            for edge_id, from_node, to_node in [
                ("edge-pm", "ramya", "billing"),
                ("edge-em", "kaleena", "billing"),
                ("edge-delivery", "billing", "nea-led"),
            ]:
                connection.execute(
                    """INSERT INTO edge VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        edge_id,
                        from_node,
                        "RELATES_TO",
                        to_node,
                        "Candidate one-column-left normalization requires confirmation.",
                        "source attributed",
                        "medium",
                        "high",
                        "P1",
                        "Owner",
                        1,
                        "source-roster",
                        "2026-07-08T00:00:00Z",
                        "2026-07-08T00:00:00Z",
                        None,
                    ),
                )
                connection.execute(
                    """INSERT INTO assertion VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        f"asrt-{edge_id}",
                        "canonical",
                        "current",
                        1,
                        1,
                        "needs_review",
                        "medium",
                        0.75,
                        1,
                        "P1",
                        "Owner",
                        "prior-approval",
                        "source-roster",
                        "{}",
                        "edge",
                        json.dumps([edge_id]),
                        "2026-07-08T00:00:00Z",
                    ),
                )
                connection.execute(
                    "INSERT INTO assertion_evidence VALUES (?,?,?)",
                    (f"asrt-{edge_id}", f"evidence-{edge_id}", "supports"),
                )
            connection.execute(
                """INSERT INTO assertion VALUES
                   ('asrt-candidate','validation_pending','current',0,0,'needs_review',
                    'medium',0.75,1,'P1','Owner',NULL,'source-repair','{}','claim',
                    '[\"claim-candidate\"]','2026-07-08T00:00:00Z')"""
            )
            connection.commit()
        finally:
            connection.close()

        active_pair = {
            "generation_id": "generation-test",
            "graph_sha256": sha256_file(graph),
        }
        plan = build_validation_closeout_plan(
            graph,
            active_pair=active_pair,
            validation_id="validation-billing",
            resolution="User confirms the three Billing Inquiry Journey field alignments.",
            disposition="confirm_mapping",
            confirmed_edge_ids=["edge-pm", "edge-em", "edge-delivery"],
            candidate_claim_ids=["claim-candidate"],
            directive_approval_id="hitl-approval-test",
        )

        self.assertEqual(plan["summary"]["confirmed_mapping_assertions"], 3)
        self.assertEqual(plan["summary"]["confirmed_legacy_edges"], 3)
        self.assertEqual(plan["summary"]["linked_assertions_reclassified"], 1)
        receipt = apply_validation_closeout_plan(
            graph,
            plan=plan,
            approval_id="hitl-approval-test",
            run_id="run-validation-confirmation-test",
        )

        self.assertEqual(receipt["operation_count"], 9)
        connection = sqlite3.connect(graph)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT status FROM validation_queue WHERE validation_id='validation-billing'"
                ).fetchone()[0],
                "closed",
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM edge WHERE hitl_required=0 AND validation_priority IS NULL"
                ).fetchone()[0],
                3,
            )
            self.assertEqual(
                connection.execute(
                    """SELECT count(*) FROM assertion
                         WHERE assertion_id LIKE 'asrt-edge-%'
                           AND validation_state='accepted_legacy'
                           AND hitl_required=0
                           AND approval_reference LIKE 'HITL-VALIDATION-CONFIRMATION-%'"""
                ).fetchone()[0],
                3,
            )
            self.assertEqual(
                connection.execute(
                    """SELECT canonical_status,validation_state,retrieval_eligible
                         FROM assertion WHERE assertion_id='asrt-candidate'"""
                ).fetchone(),
                ("evidence_only", "unreviewed", 0),
            )
            scope = json.loads(
                connection.execute("SELECT scope_json FROM governance_approval").fetchone()[0]
            )
            self.assertEqual(scope["validation_id"], "validation-billing")
            self.assertEqual(len(scope["assertion_ids"]), 3)
            self.assertEqual(connection.execute("SELECT count(*) FROM audit_event").fetchone()[0], 9)
        finally:
            connection.close()

    def test_confirm_canonical_name_retires_the_open_name_question(self) -> None:
        graph = Path(self.temp.name) / "name-confirmation.sqlite"
        connection = sqlite3.connect(graph)
        try:
            connection.executescript(
                """
                CREATE TABLE node (
                  node_id TEXT PRIMARY KEY, node_type TEXT NOT NULL, canonical_name TEXT NOT NULL,
                  display_name TEXT, description TEXT, status TEXT, active INTEGER NOT NULL,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE validation_queue (
                  validation_id TEXT PRIMARY KEY, node_id TEXT, edge_id TEXT,
                  claim_id TEXT, validation_question TEXT NOT NULL, owner TEXT NOT NULL,
                  priority TEXT NOT NULL, due_date TEXT, status TEXT NOT NULL,
                  resolution TEXT, resolved_at TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE claim (
                  claim_id TEXT PRIMARY KEY, subject_node_id TEXT NOT NULL, claim_text TEXT NOT NULL,
                  evidence_posture TEXT NOT NULL, confidence TEXT NOT NULL, materiality TEXT NOT NULL,
                  validation_priority TEXT, validation_owner TEXT, hitl_required INTEGER NOT NULL,
                  source_id TEXT NOT NULL, card_id TEXT NOT NULL, created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL, superseded_by TEXT
                );
                CREATE TABLE edge (
                  edge_id TEXT PRIMARY KEY, from_node_id TEXT NOT NULL, edge_type TEXT NOT NULL,
                  to_node_id TEXT NOT NULL, description TEXT, evidence_posture TEXT NOT NULL,
                  confidence TEXT NOT NULL, materiality TEXT NOT NULL, validation_priority TEXT,
                  validation_owner TEXT, hitl_required INTEGER NOT NULL, source_id TEXT NOT NULL,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, superseded_by TEXT
                );
                CREATE TABLE assertion (
                  assertion_id TEXT PRIMARY KEY, canonical_status TEXT NOT NULL,
                  currentness_state TEXT NOT NULL, canonical_eligible INTEGER NOT NULL,
                  retrieval_eligible INTEGER NOT NULL, validation_state TEXT NOT NULL,
                  confidence_label TEXT NOT NULL, confidence_score REAL NOT NULL,
                  hitl_required INTEGER NOT NULL, validation_priority TEXT,
                  validation_owner TEXT, approval_reference TEXT, primary_source_id TEXT,
                  qualifiers_json TEXT NOT NULL, legacy_record_type TEXT NOT NULL,
                  legacy_record_ids_json TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE canonical_alias (
                  canonical_alias_id TEXT PRIMARY KEY, normalized_alias TEXT NOT NULL,
                  display_alias TEXT NOT NULL, entity_id TEXT NOT NULL, alias_type TEXT NOT NULL,
                  source_id TEXT NOT NULL, confidence_label TEXT NOT NULL, confidence_score REAL NOT NULL,
                  valid_from TEXT, valid_to TEXT, ambiguity_state TEXT NOT NULL,
                  preferred_flag INTEGER NOT NULL, review_state TEXT NOT NULL,
                  active INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE governance_approval (
                  approval_id TEXT PRIMARY KEY, approval_reference TEXT NOT NULL,
                  approval_type TEXT NOT NULL, scope_json TEXT NOT NULL,
                  baseline_zip_sha256 TEXT, baseline_graph_sha256 TEXT,
                  proposal_sha256 TEXT NOT NULL, decision_record_sha256 TEXT,
                  card_proposal_sha256 TEXT, gate_o1_sha256 TEXT,
                  approval_statement_sha256 TEXT NOT NULL, approved_at TEXT NOT NULL,
                  expires_at TEXT, status TEXT NOT NULL, approved_by TEXT NOT NULL,
                  release_version TEXT NOT NULL, active INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE audit_event (
                  audit_event_id TEXT PRIMARY KEY, actor_or_process TEXT NOT NULL,
                  action TEXT NOT NULL, record_type TEXT NOT NULL, record_id TEXT NOT NULL,
                  previous_value_json TEXT, new_value_json TEXT, source_id TEXT,
                  event_timestamp TEXT NOT NULL, reason TEXT NOT NULL,
                  validation_reference TEXT, approval_reference TEXT,
                  migration_or_ingestion_run_id TEXT, release_version TEXT NOT NULL
                );
                """
            )
            now = "2026-07-08T00:00:00Z"
            connection.execute(
                "INSERT INTO node VALUES ('pgd','workstream','PGD CoPilot / technician assistant','PGD CoPilot / technician assistant',NULL,'name TBD',1,?,?)",
                (now, now),
            )
            connection.execute(
                "INSERT INTO node VALUES ('openq','open_question','Official PGD name','Official PGD name',NULL,'open',1,?,?)",
                (now, now),
            )
            connection.execute(
                "INSERT INTO edge VALUES ('edge-name','pgd','HAS_OPEN_QUESTION','openq','Name TBD','directional','medium','high','P1','Owner',1,'source-1',?,?,NULL)",
                (now, now),
            )
            connection.execute(
                "INSERT INTO claim VALUES ('claim-name','pgd','Name TBD','directional','medium','high','P1','Owner',1,'source-1','CARD',?,?,NULL)",
                (now, now),
            )
            connection.execute(
                "INSERT INTO validation_queue VALUES ('validation-name','pgd','edge-name','claim-name','What is the name?','Owner','P1',NULL,'open',NULL,NULL,?)",
                (now,),
            )
            for assertion_id, record_type, record_id, canonical_status in [
                ("asrt-edge", "edge", "edge-name", "canonical"),
                ("asrt-claim", "claim", "claim-name", "validation_pending"),
            ]:
                connection.execute(
                    """INSERT INTO assertion VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        assertion_id, canonical_status, "current", 1 if canonical_status == "canonical" else 0,
                        1 if canonical_status == "canonical" else 0, "needs_review", "medium", 0.75,
                        1, "P1", "Owner", "prior", "source-1", "{}", record_type,
                        json.dumps([record_id]), now,
                    ),
                )
            connection.execute(
                """INSERT INTO canonical_alias VALUES
                   ('alias-bedrock','neebedrock','NEE Bedrock','pgd','legacy_alias','source-1',
                    'high',0.9,'2026-07-01',NULL,'unique',1,'reviewed',1,?)""",
                (now,),
            )
            connection.commit()
        finally:
            connection.close()

        active_pair = {"generation_id": "generation-test", "graph_sha256": sha256_file(graph)}
        plan = build_validation_closeout_plan(
            graph,
            active_pair=active_pair,
            validation_id="validation-name",
            resolution="User confirms PGD CoPilot as the official current name.",
            disposition="confirm_canonical_name",
            confirmed_name="PGD CoPilot",
            directive_approval_id="hitl-approval-test",
        )
        self.assertEqual(plan["summary"]["canonical_name_confirmed"], "PGD CoPilot")
        receipt = apply_validation_closeout_plan(
            graph,
            plan=plan,
            approval_id="hitl-approval-test",
            run_id="run-validation-name-test",
        )

        self.assertEqual(receipt["operation_count"], 10)
        connection = sqlite3.connect(graph)
        try:
            self.assertEqual(
                connection.execute("SELECT canonical_name,status FROM node WHERE node_id='pgd'").fetchone(),
                ("PGD CoPilot", "Official/current name confirmed as PGD CoPilot."),
            )
            self.assertEqual(
                connection.execute("SELECT active FROM node WHERE node_id='openq'").fetchone()[0], 0
            )
            self.assertEqual(
                connection.execute("SELECT status FROM validation_queue").fetchone()[0], "closed"
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM canonical_alias WHERE display_alias='PGD CoPilot' AND preferred_flag=1"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT preferred_flag FROM canonical_alias WHERE canonical_alias_id='alias-bedrock'"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT count(*) FROM assertion WHERE canonical_status='evidence_only' AND hitl_required=0"
                ).fetchone()[0],
                2,
            )
        finally:
            connection.close()

    def test_remove_from_graph_deletes_the_bounded_irrelevant_item(self) -> None:
        graph = Path(self.temp.name) / "removal.sqlite"
        connection = sqlite3.connect(graph)
        try:
            connection.executescript(
                """
                PRAGMA foreign_keys=ON;
                CREATE TABLE node (node_id TEXT PRIMARY KEY);
                CREATE TABLE edge (
                  edge_id TEXT PRIMARY KEY,
                  from_node_id TEXT REFERENCES node(node_id),
                  to_node_id TEXT REFERENCES node(node_id),
                  superseded_by TEXT REFERENCES edge(edge_id)
                );
                CREATE TABLE claim (
                  claim_id TEXT PRIMARY KEY,
                  subject_node_id TEXT REFERENCES node(node_id),
                  source_id TEXT, superseded_by TEXT REFERENCES claim(claim_id)
                );
                CREATE TABLE validation_queue (
                  validation_id TEXT PRIMARY KEY, node_id TEXT REFERENCES node(node_id),
                  edge_id TEXT REFERENCES edge(edge_id), claim_id TEXT REFERENCES claim(claim_id),
                  validation_question TEXT NOT NULL, owner TEXT NOT NULL, priority TEXT NOT NULL,
                  due_date TEXT, status TEXT NOT NULL, resolution TEXT, resolved_at TEXT,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE assertion (
                  assertion_id TEXT PRIMARY KEY,
                  subject_node_id TEXT REFERENCES node(node_id),
                  object_node_id TEXT REFERENCES node(node_id),
                  legacy_record_type TEXT NOT NULL, legacy_record_ids_json TEXT NOT NULL,
                  superseded_by_assertion_id TEXT REFERENCES assertion(assertion_id)
                );
                CREATE TABLE assertion_evidence (
                  assertion_id TEXT REFERENCES assertion(assertion_id) ON DELETE CASCADE, evidence_id TEXT,
                  PRIMARY KEY (assertion_id, evidence_id)
                );
                CREATE TABLE legacy_claim_assertion_map (
                  claim_id TEXT REFERENCES claim(claim_id),
                  assertion_id TEXT REFERENCES assertion(assertion_id),
                  PRIMARY KEY (claim_id)
                );
                CREATE TABLE legacy_edge_assertion_map (
                  edge_id TEXT REFERENCES edge(edge_id),
                  assertion_id TEXT REFERENCES assertion(assertion_id),
                  PRIMARY KEY (edge_id)
                );
                CREATE TABLE search_document (
                  object_type TEXT, object_id TEXT,
                  assertion_id TEXT REFERENCES assertion(assertion_id) ON DELETE CASCADE
                );
                CREATE TABLE canonical_route (object_type TEXT, object_id TEXT);
                CREATE TABLE graph_quality_exception (object_type TEXT, object_id TEXT);
                CREATE TABLE candidate_entity (
                  candidate_entity_id TEXT PRIMARY KEY, matched_node_id TEXT REFERENCES node(node_id)
                );
                CREATE TABLE candidate_assertion (
                  candidate_assertion_id TEXT PRIMARY KEY, subject_candidate_or_node_id TEXT,
                  object_candidate_node_id TEXT
                );
                CREATE TABLE canonical_alias (
                  canonical_alias_id TEXT PRIMARY KEY, entity_id TEXT REFERENCES node(node_id)
                );
                CREATE TABLE alias (alias_id TEXT PRIMARY KEY, node_id TEXT REFERENCES node(node_id));
                CREATE TABLE legacy_alias_canonical_map (
                  alias_id TEXT REFERENCES alias(alias_id), canonical_alias_id TEXT REFERENCES canonical_alias(canonical_alias_id)
                );
                CREATE TABLE entity_resolution_candidate_member (node_id TEXT REFERENCES node(node_id));
                CREATE TABLE node_card_map (node_id TEXT REFERENCES node(node_id));
                CREATE TABLE node_state (node_id TEXT PRIMARY KEY REFERENCES node(node_id));
                CREATE TABLE entity_redirect (
                  from_node_id TEXT REFERENCES node(node_id), to_node_id TEXT REFERENCES node(node_id)
                );
                CREATE TABLE entity_merge_event (canonical_node_id TEXT REFERENCES node(node_id));
                CREATE TABLE node_type_registry (example_node_id TEXT REFERENCES node(node_id));
                CREATE TABLE audit_event (
                  audit_event_id TEXT PRIMARY KEY, actor_or_process TEXT NOT NULL,
                  action TEXT NOT NULL, record_type TEXT NOT NULL, record_id TEXT NOT NULL,
                  previous_value_json TEXT, new_value_json TEXT, source_id TEXT,
                  event_timestamp TEXT NOT NULL, reason TEXT NOT NULL,
                  validation_reference TEXT, approval_reference TEXT,
                  migration_or_ingestion_run_id TEXT, release_version TEXT NOT NULL
                );
                INSERT INTO node VALUES ('risk-incident'), ('risk-parent'), ('pattern-checklist'), ('source-node');
                INSERT INTO edge VALUES
                  ('edge-parent','risk-parent','risk-incident',NULL),
                  ('edge-checklist','risk-incident','pattern-checklist',NULL),
                  ('edge-source','source-node','risk-incident',NULL);
                INSERT INTO claim VALUES ('claim-incident','risk-incident','source-1',NULL);
                INSERT INTO validation_queue VALUES
                  ('validation-incident','risk-incident','edge-parent','claim-incident',
                   'Is this relevant?','Owner','P0','2026-07-15','closed',
                   'Previously closed as evidence-only.', '2026-07-26T00:00:00Z','2026-07-08T00:00:00Z');
                INSERT INTO assertion VALUES
                  ('assertion-claim','risk-incident',NULL,'claim','["claim-incident"]',NULL),
                  ('assertion-parent','risk-parent','risk-incident','edge','["edge-parent"]',NULL),
                  ('assertion-checklist','risk-incident','pattern-checklist','edge','["edge-checklist"]',NULL),
                  ('assertion-source','source-node','risk-incident','edge','["edge-source"]',NULL);
                INSERT INTO legacy_claim_assertion_map VALUES ('claim-incident','assertion-claim');
                INSERT INTO legacy_edge_assertion_map VALUES ('edge-parent','assertion-parent');
                INSERT INTO legacy_edge_assertion_map VALUES ('edge-checklist','assertion-checklist');
                INSERT INTO legacy_edge_assertion_map VALUES ('edge-source','assertion-source');
                INSERT INTO search_document VALUES ('node','risk-incident',NULL), ('claim','claim-incident',NULL), ('assertion','assertion-claim','assertion-claim');
                INSERT INTO canonical_route VALUES ('node','risk-incident'), ('claim','claim-incident'), ('assertion','assertion-claim');
                INSERT INTO graph_quality_exception VALUES ('node','risk-incident');
                INSERT INTO node_state VALUES ('risk-incident');
                INSERT INTO node_card_map VALUES ('risk-incident');
                """
            )
            connection.commit()
        finally:
            connection.close()

        active_pair = {
            "generation_id": "generation-test",
            "graph_sha256": sha256_file(graph),
        }
        plan = build_validation_closeout_plan(
            graph,
            active_pair=active_pair,
            validation_id="validation-incident",
            resolution="User determined the incident is not relevant to NEE.",
            disposition="remove_from_graph",
        )

        self.assertEqual(plan["summary"]["validation_items_removed"], 1)
        self.assertEqual(plan["summary"]["linked_assertions_removed"], 4)
        self.assertEqual(plan["validation_status_before"], "closed")
        receipt = apply_validation_closeout_plan(
            graph,
            plan=plan,
            approval_id="human-decision-test",
            run_id="run-validation-removal-test",
        )

        self.assertEqual(receipt["operation_count"], 1)
        connection = sqlite3.connect(graph)
        try:
            self.assertEqual(connection.execute("SELECT count(*) FROM validation_queue").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM claim").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM edge").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM assertion").fetchone()[0], 0)
            self.assertEqual(
                connection.execute("SELECT count(*) FROM node WHERE node_id='risk-incident'").fetchone()[0],
                0,
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM node WHERE node_id='risk-parent'").fetchone()[0],
                1,
            )
            self.assertEqual(connection.execute("SELECT count(*) FROM search_document").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM audit_event").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
