"""Adversarial and edge-case tests for sealed generation write guards."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from dual_pipeline.core import connect_sqlite
from dual_pipeline.storage.governed_context import (
    GovernedStorageContext,
    ProductionPurityViolationError,
    is_production_database,
    is_sealed_generation_database,
)


def _create_minimal_sqlite_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as conn:
        conn.execute("CREATE TABLE test_data (id INTEGER PRIMARY KEY, val TEXT);")
        conn.execute("INSERT INTO test_data (val) VALUES ('seed_value');")
        conn.commit()


class SealedGenerationWriteGuardAdversarialTest(unittest.TestCase):
    """Adversarial edge-case suite covering boundary payloads, bypass attempts, and lifecycle transitions."""

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="sealed_gen_adv_"))
        self.rt_root = self.tmp_dir / ".local" / "dual_pipeline"
        self.gen_dir = self.rt_root / "generations" / "gen-adv-999"
        self.gen_dir.mkdir(parents=True, exist_ok=True)
        self.graph_file = self.gen_dir / "graph.sqlite"
        _create_minimal_sqlite_db(self.graph_file)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # Category 1: Boundary Payloads
    # -------------------------------------------------------------------------
    def test_adv_boundary_in_memory_allowed_read_write(self) -> None:
        """In-memory database ':memory:' is never classified as sealed generation and allows writes."""
        self.assertFalse(is_sealed_generation_database(":memory:"))
        self.assertFalse(is_production_database(":memory:"))
        conn = connect_sqlite(":memory:", readonly=False)
        try:
            conn.execute("CREATE TABLE mem_tbl (k TEXT);")
            conn.execute("INSERT INTO mem_tbl VALUES ('ok');")
            row = conn.execute("SELECT k FROM mem_tbl").fetchone()
            self.assertEqual(row[0], "ok")
        finally:
            conn.close()

    def test_adv_boundary_non_sqlite_extensions_and_empty_paths(self) -> None:
        """Non-sqlite extensions and arbitrary files in generation directories are not sealed databases."""
        text_file = self.gen_dir / "notes.txt"
        text_file.write_text("not a db", encoding="utf-8")
        self.assertFalse(is_sealed_generation_database(text_file))

        non_gen_file = self.tmp_dir / "regular_dir" / "data.sqlite"
        _create_minimal_sqlite_db(non_gen_file)
        self.assertFalse(is_sealed_generation_database(non_gen_file))

    # -------------------------------------------------------------------------
    # Category 2: Invariant Bypass Attempts
    # -------------------------------------------------------------------------
    def test_adv_invariant_bypass_all_governed_write_entrypoints_fail_closed(self) -> None:
        """All governed write entrypoints fail closed with ProductionPurityViolationError on sealed DB."""
        # Seal generation by adding pair_manifest.json
        (self.gen_dir / "pair_manifest.json").write_text(
            json.dumps({"generation_id": "gen-adv-999"}), encoding="utf-8"
        )
        self.assertTrue(is_sealed_generation_database(self.graph_file))
        self.assertTrue(is_production_database(self.graph_file))

        # 1. connect_sqlite(readonly=False)
        with self.assertRaises(ProductionPurityViolationError):
            connect_sqlite(self.graph_file, readonly=False)

        # 2. GovernedStorageContext.connect(readonly=False)
        with self.assertRaises(ProductionPurityViolationError):
            GovernedStorageContext.connect(self.graph_file, readonly=False)

        # 3. GovernedStorageContext.get_write_connection
        with self.assertRaises(ProductionPurityViolationError):
            GovernedStorageContext.get_write_connection(self.graph_file)

        # 4. GovernedStorageContext.write_context
        with self.assertRaises(ProductionPurityViolationError):
            with GovernedStorageContext.write_context(self.graph_file):
                pass

    def test_adv_invariant_bypass_relative_traversal_path_cannot_evade_guard(self) -> None:
        """Relative path traversal across directory boundaries resolves to canonical target and fails closed."""
        (self.gen_dir / "pair_manifest.json").write_text(
            json.dumps({"generation_id": "gen-adv-999"}), encoding="utf-8"
        )

        sub_dir = self.gen_dir / "subdir"
        sub_dir.mkdir(parents=True, exist_ok=True)
        traversal_path = sub_dir / ".." / "graph.sqlite"

        self.assertTrue(is_sealed_generation_database(traversal_path))
        self.assertTrue(is_production_database(traversal_path))
        with self.assertRaises(ProductionPurityViolationError):
            connect_sqlite(traversal_path, readonly=False)

    # -------------------------------------------------------------------------
    # Category 3: Rollback & Recovery / Lifecycle Transitions
    # -------------------------------------------------------------------------
    def test_adv_lifecycle_transition_from_unsealed_to_sealed(self) -> None:
        """Generation DB allows write connection while unsealed, then locks down once pair_manifest exists."""
        # Stage 1: Unsealed generation permits write access during initial generation build
        self.assertFalse(is_sealed_generation_database(self.graph_file))
        with connect_sqlite(self.graph_file, readonly=False) as conn:
            conn.execute("INSERT INTO test_data (val) VALUES ('unsealed_write');")
            conn.commit()

        # Stage 2: Manifest creation seals the generation
        (self.gen_dir / "pair_manifest.json").write_text(
            json.dumps({"generation_id": "gen-adv-999", "graph_path": "graph.sqlite"}),
            encoding="utf-8",
        )
        self.assertTrue(is_sealed_generation_database(self.graph_file))

        # Stage 3: Write connection immediately blocked
        with self.assertRaises(ProductionPurityViolationError):
            connect_sqlite(self.graph_file, readonly=False)

        # Stage 4: Read-only connection succeeds and reads unsealed data intact
        with connect_sqlite(self.graph_file, readonly=True) as conn:
            rows = conn.execute("SELECT val FROM test_data ORDER BY id").fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["val"], "seed_value")
            self.assertEqual(rows[1]["val"], "unsealed_write")


if __name__ == "__main__":
    unittest.main()
