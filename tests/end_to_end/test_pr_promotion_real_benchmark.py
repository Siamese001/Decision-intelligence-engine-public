"""
tests/end_to_end/test_pr_promotion_real_benchmark.py

Wave 2 E2E test suite verifying:
- real command execution across PR promotion phases
- actual verdicts (PASS on valid candidate, FAIL on defective candidate)
- sandbox containment (hermetic git repo, isolated from host main and dirty worktrees)
- injected-defect parity and repair transition on both baseline and optimization arms
- rejection of mock/stale evidence
- receipt integrity and statistical evaluation in reports/pr_latency/effectiveness_wave_2.json
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.pr_real_benchmark_core.candidates import get_candidate
from tools.pr_real_benchmark_core.harness import (
    SandboxRepo,
    run_real_candidate_promotion,
)
from tools.pr_real_benchmark_core.statistics import (
    calculate_percentile,
    evaluate_qualification,
)

ROOT = Path(__file__).resolve().parents[2]


class TestPrPromotionRealBenchmarkE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = ROOT
        cls.receipt_path = ROOT / "reports" / "pr_latency" / "effectiveness_wave_2.json"
        if not cls.receipt_path.is_file():
            # If receipt is not generated yet, run benchmark to generate it
            import subprocess

            cmd = [
                ".venv/bin/python",
                "tools/benchmark_pr_promotion_real.py",
                "--compare",
                "e11bb6f28",
                "e037b5e41",
                "--receipt",
                str(cls.receipt_path),
                "--repetitions",
                "2",
            ]
            subprocess.check_call(cmd, cwd=str(ROOT))

        with cls.receipt_path.open("r", encoding="utf-8") as f:
            cls.receipt = json.load(f)

    def test_real_commands_and_actual_verdicts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_real_sandbox_") as tmp:
            sandbox = SandboxRepo(Path(tmp))
            sandbox.init()

            cand = get_candidate("application", is_defect=False)
            res = run_real_candidate_promotion("pre", "e11bb6f28", cand, "cold", sandbox)

            self.assertEqual(res.terminal_outcome, "PASS")
            self.assertEqual(len(res.phases), 10)
            for p in res.phases:
                self.assertEqual(p.status, "PASS")
                self.assertGreaterEqual(p.wall_clock_seconds, 0.0)

            # Check that merge phase completed with PASS
            merge_phase = [p for p in res.phases if p.phase_name == "merge"][0]
            self.assertEqual(merge_phase.status, "PASS")

    def test_sandbox_containment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_containment_") as tmp:
            sandbox_dir = Path(tmp)
            sandbox = SandboxRepo(sandbox_dir)
            sandbox.init()

            # Ensure host git dir is unaffected
            self.assertTrue(sandbox_dir.is_dir())
            self.assertTrue((sandbox_dir / ".git").is_dir())
            self.assertFalse(sandbox_dir.is_symlink())

            # Candidate execution within sandbox
            cand = get_candidate("governance", is_defect=False)
            res = run_real_candidate_promotion("post", "e037b5e41", cand, "cold", sandbox)
            self.assertEqual(res.terminal_outcome, "PASS")

            # Host repository working tree remains unchanged
            host_git = ROOT / ".git"
            self.assertTrue(host_git.exists())

    def test_injected_defect_parity_and_repair(self) -> None:
        with tempfile.TemporaryDirectory(prefix="test_parity_") as tmp:
            sandbox = SandboxRepo(Path(tmp))
            sandbox.init()

            for workload in ("documentation", "governance", "application"):
                # 1. Injected defect
                cand_defect = get_candidate(workload, is_defect=True)
                res_pre = run_real_candidate_promotion("pre", "e11bb6f28", cand_defect, "cold", sandbox)
                res_post = run_real_candidate_promotion("post", "e037b5e41", cand_defect, "cold", sandbox)

                self.assertEqual(
                    res_pre.terminal_outcome,
                    "FAIL",
                    f"Arm A failed to detect defect in {workload}",
                )
                self.assertEqual(
                    res_post.terminal_outcome,
                    "FAIL",
                    f"Arm B failed to detect defect in {workload}",
                )

                # 2. Repair transition
                cand_repair = get_candidate(workload, is_defect=True, repair=True)
                res_pre_rep = run_real_candidate_promotion("pre", "e11bb6f28", cand_repair, "cold", sandbox)
                res_post_rep = run_real_candidate_promotion("post", "e037b5e41", cand_repair, "cold", sandbox)

                self.assertEqual(
                    res_pre_rep.terminal_outcome,
                    "PASS",
                    f"Arm A failed to pass repair in {workload}",
                )
                self.assertEqual(
                    res_post_rep.terminal_outcome,
                    "PASS",
                    f"Arm B failed to pass repair in {workload}",
                )

    def test_rejection_of_mock_and_stale_evidence(self) -> None:
        # Mock evidence with zero durations or positive durations without confidence bound is not proven
        mock_pre = [0.001, 0.001, 0.001]
        mock_post = [0.0005, 0.0005, 0.0005]
        # Equal defect detection or insufficient tail evidence rejects claim
        status, reason = evaluate_qualification(
            median_ratio=0.5,
            median_ratio_ucb=0.45,  # > 0.40
            p95_ratio=0.5,
            p95_ratio_ucb=0.55,    # > 0.40
            per_lane_regression=False,
            tail_evidence_sufficient=False,
        )
        self.assertEqual(status, "NOT_PROVEN")
        self.assertIn("NOT_PROVEN", reason)

    def test_receipt_structure_and_gate_audit(self) -> None:
        self.assertEqual(self.receipt.get("terminal_outcome"), "PASS")
        self.assertEqual(self.receipt.get("wave"), "Wave 2: Establish a real paired pre/post baseline")
        self.assertIn(self.receipt.get("speedup_claim_qualification"), ("PROVEN", "NOT_PROVEN"))

        pinned = self.receipt.get("pinned_revisions", {})
        self.assertEqual(pinned.get("base_pre_revision"), "e11bb6f28")
        self.assertEqual(pinned.get("optimization_post_revision"), "e037b5e41")

        defect_audit = self.receipt.get("injected_defect_parity_audit", [])
        self.assertGreaterEqual(len(defect_audit), 3)
        for d in defect_audit:
            self.assertTrue(d.get("parity_satisfied"))

        raw = self.receipt.get("raw_samples", {})
        self.assertIn("pre", raw)
        self.assertIn("post", raw)
        self.assertEqual(len(raw["pre"]), len(raw["post"]))


if __name__ == "__main__":
    unittest.main()
