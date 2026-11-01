"""
tests/end_to_end/test_graph_consolidation_detect_e2e.py

End-to-End lifecycle test for Graph Consolidation Wave 2 detection.
Exercises:
1. Full scan across active graph state using ReleaseManager active_pair resolution.
2. CLI invocation via `nee graph-consolidate-scan`.
3. Output receipt verification: status proposed, schema version 1.0.0, cryptographic digest.
4. Non-mutation invariant: active graph SHA256 is strictly preserved.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_consolidation_core.plan import build_consolidation_plan
from dual_pipeline.release import ReleaseManager
from tools.collect_graph_consolidation_baseline import (
    DEFAULT_RUNTIME_ROOT,
)


class TestGraphConsolidationDetectE2E(unittest.TestCase):
    """End-to-end lifecycle verification for graph consolidation scan."""

    def setUp(self) -> None:
        self.runtime_root = DEFAULT_RUNTIME_ROOT
        self.manager = ReleaseManager(self.runtime_root)
        self.active_pair = self.manager.active_pair()
        self.graph_path = Path(self.manager._resolve(self.active_pair["graph_path"])).resolve()
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_e2e_active_graph_scan_programmatic(self) -> None:
        """Runs full consolidation scan against active graph and verifies contract fields."""
        self.assertTrue(self.graph_path.exists(), f"Active graph not found at {self.graph_path}")
        initial_sha256 = hashlib.sha256(self.graph_path.read_bytes()).hexdigest()

        plan = build_consolidation_plan(
            self.graph_path,
            active_pair=self.active_pair,
        )

        # Plan structure invariants
        self.assertEqual(plan.get("schema_version"), "1.0.0")
        self.assertEqual(plan.get("proposal_type"), "graph_consolidation_plan")
        self.assertEqual(plan.get("status"), "proposed")
        self.assertTrue(plan.get("approval_required"))
        self.assertFalse(plan.get("auto_apply"))
        self.assertEqual(plan.get("base_generation_id"), self.active_pair["generation_id"])
        self.assertEqual(plan.get("base_graph_sha256"), self.active_pair["graph_sha256"])

        # Empirical findings must match active baseline
        summary = plan.get("summary", {})
        self.assertEqual(summary.get("candidate_node_merges_count"), 50)
        self.assertEqual(summary.get("candidate_assertion_supersessions_count"), 40)
        self.assertEqual(summary.get("candidate_evidence_clusters_count"), 155)
        self.assertEqual(summary.get("total_operations_count"), 90)
        self.assertEqual(summary.get("hitl_decisions_required_count"), 10)
        self.assertEqual(summary.get("autonomous_resolutions_count"), 80)

        # Graph file must not have been mutated
        post_sha256 = hashlib.sha256(self.graph_path.read_bytes()).hexdigest()
        self.assertEqual(initial_sha256, post_sha256)

    def test_e2e_cli_invocation_and_file_emission(self) -> None:
        """Invokes `nee graph-consolidate-scan` via CLI and verifies output file."""
        out_file = Path(self.temp_dir.name) / "cli_scan_plan.json"
        cmd = [
            sys.executable,
            "nee.py",
            "graph-consolidate-scan",
            "--runtime-root",
            str(self.runtime_root),
            "--output",
            str(out_file),
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(Path(__file__).resolve().parent.parent.parent),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, f"CLI command failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
        self.assertTrue(out_file.exists(), f"Expected output file {out_file} was not written")

        data = json.loads(out_file.read_text(encoding="utf-8"))
        self.assertEqual(data.get("schema_version"), "1.0.0")
        self.assertEqual(data.get("status"), "proposed")
        self.assertIn("plan_digest", data)
        self.assertEqual(len(data.get("proposed_operations", [])), 90)


if __name__ == "__main__":
    unittest.main()
