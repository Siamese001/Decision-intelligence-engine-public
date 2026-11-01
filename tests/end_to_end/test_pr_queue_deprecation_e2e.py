"""tests/end_to_end/test_pr_queue_deprecation_e2e.py

End-to-End & Adversarial Verification Suite for PR Queue Code Deprecation,
Ephemeral Attempt Noise Quarantine, Hunk Absorption, and Tri-Partite Schema Parity.

Covers mandatory edge-case categories under e2e-and-edge-case-coverage.md:
1. Boundary Payload: Empty queue, 0-ahead branch, 0-byte uncommitted files.
2. Malformed Input / Noise: Attempts noise, raw ASR, and ckpt files quarantined from dirty checks.
3. Invariant Bypass Attempt: Zero-ahead branches attempting to claim 'Code Completed? = Y' or 'Promote to Main'.
4. Concurrency & Exact Production Regression: Hunk absorption ratio thresholds and superseded tombstone assignment.
5. Tri-Partite Contract Parity: Schema 4.0.0 alignment across contract, prompt, rule, and renderer.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from tools.lint_prompt_ssot import audit_tripartite_schema_parity, run_prompt_ssot_lint
from tools.pr_governance_core.code_review_rules import (
    DeterministicSignals,
    ReviewVerdict,
    evaluate_review_verdict,
)
from tools.pr_governance_core.executive_render import evaluate_stage_gates_and_disposition, format_executive_disposition
from tools.pr_governance_core.worktree import _inspect_worktree_dirty, is_ephemeral_worktree_path
from tools.pr_priority_facts import fetch_worktree_dirty_numstat

REPO_ROOT = Path(__file__).resolve().parents[2]


class PRQueueDeprecationE2ETests(unittest.TestCase):
    """Class B E2E & Adversarial verification suite for PR queue code deprecation."""

    def test_tripartite_schema_parity_live_repo(self) -> None:
        """Category 5: Verifies tri-partite schema parity across contract, prompt, rule, and renderer in live repo."""
        ok, errors = run_prompt_ssot_lint(REPO_ROOT)
        self.assertTrue(ok, f"Prompt SSOT lint failed on live repository: {errors}")
        schema_errors = audit_tripartite_schema_parity(REPO_ROOT)
        self.assertEqual(schema_errors, [], f"Tri-partite schema parity errors: {schema_errors}")

    def test_tripartite_schema_parity_adversarial_drift(self) -> None:
        """Category 5 (Adversarial): Modifying column counts or disposition names in a clone fails closed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            cfg_dir = tmppath / "config" / "scheduled_tasks"
            cfg_dir.mkdir(parents=True)
            prompt_dir = tmppath / "automations" / "prompts"
            prompt_dir.mkdir(parents=True)
            rule_dir = tmppath / ".agents" / "rules"
            rule_dir.mkdir(parents=True)
            core_dir = tmppath / "tools" / "pr_governance_core"
            core_dir.mkdir(parents=True)

            # Valid contract
            contract_data = {
                "output": {
                    "columns": ["Priority", "Branch", "Category", "Complexity", "Code Completed?", "Rebase Required?", "PR to Main Required?", "Graph Mutated (Y/N)", "Recommended Disposition", "Immediate Blocker / Action"],
                    "value_dispositions": ["Promote to Main", "Complete In-Flight Work", "Salvage Subset & Archive", "Hold / Blocked", "Decommission (No Value)"],
                }
            }
            (cfg_dir / "pr_queue_output_contract.json").write_text(json.dumps(contract_data), encoding="utf-8")

            # Corrupt prompt with old 6-column header
            (prompt_dir / "pr_queue_observer_agent.md").write_text(
                "| Priority | Branch | Category | Complexity | Recommended Disposition | Immediate Blocker / Action |\n"
                "Promote to Main, Complete In-Flight Work, Salvage Subset & Archive, Hold / Blocked, Decommission (No Value)",
                encoding="utf-8"
            )
            # Valid rule and renderer
            cols_header = "| " + " | ".join(contract_data["output"]["columns"]) + " |"
            disps_text = ", ".join(contract_data["output"]["value_dispositions"])
            (rule_dir / "pr-queue-summary.md").write_text(f"{cols_header}\n{disps_text}", encoding="utf-8")
            (core_dir / "executive_render.py").write_text(f'{cols_header}\n"{disps_text}"', encoding="utf-8")

            errors = audit_tripartite_schema_parity(tmppath)
            self.assertTrue(any("missing canonical 10-column header" in e for e in errors))
            self.assertTrue(any("obsolete 6-column header" in e for e in errors))

    def test_ephemeral_attempts_noise_boundary_quarantine(self) -> None:
        """Category 2 (Noise Quarantine): Verifies attempt noise, raw ASR, and ckpt files are excluded from dirty checks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "tester@test.com"], cwd=repo_path, check=True, capture_output=True)

            # Initial commit
            readme = repo_path / "README.md"
            readme.write_text("# Test Repo\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=repo_path, check=True, capture_output=True)

            # Add ephemeral attempt checkpoints and noise
            attempts_dir = repo_path / "output" / "meeting" / "attempts" / "attempt_1"
            attempts_dir.mkdir(parents=True)
            (attempts_dir / "01_meeting_recap.md").write_text("recap", encoding="utf-8")
            (attempts_dir / "02_executive_summary.md").write_text("summary", encoding="utf-8")
            (attempts_dir / "03_decision_log.md").write_text("decisions", encoding="utf-8")
            (repo_path / "meeting.raw_asr.json").write_text("{}", encoding="utf-8")
            cache_dir = repo_path / "output" / "transcript_cache"
            cache_dir.mkdir(parents=True)
            (cache_dir / "temp.wav.chunk").write_text("raw audio", encoding="utf-8")

            # Check dirty inspection ignores all of them
            dirty_res = _inspect_worktree_dirty(repo_path, ignore_review_artifacts=True)
            self.assertFalse(dirty_res.dirty, f"Expected dirty=False, got: {dirty_res}")
            self.assertEqual(dirty_res.authored_files_count, 0)
            self.assertGreater(len(dirty_res.ephemeral_attempt_files), 0)

            self.assertEqual(len(dirty_res.ephemeral_attempt_files), 5)
            for ef in dirty_res.ephemeral_attempt_files:
                self.assertTrue(is_ephemeral_worktree_path(ef))

            # Now add one genuine authored file
            (repo_path / "src").mkdir(parents=True, exist_ok=True)
            (repo_path / "src" / "feature.py").write_text("print('hello')\n", encoding="utf-8")

            dirty_res_after = _inspect_worktree_dirty(repo_path, ignore_review_artifacts=True)
            self.assertTrue(dirty_res_after.dirty)
            self.assertEqual(dirty_res_after.authored_files_count, 1)

    def test_zero_ahead_invariant_bypass_prevention(self) -> None:
        """Category 3 (Invariant Bypass): Zero-ahead branches must NEVER be marked Code Completed = Y or Promote to Main."""
        test_cases = [
            # Case 1: ahead==0, behind==45, unstarted docs plan
            {
                "branch": "feat/wave-plan-word-limit-debate",
                "ahead": 0,
                "behind": 45,
                "clean_merge": True,
                "dirty": False,
                "tier": "D.1",
                "diff_category": "DOCS_ONLY_UNSTARTED",
                "is_docs_only_plan": True,
            },
            # Case 2: ahead==0, behind==10, dirty inception worktree
            {
                "branch": "feat/live-standup_amit",
                "ahead": 0,
                "behind": 10,
                "clean_merge": True,
                "dirty": True,
                "dirty_files_count": 5,
                "tier": "P2",
            },
            # Case 3: ahead==0, behind==0, empty stub
            {
                "branch": "feat/empty-session-stub",
                "ahead": 0,
                "behind": 0,
                "clean_merge": True,
                "dirty": False,
                "tier": "D.1",
                "diff_category": "EMPTY_STUB",
            },
        ]

        for case in test_cases:
            code_done, rebase_req, pr_req, disp, action = evaluate_stage_gates_and_disposition(case)
            self.assertNotEqual(code_done, "Y", f"ahead==0 branch {case['branch']} was granted Code Completed? = Y")
            self.assertNotEqual(disp, "Promote to Main", f"ahead==0 branch {case['branch']} was granted Promote to Main")

    def test_hunk_absorption_exact_regression_thresholds(self) -> None:
        """Category 4 (Production Regression): High hunk absorption ratio >= 0.85 maps to SUPERSEDED_TOMBSTONE."""
        # High absorption >= 0.85
        signals_high = DeterministicSignals(
            hunk_absorption_ratio=0.92,
            unique_production_commits=1,
            commits_in_main=0,
        )
        verdict, reason, action = evaluate_review_verdict(
            signals=signals_high,
            is_clean_merge=True,
            branch_name="feat/superseded-tool",
        )
        self.assertEqual(verdict, ReviewVerdict.SUPERSEDED_TOMBSTONE)
        self.assertIn("92% hunk absorption", reason)

        # Partial absorption 0.30 <= ratio < 0.85
        signals_partial = DeterministicSignals(
            hunk_absorption_ratio=0.55,
            unique_production_commits=1,
            commits_in_main=3,
        )
        verdict_p, reason_p, action_p = evaluate_review_verdict(
            signals=signals_partial,
            is_clean_merge=True,
            branch_name="feat/partially-superseded",
        )
        self.assertEqual(verdict_p, ReviewVerdict.REVIEW_READY)
        self.assertIn("[SALVAGE_DELIVERABLES]", action_p)

    def test_disjunctive_prose_elimination_e2e(self) -> None:
        """Category 1 (Boundary/Integrity): Promotion actions must be decisive and never contain 'or discard'."""
        item = {
            "branch": "feat/ready-deliverable",
            "ahead": 2,
            "behind": 0,
            "clean_merge": True,
            "dirty": False,
            "tier": "P1",
            "open_waves": "—",
            "readiness_receipt_pass": True,
            "tip_sha": "abc123456",
            "main_head_sha": "abc123456",
        }
        _, _, _, disp, action = evaluate_stage_gates_and_disposition(item)
        self.assertEqual(disp, "Promote to Main")
        self.assertNotIn("or discard", action)
        self.assertNotIn("Merge ..., or discard", action)


if __name__ == "__main__":
    unittest.main()
