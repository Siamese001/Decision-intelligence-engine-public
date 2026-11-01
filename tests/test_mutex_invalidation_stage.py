"""Unit and integration tests for Automated Mutex Invalidation & Supersession."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import connect_sqlite
from dual_pipeline.graph_signal_core import (
    find_conflicting_assertions,
    is_functional_mutex_target,
    reconcile_all_graph_mutex,
    supersede_conflicting_assertions,
)


class MutexInvalidationStageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_mutex.sqlite"
        conn = connect_sqlite(self.db_path)
        conn.executescript(
            """
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_node_id TEXT,
                currentness_state TEXT NOT NULL,
                canonical_status TEXT NOT NULL,
                superseded_at TEXT,
                superseded_by_assertion_id TEXT,
                retrieval_eligible INTEGER NOT NULL DEFAULT 1,
                recorded_at TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE search_document (
                search_document_id TEXT PRIMARY KEY,
                assertion_id TEXT NOT NULL,
                currentness_state TEXT NOT NULL,
                retrieval_eligible INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT
            );
            """
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_is_functional_mutex_target(self) -> None:
        self.assertTrue(is_functional_mutex_target("workstream_project_beacon"))
        self.assertTrue(is_functional_mutex_target("pod_enterprise_architecture"))
        self.assertTrue(is_functional_mutex_target("entity-50284712"))
        self.assertTrue(is_functional_mutex_target("urn:workstream:core"))
        self.assertFalse(is_functional_mutex_target("person_lan_tran"))
        self.assertFalse(is_functional_mutex_target("org_partner_advisory"))
        self.assertFalse(is_functional_mutex_target("policy_security_gate"))
        self.assertFalse(is_functional_mutex_target(None))

    def test_find_conflicting_assertions(self) -> None:
        conn = connect_sqlite(self.db_path)
        conn.execute(
            """INSERT INTO assertion VALUES
               ('asrt_old', 'person_alice', 'OWNS_OR_LEADS', 'workstream_sherpa',
                'current', 'canonical', NULL, NULL, 1, '2026-06-15T00:00:00Z', '2026-06-15T00:00:00Z', NULL)"""
        )
        conn.commit()

        # Incoming new lead on same workstream -> Conflict detected
        conflicts = find_conflicting_assertions(
            conn,
            object_node_id="workstream_sherpa",
            predicate="OWNS_OR_LEADS",
            incoming_assertion_id="asrt_new",
            subject_node_id="person_bob",
        )
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["assertion_id"], "asrt_old")
        self.assertEqual(conflicts[0]["conflict_type"], "MUTEX_CARDINALITY_COLLISION")

        # Incoming contributor on same workstream -> No conflict (CONTRIBUTES_TO not mutex)
        no_conflicts = find_conflicting_assertions(
            conn,
            object_node_id="workstream_sherpa",
            predicate="CONTRIBUTES_TO",
            incoming_assertion_id="asrt_new_contrib",
            subject_node_id="person_charlie",
        )
        self.assertEqual(len(no_conflicts), 0)
        conn.close()

    def test_supersede_conflicting_assertions(self) -> None:
        conn = connect_sqlite(self.db_path)
        conn.execute(
            """INSERT INTO assertion VALUES
               ('asrt_old', 'person_alice', 'OWNS_OR_LEADS', 'workstream_sherpa',
                'current', 'canonical', NULL, NULL, 1, '2026-06-15T00:00:00Z', '2026-06-15T00:00:00Z', NULL)"""
        )
        conn.execute(
            """INSERT INTO search_document VALUES
               ('doc_old', 'asrt_old', 'current', 1, '2026-06-15T00:00:00Z')"""
        )
        conn.commit()

        superseded = supersede_conflicting_assertions(
            conn,
            incoming_assertion_id="asrt_new",
            subject_node_id="person_bob",
            predicate="OWNS_OR_LEADS",
            object_node_id="workstream_sherpa",
            now_iso="2026-09-26T12:00:00Z",
        )
        conn.commit()

        self.assertEqual(superseded, ["asrt_old"])

        # Check assertion state
        row = conn.execute(
            "SELECT currentness_state, superseded_at, superseded_by_assertion_id, retrieval_eligible FROM assertion WHERE assertion_id='asrt_old'"
        ).fetchone()
        self.assertEqual(row[0], "historical")
        self.assertEqual(row[1], "2026-09-26T12:00:00Z")
        self.assertEqual(row[2], "asrt_new")
        self.assertEqual(row[3], 0)

        # Check search_document state
        doc_row = conn.execute(
            "SELECT currentness_state, retrieval_eligible FROM search_document WHERE assertion_id='asrt_old'"
        ).fetchone()
        self.assertEqual(doc_row[0], "historical")
        self.assertEqual(doc_row[1], 0)
        conn.close()

    def test_supersede_conflicting_assertions_respects_edge_fk(self) -> None:
        conn = connect_sqlite(self.db_path)
        conn.execute(
            """CREATE TABLE edge (
                edge_id TEXT PRIMARY KEY,
                from_node_id TEXT,
                to_node_id TEXT,
                superseded_by TEXT REFERENCES edge(edge_id),
                updated_at TEXT
            )"""
        )
        conn.execute("INSERT INTO edge VALUES ('edge_1', 'person_alice', 'workstream_sherpa', NULL, '2026-06-15T00:00:00Z')")
        conn.execute(
            """INSERT INTO assertion VALUES
               ('asrt_old_fk', 'person_alice', 'OWNS_OR_LEADS', 'workstream_sherpa',
                'current', 'canonical', NULL, NULL, 1, '2026-06-15T00:00:00Z', '2026-06-15T00:00:00Z', NULL)"""
        )
        conn.execute(
            """INSERT INTO search_document VALUES
               ('doc_old_fk', 'asrt_old_fk', 'current', 1, '2026-06-15T00:00:00Z')"""
        )
        conn.commit()
        superseded = supersede_conflicting_assertions(
            conn,
            incoming_assertion_id="asrt_new_not_an_edge",
            subject_node_id="person_bob",
            predicate="OWNS_OR_LEADS",
            object_node_id="workstream_sherpa",
            now_iso="2026-09-26T12:00:00Z",
        )
        conn.commit()
        self.assertIn("asrt_old_fk", superseded)
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        conn.close()

    def test_reconcile_all_graph_mutex(self) -> None:
        conn = connect_sqlite(self.db_path)
        conn.execute(
            """INSERT INTO assertion VALUES
               ('asrt_1', 'person_1', 'OWNS_OR_LEADS', 'pod_ea', 'current', 'canonical', NULL, NULL, 1, '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z', NULL),
               ('asrt_2', 'person_2', 'OWNS_OR_LEADS', 'pod_ea', 'current', 'canonical', NULL, NULL, 1, '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z', NULL),
               ('asrt_3', 'person_3', 'OWNS_OR_LEADS', 'pod_ea', 'current', 'canonical', NULL, NULL, 1, '2026-09-25T00:00:00Z', '2026-09-25T00:00:00Z', NULL)"""
        )
        conn.commit()

        logs = reconcile_all_graph_mutex(conn, now_iso="2026-09-26T12:00:00Z")
        conn.commit()

        self.assertEqual(len(logs), 2)
        # Winner must be asrt_3 (the latest recorded_at)
        winner_id = logs[0]["winner_assertion_id"]
        self.assertEqual(winner_id, "asrt_3")

        # Verify only asrt_3 remains current
        current_rows = conn.execute(
            "SELECT assertion_id FROM assertion WHERE currentness_state='current'"
        ).fetchall()
        self.assertEqual(len(current_rows), 1)
        self.assertEqual(current_rows[0][0], "asrt_3")
        conn.close()

    def test_subsidiary_org_nodes_do_not_clobber_each_other_and_intra_batch_planned_parity(self) -> None:
        from dual_pipeline.graph_delta_core.counts import _count_mutex_supersessions

        conn = connect_sqlite(self.db_path)
        conn.execute("ALTER TABLE assertion ADD COLUMN scope_text TEXT")
        conn.execute("ALTER TABLE assertion ADD COLUMN qualifiers_json TEXT")
        conn.execute("CREATE TABLE node (node_id TEXT PRIMARY KEY, node_type TEXT, description TEXT)")
        conn.execute("INSERT INTO node VALUES ('org_nee', 'org', 'Apex Global Enterprises parent company.')")
        conn.execute("INSERT INTO node VALUES ('org_neer', 'org', 'Apex Global Enterprises Resources subsidiary.')")
        conn.execute("INSERT INTO node VALUES ('org_fpl', 'org', 'Apex Power and Grid subsidiary.')")
        conn.execute("INSERT INTO node VALUES ('entity-custom-org', 'org', 'Custom regulated utility org.')")
        conn.execute(
            """INSERT INTO assertion VALUES
               ('asrt_nee_bess', 'org_nee', 'OWNS_OR_LEADS', 'entity-bess-1',
                'current', 'canonical', NULL, NULL, 1, '2026-10-30T00:00:00Z', '2026-10-30T00:00:00Z', NULL, NULL, NULL)"""
        )
        conn.commit()

        # Distinct org subjects (org_neer vs org_nee vs entity-custom-org) must NOT clobber each other
        conflicts_neer = find_conflicting_assertions(
            conn,
            object_node_id="entity-bess-1",
            predicate="OWNS_OR_LEADS",
            incoming_assertion_id="asrt_neer_bess",
            subject_node_id="org_neer",
        )
        self.assertEqual(conflicts_neer, [])
        conflicts_custom = find_conflicting_assertions(
            conn,
            object_node_id="entity-bess-1",
            predicate="OWNS_OR_LEADS",
            incoming_assertion_id="asrt_custom_bess",
            subject_node_id="entity-custom-org",
        )
        self.assertEqual(conflicts_custom, [])

        # Intra-batch sequential collisions from the same org track are counted by _count_mutex_supersessions
        ops = [
            {"operation_id": "op1", "operation": "add_relationship", "target_id": "asrt_lead_1"},
            {"operation_id": "op2", "operation": "add_relationship", "target_id": "asrt_lead_2"},
            {"operation_id": "op3", "operation": "add_relationship", "target_id": "asrt_lead_2"},
            {"operation_id": "op4", "operation": "add_candidate_assertion", "target_id": "asrt_skip"},
            {"operation_id": "op5", "operation": "add_relationship", "target_id": "asrt_other_ws"},
            {"operation_id": "op6", "operation": "add_relationship", "target_id": "asrt_lead_3"},
        ]
        payloads = {
            "op1": {"disposition": "APPROVED", "subject_node_id": "person_a", "predicate": "OWNS_OR_LEADS", "object_node_id": "workstream_1"},
            "op2": {"disposition": "APPROVED", "subject_node_id": "person_b", "predicate": "OWNS_OR_LEADS", "object_node_id": "workstream_1"},
            "op3": {"disposition": "APPROVED", "subject_node_id": "person_b", "predicate": "OWNS_OR_LEADS", "object_node_id": "workstream_1"},
            "op4": {"disposition": "CANDIDATE", "subject_node_id": "person_c", "predicate": "HAS_CLAIM_TEXT", "object_node_id": None},
            "op5": {"disposition": "APPROVED", "subject_node_id": "person_d", "predicate": "OWNS_OR_LEADS", "object_node_id": "workstream_2"},
            "op6": {"disposition": "APPROVED", "subject_node_id": "person_b", "predicate": "OWNS_OR_LEADS", "object_node_id": "workstream_1"},
        }
        planned_sup = _count_mutex_supersessions(conn, ops, payloads, set(), [])
        self.assertEqual(planned_sup, 2)
        conn.close()

        from dual_pipeline.graph_delta_core.counts import _row, _sidecar_counts, _table_counts
        self.assertEqual(_table_counts(self.db_path)["assertion"], 1)
        self.assertEqual(_sidecar_counts(self.db_path)["dense_vector"], 0)
        self.assertEqual(_row(component_key="assertion", graph_component="Assertions", baseline=1, planned_delta=1)["planned_after"], 2)
        self.assertEqual(_row(component_key="assertion", graph_component="Assertions", baseline=1, planned_delta=0)["planned_after"], 1)


if __name__ == "__main__":
    unittest.main()
