"""Wave 2 E2E Tests: Healer De-Hardcoding, Deterministic Hygiene Delta & Role Node Type Integrity."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from dual_pipeline.transcript_qa_core.forensic_healer import heal_output_1_forensic
from dual_pipeline.transcript_qa_core.graph_healer import (
    CORE_PROTECTED_ENTITY_IDS,
    _heal_signals,
)
from dual_pipeline.uwg_core.universal_write_gateway_role import persist_validated_role
from storage.connection import create_sqlite_conn
from tests.end_to_end.test_graph_hygiene_uwg_role_e2e import _bootstrap_test_graph
from tools.apply_graph_hygiene_remediation import apply_hygiene_remediation
from tools.tier2_dedup_core.hygiene_promotion import strip_hex_suffixes_on_survivors


class TestHealerHygieneRoleE2E(unittest.TestCase):
    """End-to-end verification of Wave 2 healer de-hardcoding, hygiene delta, and role node types."""

    def test_e2e_healer_contains_zero_hardcoded_person_slugs_or_roster_pruning(self) -> None:
        """Verifies CORE_PROTECTED_ENTITY_IDS has zero person:* literals and _heal_signals preserves valid MEMBER_OF edges."""
        person_entries = [eid for eid in CORE_PROTECTED_ENTITY_IDS if eid.startswith("person:")]
        self.assertEqual(person_entries, [])

        signals = [
            {
                "from_id": "person:manasa",
                "to_id": "org:partner_advisory",
                "predicate": "MEMBER_OF",
                "evidence_ids": ["ev-01"],
            }
        ]
        changes: list[str] = []
        _heal_signals(signals, changes)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["predicate"], "MEMBER_OF")

    def test_e2e_uwg_role_mints_person_node_type_even_with_registry(self) -> None:
        """Verifies net-new person_<slug> subjects are created with node_type='person' when registered."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "uwg_person.sqlite"
            _bootstrap_test_graph(db_path)

            conn = create_sqlite_conn(db_path, readonly=False)
            try:
                conn.execute("CREATE TABLE node_type_registry (node_type TEXT PRIMARY KEY)")
                conn.execute("INSERT INTO node_type_registry (node_type) VALUES ('person'), ('stakeholder')")
                conn.commit()
            finally:
                conn.close()

            res = persist_validated_role(db_path, "Jane Doe", "Principal Cloud Architect")
            self.assertEqual(res["status"], "PERSISTED_SSOT")
            self.assertEqual(res["subject_node_id"], "person_jane_doe")
            self.assertEqual(res["physical_proof"]["overall"], "PASS")

            ro_conn = create_sqlite_conn(db_path, readonly=True)
            try:
                row = ro_conn.execute(
                    "SELECT node_type, description FROM node WHERE node_id = 'person_jane_doe'"
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], "person")
                self.assertIn("Principal Cloud Architect", row[1])
            finally:
                ro_conn.close()

    def test_e2e_idempotent_hex_suffix_stripping_and_hygiene_delta(self) -> None:
        """Verifies strip_hex_suffixes_on_survivors is idempotent across multiple passes."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "hex_strip.sqlite"
            _bootstrap_test_graph(db_path)

            conn = create_sqlite_conn(db_path, readonly=False)
            try:
                conn.execute(
                    "INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, scope_text, currentness_state, updated_at) "
                    "VALUES ('a_hex', 'stakeholder_dan_okray', 'HAS_CLAIM_TEXT', 'Deliver Phase 2 [a1b2c3]', 'Deliver Phase 2 [a1b2c3] [d4e5f6]', 'current', '2026-10-20T00:00:00Z')"
                )
                first_pass = strip_hex_suffixes_on_survivors(conn, "2026-10-28T01:00:00Z")
                second_pass = strip_hex_suffixes_on_survivors(conn, "2026-10-28T02:00:00Z")
                conn.commit()
            finally:
                conn.close()

            self.assertEqual(first_pass, 1)
            self.assertEqual(second_pass, 0)

            ro_conn = create_sqlite_conn(db_path, readonly=True)
            try:
                row = ro_conn.execute(
                    "SELECT scope_text, object_literal, updated_at FROM assertion WHERE assertion_id = 'a_hex'"
                ).fetchone()
                self.assertEqual(row[0], "Deliver Phase 2")
                self.assertEqual(row[1], "Deliver Phase 2")
                self.assertEqual(row[2], "2026-10-28T01:00:00Z")
            finally:
                ro_conn.close()


if __name__ == "__main__":
    unittest.main()
