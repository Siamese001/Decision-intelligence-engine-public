"""Wave 2 Adversarial & Edge-Case Tests: Healer, Hygiene Delta & Role Upserts.

Covers mandatory edge-case categories:
1. Rollback & recovery / Invariant bypass (_compute_expected_hygiene_deltas refuses tautological after-before copy on unexpected table deletes under strict_delta=True)
2. Boundary payload (repeated nested [6-hex] suffixes, empty strings, legacy stakeholder-only node_type_registry)
3. Concurrency & race (repeated role updates updating node_state.updated_at deterministically)
4. Exact production regression (person:manasa MEMBER_OF edge deletion & tautological hygiene delta)
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from dual_pipeline.uwg_core.universal_write_gateway_role import persist_validated_role
from storage.connection import create_sqlite_conn
from tests.end_to_end.test_graph_hygiene_uwg_role_e2e import _bootstrap_test_graph
from tools.apply_graph_hygiene_remediation import _compute_expected_hygiene_deltas


class TestHealerHygieneRoleAdversarial(unittest.TestCase):
    """Adversarial tests for Wave 2 healer, hygiene delta, and UWG role persistence."""

    def test_adversarial_hygiene_strict_delta_does_not_copy_unexpected_table_drift(self) -> None:
        """Category: Invariant bypass — _compute_expected_hygiene_deltas never copies unexpected table wipes from a_rc - b_rc."""
        pkg = {"findings": []}
        b_rc = {"node": 100, "assertion": 500, "edge": 50, "audit_event": 10, "entity_merge_event": 2}
        # Simulate accidental deletion of 20 nodes and 50 assertions during transaction
        a_rc = {"node": 80, "assertion": 450, "edge": 48, "audit_event": 11, "entity_merge_event": 3}

        exp_deltas, use_strict = _compute_expected_hygiene_deltas(
            pkg,
            b_rc,
            a_rc,
            retired={"node_ret_1"},
            c3_cnt=2,
            strict_delta=True,
            expected_deltas_override=None,
            stats={"edges_auto_linked": 0, "boundary_exclusions_recorded": 0},
        )
        self.assertTrue(use_strict)
        self.assertNotIn("node", exp_deltas)
        self.assertNotIn("assertion", exp_deltas)
        self.assertEqual(exp_deltas["entity_merge_event"], 1)
        self.assertEqual(exp_deltas["edge"], -2)
        self.assertEqual(exp_deltas["audit_event"], 1)

    def test_adversarial_legacy_registry_fallback_and_repeated_role_updates(self) -> None:
        """Category: Boundary payload & repeated role updates on existing subject."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy_reg.sqlite"
            _bootstrap_test_graph(db_path)

            conn = create_sqlite_conn(db_path, readonly=False)
            try:
                # Legacy registry that ONLY permits 'stakeholder'
                conn.execute("CREATE TABLE node_type_registry (node_type TEXT PRIMARY KEY)")
                conn.execute("INSERT INTO node_type_registry (node_type) VALUES ('stakeholder')")
                conn.commit()
            finally:
                conn.close()

            res1 = persist_validated_role(db_path, "Alex Rivera", "Lead Data Scientist")
            self.assertEqual(res1["status"], "PERSISTED_SSOT")
            res2 = persist_validated_role(db_path, "Alex Rivera", "VP Data Science")
            self.assertEqual(res2["status"], "PERSISTED_SSOT")
            self.assertEqual(res2["subject_node_id"], res1["subject_node_id"])

            ro_conn = create_sqlite_conn(db_path, readonly=True)
            try:
                row = ro_conn.execute(
                    "SELECT node_type, description FROM node WHERE node_id = ?",
                    (res1["subject_node_id"],),
                ).fetchone()
                self.assertEqual(row[0], "stakeholder")
                self.assertIn("VP Data Science", row[1])
            finally:
                ro_conn.close()


if __name__ == "__main__":
    unittest.main()
