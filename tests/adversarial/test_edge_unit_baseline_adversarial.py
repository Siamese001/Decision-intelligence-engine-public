"""
tests/adversarial/test_edge_unit_baseline_adversarial.py

Adversarial test suite for edge unit baseline audit.
Covers mandatory failure modes:
1. Missing or corrupted active_pair.json fails closed.
2. Missing or unreadable SQLite databases raise explicit errors without silent fallbacks.
3. Corrupted SQLite file headers trigger fail-closed database errors.
4. CLI --fail-on-drift flag properly enforces gate exit codes.
5. Zero mutation invariant: proves files and SHA-256 digests remain bitwise immutable.
"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import (
    read_json,
    sha256_file,
    write_json,
)
from tests.test_edge_unit_baseline import create_mock_runtime
from tools.audit_edge_unit_baseline import (
    main,
    run_edge_unit_baseline_audit,
)


class TestEdgeUnitBaselineAdversarial(unittest.TestCase):
    """Adversarial and failure mode testing for baseline audit tool."""

    def test_missing_active_pair_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            empty_root = Path(tmp_dir)
            with self.assertRaises(FileNotFoundError) as ctx:
                run_edge_unit_baseline_audit(empty_root, show_progress=False)
            self.assertIn("active_pair.json not found", str(ctx.exception))

    def test_missing_pair_paths_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_json(root / "active_pair.json", {"generation_id": "gen-x"})
            with self.assertRaises(ValueError) as ctx:
                run_edge_unit_baseline_audit(root, show_progress=False)
            self.assertIn("missing graph_path or sidecar_path", str(ctx.exception))

    def test_missing_sqlite_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            write_json(
                root / "active_pair.json",
                {
                    "generation_id": "gen-x",
                    "graph_path": "missing_graph.sqlite",
                    "sidecar_path": "missing_sidecar.sqlite",
                },
            )
            with self.assertRaises(FileNotFoundError) as ctx:
                run_edge_unit_baseline_audit(root, show_progress=False)
            self.assertIn("Active graph file not found", str(ctx.exception))

    def test_corrupted_sqlite_header_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            info = create_mock_runtime(Path(tmp_dir), drift_graph=False)
            graph_path = Path(info["graph_path"])

            # Overwrite graph with corrupt non-sqlite bytes
            with open(graph_path, "wb") as f:
                f.write(b"CORRUPTED_NON_SQLITE_HEADER_DATA_1234567890")

            with self.assertRaises(sqlite3.DatabaseError):
                run_edge_unit_baseline_audit(
                    Path(info["runtime_root"]),
                    show_progress=False,
                )

    def test_cli_fail_on_drift_enforces_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            info = create_mock_runtime(Path(tmp_dir), drift_graph=True)
            runtime_root = info["runtime_root"]
            out_file = Path(tmp_dir) / "receipt.json"

            # Default without --fail-on-drift returns 0
            code_default = main([
                "--runtime-root", runtime_root,
                "--output", str(out_file),
                "--json",
            ])
            self.assertEqual(code_default, 0)
            receipt = read_json(out_file)
            self.assertEqual(receipt["status"], "PIN_DRIFT")

            # With --fail-on-drift returns 1
            code_fail = main([
                "--runtime-root", runtime_root,
                "--output", str(out_file),
                "--fail-on-drift",
                "--json",
            ])
            self.assertEqual(code_fail, 1)

    def test_zero_mutation_invariant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            info = create_mock_runtime(Path(tmp_dir), drift_graph=False)
            graph_path = Path(info["graph_path"])
            sidecar_path = Path(info["sidecar_path"])

            before_graph_sha = sha256_file(graph_path)
            before_sidecar_sha = sha256_file(sidecar_path)

            receipt = run_edge_unit_baseline_audit(
                Path(info["runtime_root"]),
                show_progress=False,
            )
            self.assertTrue(receipt["hash_verification"]["zero_mutation_verified"])

            after_graph_sha = sha256_file(graph_path)
            after_sidecar_sha = sha256_file(sidecar_path)

            self.assertEqual(before_graph_sha, after_graph_sha)
            self.assertEqual(before_sidecar_sha, after_sidecar_sha)


if __name__ == "__main__":
    unittest.main()
