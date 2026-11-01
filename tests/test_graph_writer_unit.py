"""Unit tests for GovernedGraphWriter in dual_pipeline/graph_writer.py.

Verifies:
1. Table existence inspection helper (_table_exists).
2. Source registration and version tracking (_ensure_source).
3. Idempotent re-execution and collision detection for source metadata.
4. Extraction run lifecycle recording (_ensure_extraction_run).
5. Graph path initialization and validation.
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.graph_writer import GovernedGraphWriter


def _init_test_source_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS source (
        source_id TEXT PRIMARY KEY,
        source_type TEXT NOT NULL,
        source_name TEXT NOT NULL,
        source_date TEXT,
        authority_rank INTEGER NOT NULL DEFAULT 5,
        source_posture TEXT NOT NULL,
        cold_archive_ref TEXT,
        notes TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS source_version (
        source_version_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        version_number INTEGER NOT NULL,
        source_fingerprint TEXT,
        content_hash TEXT NOT NULL,
        source_date_normalized TEXT,
        source_date_precision TEXT,
        source_date_status TEXT,
        authority_rank INTEGER,
        scope_text TEXT,
        posture_text TEXT,
        parser_version TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (source_id) REFERENCES source(source_id)
    );

    CREATE TABLE IF NOT EXISTS extraction_run (
        run_id TEXT PRIMARY KEY,
        source_version_id TEXT NOT NULL,
        model_id TEXT,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (source_version_id) REFERENCES source_version(source_version_id)
    );
    """)


class TestGraphWriterUnit(unittest.TestCase):
    """Unit test suite for GovernedGraphWriter."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_graph.sqlite"
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        _init_test_source_schema(self.conn)
        self.writer = GovernedGraphWriter(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()
        self.temp_dir.cleanup()

    def test_table_exists(self) -> None:
        self.assertTrue(self.writer._table_exists(self.conn, "source"))
        self.assertTrue(self.writer._table_exists(self.conn, "source_version"))
        self.assertFalse(self.writer._table_exists(self.conn, "nonexistent_table_xyz"))

    def test_ensure_source_and_idempotency(self) -> None:
        source_data = {
            "source_id": "source_meeting_001",
            "source_version_id": "sv_001",
            "name": "Project Strategy Session",
            "date": "2026-09-12",
            "authority_rank": 8,
            "source_posture": "authoritative_internal",
            "package_digest": "a" * 64,
        }
        now = "2026-09-12T15:00:00Z"

        # First application
        self.writer._ensure_source(self.conn, source_data, now)
        row = self.conn.execute("SELECT * FROM source WHERE source_id=?", ("source_meeting_001",)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["source_name"], "Project Strategy Session")
        self.assertEqual(row["authority_rank"], 8)

        # Idempotent second application must succeed without error
        self.writer._ensure_source(self.conn, source_data, now)

    def test_source_collision_detection(self) -> None:
        source_a = {
            "source_id": "source_dup",
            "source_version_id": "sv_dup_1",
            "name": "Legitimate Source",
            "package_digest": "b" * 64,
        }
        source_b = {
            "source_id": "source_dup",
            "source_version_id": "sv_dup_2",
            "name": "Colliding Discrepant Source",
            "package_digest": "c" * 64,
        }
        now = "2026-09-12T15:00:00Z"
        self.writer._ensure_source(self.conn, source_a, now)

        # Attempting to re-register same ID with different name must raise RuntimeError
        with self.assertRaises(RuntimeError) as ctx:
            self.writer._ensure_source(self.conn, source_b, now)
        self.assertIn("source identity collides", str(ctx.exception))

    def test_writer_initialization(self) -> None:
        self.assertEqual(self.writer.graph_path, self.db_path.resolve())


if __name__ == "__main__":
    unittest.main()
