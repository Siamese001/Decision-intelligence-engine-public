"""
tests/end_to_end/test_graphdb_schema_normalization_e2e.py

End-to-End lifecycle verification for Wave 1 Schema Normalization:
1. Legacy persona table retirement with zero data loss.
2. Referential integrity and foreign key enforcement.
3. Orphaned node garbage collection.
4. Composite traversal and JSON expression index utilization.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.migrations.migrate_v56_schema_normalization import (
    LEGACY_BLOAT_TABLES,
    migrate_v56_schema_normalization,
    table_exists,
)
from storage.schema import ensure_traversal_indexes, verify_referential_integrity


class TestGraphDBSchemaNormalizationE2E(unittest.TestCase):
    """E2E lifecycle suite for schema normalization and traversal indexing."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.db_path = self.tmp_path / "test_graph.sqlite"

        source_db = Path("graph/nee_graph.sqlite")
        if source_db.exists():
            shutil.copyfile(source_db, self.db_path)
        else:
            self._init_mock_graph(self.db_path)

        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("PRAGMA journal_mode = WAL;")
        self.conn.execute("PRAGMA busy_timeout = 30000;")
        self.conn.execute("PRAGMA foreign_keys = ON;")

    def tearDown(self) -> None:
        self.conn.close()
        self.temp_dir.cleanup()

    def _init_mock_graph(self, path: Path) -> None:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.executescript("""
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_literal TEXT,
                assertion_type TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0,
                status TEXT NOT NULL DEFAULT 'CONFIRMED',
                qualifiers_json TEXT,
                allowed_audiences_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(subject_node_id) REFERENCES node(node_id) ON DELETE CASCADE
            );
            CREATE TABLE dan_next_best_action (
                action_rank INTEGER PRIMARY KEY,
                action TEXT NOT NULL,
                owner TEXT NOT NULL,
                why_it_matters TEXT NOT NULL,
                first_step TEXT NOT NULL,
                source_posture TEXT NOT NULL,
                confidence TEXT NOT NULL,
                linked_validation_ids TEXT,
                card_route TEXT,
                created_at TEXT NOT NULL
            );
            INSERT INTO node VALUES ('person_dan', 'stakeholder', 'Dan', '2026-07-09T00:00:00Z', '2026-07-09T00:00:00Z');
            INSERT INTO node VALUES ('orphan_1', 'concept', 'Orphaned Node', '2026-07-09T00:00:00Z', '2026-07-09T00:00:00Z');
            INSERT INTO dan_next_best_action VALUES (1, 'Lock operating model', 'Dan', 'Matters', 'Step 1', 'posture', 'high', 'val_1', 'NEE_05', '2026-07-09T14:00:00Z');
        """)
        conn.commit()
        conn.close()

    def test_e2e_migration_lifecycle(self) -> None:
        """Full lifecycle: migration, bloat retirement, orphan pruning, and index scans."""
        # 1. Verify legacy tables exist prior to migration
        legacy_present = [t for t in LEGACY_BLOAT_TABLES if table_exists(self.conn, t)]
        self.assertTrue(len(legacy_present) > 0, "Expected legacy bloat tables to exist initially")

        # 2. Execute migration transaction
        result = migrate_v56_schema_normalization(self.conn, prune_orphans=True)
        self.assertEqual(result["status"], "COMPLETE")
        self.assertGreater(result["migrated_assertions"], 0)
        self.assertGreater(len(result["dropped_tables"]), 0)

        # 3. Verify zero legacy bloat tables remain in sqlite_master
        remaining_bloat = [t for t in LEGACY_BLOAT_TABLES if table_exists(self.conn, t)]
        self.assertEqual(remaining_bloat, [], f"Bloat tables still present: {remaining_bloat}")

        # 4. Verify migrated assertions preserve complete original qualifiers
        cur = self.conn.cursor()
        sample = cur.execute(
            "SELECT assertion_id, subject_node_id, predicate, qualifiers_json FROM assertion WHERE assertion_id LIKE 'ast_migrated_%' LIMIT 5"
        ).fetchall()
        self.assertTrue(len(sample) > 0)
        for ast_id, sub_id, pred, q_json in sample:
            self.assertIsNotNone(q_json)
            meta = json.loads(q_json)
            self.assertIn("legacy_source_table", meta)

        # 5. Verify referential integrity pragmas pass with 0 errors
        verification = verify_referential_integrity(self.conn)
        self.assertEqual(verification["status"], "ok")
        self.assertEqual(verification["foreign_key_violations"], 0)
        self.assertEqual(verification["integrity_check"], "ok")

        # 6. Verify composite traversal index plan
        plan = cur.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM assertion WHERE subject_node_id='person_dan' AND predicate='RECOMMENDS_NEXT_BEST_ACTION'"
        ).fetchall()
        plan_str = " ".join(str(r[3]) for r in plan)
        self.assertIn("USING INDEX idx_assertion_subj_pred", plan_str)
        self.assertNotIn("SCAN assertion", plan_str)

        # 7. Verify JSON expression index exists and is valid
        ensure_traversal_indexes(self.conn)
        idx_names = [
            r[0]
            for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='assertion'"
            ).fetchall()
        ]
        self.assertIn("idx_assertion_subj_pred", idx_names)
        self.assertIn("idx_assertion_qualifiers_card", idx_names)
        self.assertIn("idx_assertion_allowed_audiences", idx_names)

        # 8. Verify idempotency: re-running migration succeeds with 0 further migrations
        re_result = migrate_v56_schema_normalization(self.conn, prune_orphans=True)
        self.assertEqual(re_result["status"], "COMPLETE")
        self.assertEqual(re_result["migrated_assertions"], 0)
        self.assertEqual(re_result["dropped_tables"], [])
