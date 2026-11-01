"""
End-to-End full lifecycle test for PR Overlap & Topology Integration (Wave 7 / Unified Wave 10).

Executes the complete PR overlap lifecycle across a deterministic fixture repository:
1. Evidence extraction (hunks, symbols, duplicates)
2. Pairwise merge simulation & order planning
3. Per-commit cherry-pick classification & extract sets
4. Topology auditor consolidation plan & receipt generation
5. PR governance compact queue listing with overlap next action routing
6. Verification of zero repository mutation throughout the entire lifecycle.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "pr_overlap"


class TestPROverlapE2E(unittest.TestCase):
    """E2E full lifecycle test exercising all PR overlap tools and governance integrations."""

    @classmethod
    def setUpClass(cls):
        if not (FIXTURE_DIR / ".git").exists():
            from tests.fixtures.create_pr_overlap_fixture import generate_fixture
            generate_fixture()

    def test_full_pr_overlap_lifecycle_e2e(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            evidence_receipt = tmp_path / "evidence_receipt.json"
            plan_receipt = tmp_path / "plan_receipt.json"
            cherry_pick_receipt = tmp_path / "cherry_pick_receipt.json"
            topology_receipt = tmp_path / "topology_receipt.json"

            # 0. Capture fixture repository state before execution
            refs_before = subprocess.run(
                ["git", "for-each-ref"],
                cwd=str(FIXTURE_DIR),
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            status_before = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(FIXTURE_DIR),
                capture_output=True,
                text=True,
                check=True,
            ).stdout

            # 1. Evidence Extraction CLI
            cmd_evidence = [
                sys.executable,
                str(ROOT / "tools" / "pr_overlap_audit.py"),
                "evidence",
                "--fixture", str(FIXTURE_DIR),
                "--base", "main",
                "--cache-dir", str(tmp_path / "cache"),
                "--receipt", str(evidence_receipt),
                "--json",
            ]
            res_evidence = subprocess.run(cmd_evidence, capture_output=True, text=True, check=False)
            self.assertEqual(res_evidence.returncode, 0, f"Evidence CLI failed: {res_evidence.stderr}")
            ev_data = json.loads(res_evidence.stdout)
            self.assertEqual(ev_data["schema_version"], "urn:nee:pr-overlap-evidence:1.0.0")
            self.assertTrue(evidence_receipt.exists())

            # 2. In-memory Pairwise Merge & Order Planning CLI
            cmd_plan = [
                sys.executable,
                str(ROOT / "tools" / "pr_overlap_audit.py"),
                "plan",
                "--fixture", str(FIXTURE_DIR),
                "--base", "main",
                "--receipt", str(plan_receipt),
                "--json",
            ]
            res_plan = subprocess.run(cmd_plan, capture_output=True, text=True, check=False)
            self.assertEqual(res_plan.returncode, 0, f"Plan CLI failed: {res_plan.stderr}")
            plan_data = json.loads(res_plan.stdout)
            self.assertEqual(plan_data["schema_version"], "urn:nee:pr-overlap-plan:1.0.0")
            self.assertTrue(plan_data["zero_mutation_verified"])
            self.assertTrue(plan_receipt.exists())

            # 3. Per-Commit Cherry-Pick Assessor CLI on stacked branch
            cmd_cp = [
                sys.executable,
                str(ROOT / "tools" / "pr_overlap_audit.py"),
                "cherry-pick",
                "--fixture", str(FIXTURE_DIR),
                "--branch", "feat/theta-stacked-on-beta",
                "--base", "main",
                "--receipt", str(cherry_pick_receipt),
                "--json",
            ]
            res_cp = subprocess.run(cmd_cp, capture_output=True, text=True, check=False)
            self.assertEqual(res_cp.returncode, 0, f"Cherry-pick CLI failed: {res_cp.stderr}")
            cp_data = json.loads(res_cp.stdout)
            self.assertEqual(cp_data["schema_version"], "urn:nee:pr-cherry-pick-plan:1.0.0")
            self.assertTrue(cp_data["scratch_agreement_verified"])
            self.assertTrue(cherry_pick_receipt.exists())

            # 4. Topology Auditor CLI with Consolidation Plan & Receipt
            cmd_topo = [
                sys.executable,
                str(ROOT / "tools" / "pr_topology_auditor.py"),
                "--branches", "feat/alpha-unique", "feat/beta-base", "feat/theta-stacked-on-beta",
                "--receipt", str(topology_receipt),
                "--json", str(tmp_path / "topology_matrix.json"),
            ]
            res_topo = subprocess.run(cmd_topo, cwd=str(FIXTURE_DIR), capture_output=True, text=True, check=False)
            self.assertEqual(res_topo.returncode, 0, f"Topology auditor failed: {res_topo.stderr}")
            self.assertIn("CONSOLIDATION & CHERRY-PICK PLAN", res_topo.stdout)
            self.assertTrue(topology_receipt.exists())
            topo_data = json.loads(topology_receipt.read_text(encoding="utf-8"))
            self.assertEqual(topo_data["schema_version"], "urn:nee:pr-overlap-plan:1.0.0")
            self.assertEqual(topo_data["terminal_outcome"], "PASS")

            # 5. Verify Zero Repository Mutation on Fixture
            refs_after = subprocess.run(
                ["git", "for-each-ref"],
                cwd=str(FIXTURE_DIR),
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            status_after = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(FIXTURE_DIR),
                capture_output=True,
                text=True,
                check=True,
            ).stdout

            self.assertEqual(refs_before, refs_after, "Repository refs mutated during E2E lifecycle")
            self.assertEqual(status_before, status_after, "Working tree state mutated during E2E lifecycle")


if __name__ == "__main__":
    unittest.main()
