#!/usr/bin/env python3
"""End-to-End integration test suite for PR latency Wave 2 scope qualification.

Validates:
1. Alignment fixture resolves to exactly 57 committed files (+7331/-535 churn).
2. Governance policies, executable prompts, and AGENTS.md never enter Lane 0 (documentation).
3. Static linter blocker prevents expensive test dispatch (stages 28+) in readiness.
4. Failed-only reuse correctly identifies NOT_RUN and FAIL stages for re-execution while reusing PASS.
5. Benchmark tool phase 'scope' emits chained receipt with verified invariants.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.benchmark_pr_promotion import run_benchmark
from tools.change_lane_classifier import classify_changes, classify_file, load_policy
from tools.lint_blast_radius import (
    evaluate_blast_radius,
    get_git_numstat,
    parse_numstat_line,
    parse_numstat_output,
)
from tools.pr_governance_core.code_review_rules import ChangeClass, classify_file_path
from tools.readiness_core import _build_core_test_modules, _load_prior_failures
from tools.verify_main_readiness import verify_main_readiness

ROOT = Path(__file__).resolve().parents[2]


class TestPRLatencyScopeQualificationE2E(unittest.TestCase):
    """E2E qualification suite for Wave 2 scope and failure propagation."""

    def test_e2e_alignment_fixture_resolves_to_57_files(self) -> None:
        """Verifies that the historical alignment commit resolves to 57 files, not 0."""
        align_sha = "46a60fb68127f4ba3da76f82dce4724255d959d2"
        raw_numstat = get_git_numstat(ROOT, commit_sha=align_sha)
        diff_stats = parse_numstat_output(raw_numstat)
        report = evaluate_blast_radius(diff_stats)

        self.assertEqual(report.total_files, 57, "Alignment fixture must resolve to 57 files")
        self.assertEqual(report.total_added, 7331, "Insertions must match 7331")
        self.assertEqual(report.total_deleted, 535, "Deletions must match 535")
        self.assertEqual(report.total_churn, 7866)

    def test_e2e_governance_and_prompts_never_enter_lane_0(self) -> None:
        """Verifies that governance rules and executable prompt Markdown classify strictly into Lane 2."""
        policy = load_policy(ROOT / "config" / "governance" / "risk_lanes_policy.json")

        critical_paths = [
            "AGENTS.md",
            ".agents/rules/agent-routing.md",
            "governance/decision_intelligence/EXECUTIVE_RECAP_JUDGE_PROMPT.md",
            "config/prompts/synthesis/stages/02_executive_recap.md",
            "dual_pipeline/prompts/contracts/output2_contract.py",
            "governance/policy_registry.yaml",
        ]

        for p in critical_paths:
            lane, reason = classify_file(p, policy)
            self.assertEqual(lane, 2, f"Path '{p}' must be Lane 2, got Lane {lane} ({reason})")

        # Also verify ChangeClass in pr_governance
        self.assertNotEqual(classify_file_path("AGENTS.md"), ChangeClass.DOCUMENTATION)
        self.assertNotEqual(
            classify_file_path("governance/decision_intelligence/EXECUTIVE_RECAP_JUDGE_PROMPT.md"),
            ChangeClass.DOCUMENTATION,
        )
        self.assertNotEqual(
            classify_file_path("config/prompts/synthesis/stages/02_executive_recap.md"),
            ChangeClass.DOCUMENTATION,
        )

        # Standard documentation does map to ChangeClass.DOCUMENTATION
        self.assertEqual(classify_file_path("docs/overview.md"), ChangeClass.DOCUMENTATION)
        self.assertEqual(classify_file_path("documentation/architecture.md"), ChangeClass.DOCUMENTATION)

    def test_e2e_static_blocker_skips_expensive_test_dispatch(self) -> None:
        """Verifies that an injected static linter failure aborts dispatch before stage 28."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)

            stages = [
                (f"Static Gate {i}", ["echo", f"gate_{i}"]) for i in range(1, 28)
            ]
            # Injected failure at static gate 5
            stages[4] = ("Failing Static Gate", ["false"])
            # Expensive test stage at 28
            stages.append(("Core Architectural & Governance Test Suite", ["echo", "expensive_test_should_not_run"]))
            stages.append(("Gate 10: E2E Coverage Audit Score", ["echo", "gate_29"]))
            stages.append(("Grounded Delivery", ["echo", "gate_30"]))

            executed_stages: list[str] = []

            def mock_run_stage(name: str, cmd: list[str], repo_root: Path | None = None):
                executed_stages.append(name)
                if name == "Failing Static Gate":
                    return False, "Simulated static failure", 0.05
                return True, "Passed", 0.05

            with patch("tools.verify_main_readiness.ROOT", tmp_root), \
                 patch("tools.verify_main_readiness._build_stages", return_value=stages), \
                 patch("tools.verify_main_readiness.run_stage", side_effect=mock_run_stage), \
                 patch("tools.readiness_concurrent.run_stage", side_effect=mock_run_stage):
                code = verify_main_readiness(
                    parallel_gates=True,
                    explicit_slug="test_static_blocker",
                    use_cache=False,
                )

                self.assertEqual(code, 1, "Readiness must exit with failure")
                # Stage 28 (Test Suite) must NOT have been dispatched
                self.assertNotIn(
                    "Core Architectural & Governance Test Suite",
                    executed_stages,
                    "Expensive test suite must be skipped when static gates fail",
                )

    def test_e2e_failed_only_reuse_re_executes_not_run_and_failed_stages(self) -> None:
        """Verifies that incremental recheck re-runs NOT_RUN and FAIL stages, reusing only PASS."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            receipt_path = tmp_root / "prior_receipt.json"

            prior_data = {
                "head_commit_sha": "commit_111",
                "qualification_fingerprint": "fp_aaa",
                "terminal_outcome": "FAIL",
                "stages": [
                    {"stage_index": 1, "stage_name": "Stage 1", "status": "PASS"},
                    {"stage_index": 2, "stage_name": "Stage 2", "status": "FAIL"},
                    {"stage_index": 3, "stage_name": "Stage 3", "status": "NOT_RUN"},
                ],
            }
            receipt_path.write_text(json.dumps(prior_data), encoding="utf-8")

            # Matching SHA
            prior_map, failed = _load_prior_failures(receipt_path, expected_head_sha="commit_111")
            self.assertEqual(failed, {2, 3}, "Both FAIL and NOT_RUN must be rechecked")
            self.assertEqual(len(prior_map), 3)

            # Stale / mismatching SHA
            stale_map, stale_failed = _load_prior_failures(receipt_path, expected_head_sha="commit_222")
            self.assertEqual(stale_map, {})
            self.assertEqual(stale_failed, set(), "Stale SHA must result in empty prior cache (full rerun)")

    def test_e2e_benchmark_phase_scope_receipt_chaining(self) -> None:
        """Verifies that running benchmark with --phase scope chains to wave-1.json and validates invariants."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_root = Path(tmpdir)
            pr_latency_dir = tmp_root / "reports" / "pr_latency"
            pr_latency_dir.mkdir(parents=True)

            # Copy real Wave 1 receipt for chaining
            w1_source = ROOT / "reports" / "pr_latency" / "wave-1.json"
            w1_target = pr_latency_dir / "wave-1.json"
            w1_target.write_text(w1_source.read_text(encoding="utf-8"), encoding="utf-8")

            out_receipt = pr_latency_dir / "wave-2.json"
            result = run_benchmark(
                phase="scope",
                receipt_path=out_receipt,
                workloads=["documentation"],
                iterations=1,
                fast=True,
                repo_root=ROOT,
            )

            self.assertEqual(result["terminal_outcome"], "PASS")
            self.assertEqual(result["phase"], "scope")
            self.assertIn("scope_qualification", result)
            self.assertEqual(
                result["scope_qualification"]["alignment_fixture_validation"]["resolved_files_count"],
                57,
            )
            self.assertTrue(out_receipt.is_file())


if __name__ == "__main__":
    unittest.main()
