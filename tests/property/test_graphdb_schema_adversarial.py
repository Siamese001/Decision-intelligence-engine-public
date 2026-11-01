"""
tests/property/test_graphdb_schema_adversarial.py

Adversarial and Property-Based Verification for Wave 1 Schema Normalization:
1. Referential Integrity Violation Guard (Foreign Key Enforcement).
2. Boundary and Malformed Payloads (Truncated JSON & Empty Strings).
3. Concurrency & Race Invariance (Multi-Threaded WAL Reads/Writes).
4. Transactional Rollback & Crash Recovery.
5. Hypothesis Generative Property Invariance for Assertion Records.
"""
from __future__ import annotations

import concurrent.futures
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

import hypothesis.strategies as st
from hypothesis import given, settings

from dual_pipeline.migrations.migrate_v56_schema_normalization import (
    build_assertion_record,
    migrate_v56_schema_normalization,
)
from storage.schema import ensure_traversal_indexes, verify_referential_integrity


class TestGraphDBSchemaAdversarial(unittest.TestCase):
    """Adversarial and stress test suite for schema normalization and referential safety."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.db_path = self.tmp_path / "adversarial_graph.sqlite"
        self._init_baseline_schema(self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _init_baseline_schema(self, path: Path) -> None:
        conn = sqlite3.connect(path)
        cur = conn.cursor()
        cur.executescript("""
            PRAGMA journal_mode = WAL;
            PRAGMA busy_timeout = 30000;
            PRAGMA foreign_keys = ON;

            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                assertion_fingerprint TEXT,
                subject_node_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_literal TEXT,
                object_node_id TEXT,
                assertion_type TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0,
                status TEXT NOT NULL DEFAULT 'CONFIRMED',
                qualifiers_json TEXT,
                allowed_audiences_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(subject_node_id) REFERENCES node(node_id) ON DELETE CASCADE,
                FOREIGN KEY(object_node_id) REFERENCES node(node_id) ON DELETE CASCADE
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
            INSERT INTO dan_next_best_action VALUES (1, 'Lock operating model', 'Dan', 'Matters', 'Step 1', 'posture', 'high', 'val_1', 'NEE_05', '2026-07-09T14:00:00Z');
        """)
        conn.commit()
        conn.close()

    def test_foreign_key_violation_fails_closed(self) -> None:
        """Category: Boundary / Malformed Input - Inserting invalid FK reference fails closed."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        cur = conn.cursor()

        # Non-existent subject node must raise IntegrityError
        with self.assertRaises(sqlite3.IntegrityError):
            cur.execute("""
                INSERT INTO assertion (assertion_id, subject_node_id, predicate, assertion_type, created_at)
                VALUES ('ast_bad_subject', 'non_existent_node_id', 'TEST_PRED', 'STRATEGIC', '2026-07-09T00:00:00Z')
            """)

        # Non-existent object node must also raise IntegrityError
        with self.assertRaises(sqlite3.IntegrityError):
            cur.execute("""
                INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_node_id, assertion_type, created_at)
                VALUES ('ast_bad_object', 'person_dan', 'TEST_PRED', 'non_existent_object_node', 'STRATEGIC', '2026-07-09T00:00:00Z')
            """)
        conn.close()

    def test_concurrent_wal_writes_and_traversal(self) -> None:
        """Category: Concurrency & Race - Multi-threaded concurrent reads/writes under WAL."""
        migrate_conn = sqlite3.connect(self.db_path)
        migrate_v56_schema_normalization(migrate_conn, prune_orphans=False)
        ensure_traversal_indexes(migrate_conn)
        migrate_conn.close()

        def worker_task(worker_id: int) -> int:
            conn = sqlite3.connect(self.db_path)
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA busy_timeout = 30000;")
            conn.execute("PRAGMA foreign_keys = ON;")
            cur = conn.cursor()

            # Insert thread-isolated assertion
            ast_id = f"ast_thread_{worker_id}"
            cur.execute(
                """
                INSERT INTO assertion (assertion_id, subject_node_id, predicate, object_literal, assertion_type, created_at)
                VALUES (?, 'person_dan', 'RECOMMENDS_NEXT_BEST_ACTION', 'Worker Action', 'STRATEGIC', '2026-07-09T14:00:00Z')
                """,
                (ast_id,),
            )
            conn.commit()

            # Query using composite index
            rows = cur.execute(
                "SELECT count(*) FROM assertion WHERE subject_node_id='person_dan' AND predicate='RECOMMENDS_NEXT_BEST_ACTION'"
            ).fetchone()[0]
            conn.close()
            return rows

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker_task, i) for i in range(16)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(len(results), 16)
        final_conn = sqlite3.connect(self.db_path)
        final_verification = verify_referential_integrity(final_conn)
        self.assertEqual(final_verification["status"], "ok")
        final_conn.close()

    def test_transactional_rollback_recovery(self) -> None:
        """Category: Rollback & Recovery - Failure mid-migration restores clean state."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        cur = conn.cursor()

        # Insert a sabotaging trigger on assertion that forces failure during migration
        cur.execute("""
            CREATE TRIGGER fail_mid_migration BEFORE INSERT ON assertion
            BEGIN
                SELECT RAISE(ABORT, 'Simulated mid-migration failure');
            END;
        """)
        conn.commit()

        # Migration must fail and roll back
        with self.assertRaises(RuntimeError):
            migrate_v56_schema_normalization(conn, prune_orphans=False)

        # Database must still have original legacy table intact with original row
        row_count = cur.execute("SELECT count(*) FROM dan_next_best_action").fetchone()[0]
        self.assertEqual(row_count, 1)

        # Remove the sabotaging trigger and verify clean migration
        cur.execute("DROP TRIGGER fail_mid_migration")
        conn.commit()

        success_result = migrate_v56_schema_normalization(conn, prune_orphans=False)
        self.assertEqual(success_result["status"], "COMPLETE")
        conn.close()

    # ── Property 1: Generative Hypothesis Invariance for Assertion Records ────────

    @settings(max_examples=50, deadline=None)
    @given(
        action=st.text(min_size=1, max_size=200),
        owner=st.text(min_size=1, max_size=50),
        why_it_matters=st.text(min_size=0, max_size=300),
        card_route=st.text(min_size=0, max_size=50),
    )
    def test_property_build_assertion_record_invariants(
        self, action: str, owner: str, why_it_matters: str, card_route: str
    ) -> None:
        """Hypothesis Property: build_assertion_record preserves metadata and valid fingerprint."""
        raw_data = {
            "action_rank": 99,
            "action": action,
            "owner": owner,
            "why_it_matters": why_it_matters,
            "card_route": card_route,
        }
        ast_cols = {
            "assertion_id": "TEXT",
            "assertion_fingerprint": "TEXT",
            "subject_node_id": "TEXT",
            "predicate": "TEXT",
            "object_literal": "TEXT",
            "assertion_type": "TEXT",
            "qualifiers_json": "TEXT",
            "created_at": "TEXT",
        }
        rec = build_assertion_record("dan_next_best_action", raw_data, ast_cols, has_cvr=False)

        # Invariant 1: ID and fingerprint are non-empty strings
        self.assertTrue(len(rec["assertion_id"]) > 0)
        self.assertEqual(len(rec["assertion_fingerprint"]), 64)

        # Invariant 2: qualifiers_json is valid JSON containing all original keys
        parsed_q = json.loads(rec["qualifiers_json"])
        self.assertEqual(parsed_q["action"], action)
        self.assertEqual(parsed_q["owner"], owner)
        self.assertEqual(parsed_q["legacy_source_table"], "dan_next_best_action")
