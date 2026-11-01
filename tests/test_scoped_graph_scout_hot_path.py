"""Unit tests for Phase 3 Scoped Graph Scout hot-path integration."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.graph_mutation_ledger import GraphMutationLedger
from dual_pipeline.transcript_pipeline_core.release_runner import (
    _execute_phase3_scoped_scout,
)


def _init_mock_graph(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            description TEXT,
            status TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )"""
    )
    cursor.execute(
        """INSERT INTO node VALUES (
            'project:nexus_core', 'project', 'Project Nexus Core Architecture', 'Nexus Core',
            'Core telemetry platform', 'active', 1, '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z'
        )"""
    )
    cursor.execute(
        """INSERT INTO node VALUES (
            'project:nexus_core_platform', 'project', 'Project Nexus Core Platform Architecture', 'Nexus Core Platform',
            'Core telemetry platform initiative', 'active', 1, '2026-06-15T00:00:00Z', '2026-06-15T00:00:00Z'
        )"""
    )
    conn.commit()
    conn.close()


class TestScopedGraphScoutHotPath(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.out_dir = Path(self.temp_dir.name) / "output"
        self.out_dir.mkdir(parents=True)
        self.rt_dir = Path(self.temp_dir.name) / "runtime"
        self.rt_dir.mkdir(parents=True, exist_ok=True)
        os.environ["NEE_ALLOW_TEST_RUNTIME_ROOT"] = self.temp_dir.name

        self.graph_file = self.rt_dir / "test_graph.sqlite"
        _init_mock_graph(self.graph_file)

        # Write active_pair.json
        with open(self.rt_dir / "active_pair.json", "w") as f:
            json.dump({"graph_path": "test_graph.sqlite"}, f)

        # Write sample Output 03 JSON
        self.f3_path = self.out_dir / "03_test_graph.json"
        f3_payload = {
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "project:nexus_core_platform", "type": "project", "name": "Project Nexus Core Platform Architecture"}
                ]
            }
        }
        with open(self.f3_path, "w") as f:
            json.dump(f3_payload, f)

        # Write initial breakdown md
        self.breakdown_path = self.out_dir / "graph_mutation_breakdown.md"
        self.breakdown_path.write_text("# Graph Mutation Breakdown\n", encoding="utf-8")

    def tearDown(self) -> None:
        os.environ.pop("NEE_ALLOW_TEST_RUNTIME_ROOT", None)
        self.temp_dir.cleanup()

    def test_phase3_scoped_scout_generates_proposals_and_updates_breakdown(self) -> None:
        written = {
            "03_graph": str(self.f3_path),
        }
        metadata = {"meeting_name": "Test Meeting", "run_id": "test-run-123"}

        _execute_phase3_scoped_scout(
            canon_runtime_root=self.rt_dir,
            out_p=self.out_dir,
            written=written,
            metadata=metadata,
            dir_id="dir-test-123",
        )

        # Check proposal was recorded in written dict
        self.assertIn("phase3_scout_proposals", written)
        self.assertTrue(len(written["phase3_scout_proposals"]) >= 1)

        # Check breakdown markdown was updated with Phase 3 section
        breakdown_text = self.breakdown_path.read_text(encoding="utf-8")
        self.assertIn("Scoped Graph Health & Duplicate Scout (Phase 3 Hot Path)", breakdown_text)
        self.assertIn("Proposals Emitted to Queue", breakdown_text)

        # Check ledger recorded package preparation without claiming committed mutations
        ledger = GraphMutationLedger(self.rt_dir / "graph_mutation_ledger.json")
        self.assertEqual(ledger.get_total_preparations(), 1)
        self.assertEqual(ledger.get_total_mutations(), 0)

    def test_phase3_handles_timeout_gracefully(self) -> None:
        written = {
            "03_graph": str(self.f3_path),
        }
        metadata = {"meeting_name": "Test Meeting"}

        # Simulate timeout in ThreadPoolExecutor
        from concurrent.futures import TimeoutError as FuturesTimeoutError
        with patch("concurrent.futures.Future.result", side_effect=FuturesTimeoutError):
            _execute_phase3_scoped_scout(
                canon_runtime_root=self.rt_dir,
                out_p=self.out_dir,
                written=written,
                metadata=metadata,
                dir_id="dir-timeout",
            )

        # Ledger records package preparation without claiming committed mutations even if scout timed out
        ledger = GraphMutationLedger(self.rt_dir / "graph_mutation_ledger.json")
        self.assertEqual(ledger.get_total_preparations(), 1)
        self.assertEqual(ledger.get_total_mutations(), 0)


if __name__ == "__main__":
    unittest.main()
