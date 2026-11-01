"""Adversarial and edge-case tests for NEA Planning Partial Source Retirement & Placeholder Identity Reconciliation.

Covers mandatory edge-case categories:
1. Boundary payloads (missing database file, zero partial assertions already retired).
2. Rollback & recovery (idempotent re-execution on an already remediated graph without corrupting canonical assertions).
3. Concurrency & race / Foreign-key integrity (concurrent read-only WAL snapshot queries during remediation + zero FK violations).
"""
from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from tools.apply_graph_hygiene_remediation import (
    FULL_NEA_SOURCE_ID,
    PARTIAL_NEA_SOURCE_ID,
    UNRESOLVED_JAKE_WELLINGTON_ID,
    retire_nea_planning_partial_duplicates,
)
from tests.test_nea_planning_dedup_hygiene_e2e import _seed_sandbox_with_partial_and_full_nea_sources


class TestNeaPlanningDedupAdversarial(unittest.TestCase):
    """Adversarial suite for partial NEA planning source retirement and entity reconciliation."""

    def test_boundary_missing_graph_file_raises_filenotfound(self) -> None:
        """Category 1: Boundary payload — non-existent database path fails closed."""
        with self.assertRaises(FileNotFoundError):
            retire_nea_planning_partial_duplicates(Path("/tmp/non_existent_nea_sandbox_999999.sqlite"))

    def test_idempotent_re_execution_and_unrelated_canonical_preservation(self) -> None:
        """Category 2: Rollback & recovery — idempotent second run preserves all unrelated and full-source assertions."""
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            sandbox_db = Path(tmp) / "sandbox_graph.sqlite"
            _seed_sandbox_with_partial_and_full_nea_sources(sandbox_db)

            conn = sqlite3.connect(f"file:{sandbox_db}?mode=ro", uri=True)
            s2_before = conn.execute(
                "SELECT COUNT(*) FROM assertion WHERE primary_source_id=?",
                (FULL_NEA_SOURCE_ID,),
            ).fetchone()[0]
            conn.close()

            # First run
            r1 = retire_nea_planning_partial_duplicates(sandbox_db)
            self.assertEqual(r1["terminal_outcome"], "PASS")
            self.assertGreater(r1["remediation_stats"]["retired_assertions_removed"], 0)
            self.assertEqual(r1["remediation_stats"]["jake_wellington_merged"], 1)

            conn_mid = sqlite3.connect(f"file:{sandbox_db}?mode=ro", uri=True)
            canonical_after_first = conn_mid.execute(
                "SELECT COUNT(*) FROM assertion WHERE canonical_status='canonical'"
            ).fetchone()[0]
            conn_mid.close()

            # Second idempotent run
            r2 = retire_nea_planning_partial_duplicates(sandbox_db)
            self.assertEqual(r2["terminal_outcome"], "PASS")
            self.assertEqual(r2["remediation_stats"]["retired_assertions_removed"], 0)
            self.assertEqual(r2["remediation_stats"]["jake_wellington_merged"], 0)

            conn_after = sqlite3.connect(f"file:{sandbox_db}?mode=ro", uri=True)
            s2_after = conn_after.execute(
                "SELECT COUNT(*) FROM assertion WHERE primary_source_id=?",
                (FULL_NEA_SOURCE_ID,),
            ).fetchone()[0]
            canonical_after_second = conn_after.execute(
                "SELECT COUNT(*) FROM assertion WHERE canonical_status='canonical'"
            ).fetchone()[0]
            fk_violations = conn_after.execute("PRAGMA foreign_key_check").fetchall()
            conn_after.close()

            self.assertEqual(s2_after, s2_before)
            self.assertEqual(canonical_after_second, canonical_after_first)
            self.assertEqual(len(fk_violations), 0)

    def test_concurrent_wal_readers_during_remediation(self) -> None:
        """Category 3: Concurrency & race — concurrent WAL read snapshots succeed without OperationalError."""
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            sandbox_db = Path(tmp) / "sandbox_graph.sqlite"
            _seed_sandbox_with_partial_and_full_nea_sources(sandbox_db)

            errors: list[Exception] = []

            def _reader_worker() -> None:
                try:
                    for _ in range(10):
                        c = sqlite3.connect(f"file:{sandbox_db}?mode=ro", uri=True, timeout=30.0)
                        c.execute("SELECT COUNT(*) FROM assertion").fetchone()
                        c.execute("SELECT COUNT(*) FROM node WHERE active=1").fetchone()
                        c.close()
                except Exception as exc:  # pragma: no cover
                    errors.append(exc)

            threads = [threading.Thread(target=_reader_worker) for _ in range(4)]
            for t in threads:
                t.start()
            receipt = retire_nea_planning_partial_duplicates(sandbox_db)
            for t in threads:
                t.join()

            self.assertEqual(errors, [])
            self.assertEqual(receipt["terminal_outcome"], "PASS")
            conn_check = sqlite3.connect(f"file:{sandbox_db}?mode=ro", uri=True)
            s1_rem = conn_check.execute(
                "SELECT COUNT(*) FROM assertion WHERE primary_source_id=?",
                (PARTIAL_NEA_SOURCE_ID,),
            ).fetchone()[0]
            conn_check.close()
            self.assertEqual(s1_rem, 0)


if __name__ == "__main__":
    unittest.main()
