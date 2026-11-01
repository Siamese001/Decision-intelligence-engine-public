"""
E2E tests for PR conflict triage and rebase-effort estimation.

Verifies live repository branches reproduce Seat 3 findings and that
a simulated git workflow correctly exercises triage on real git trees.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.pr_governance_core.common import ROOT
from tools.pr_governance_core.conflict_triage import (
    ConflictClass,
    EffortBand,
    triage_branch_conflicts,
)


def _branch_exists(branch: str) -> bool:
    res = subprocess.run(["git", "rev-parse", "--verify", "-q", branch], cwd=ROOT, capture_output=True)
    return res.returncode == 0


class TestPRConflictTriageE2E(unittest.TestCase):
    def test_live_graphdb_freshness_triage(self):
        """feat/graphdb-freshness-agent-panel has 1 generated conflict and is TRIVIAL."""
        if not _branch_exists("feat/graphdb-freshness-agent-panel"):
            self.skipTest("Branch not in repo")
        result = triage_branch_conflicts("feat/graphdb-freshness-agent-panel", ROOT, base="main", use_cache=False)
        self.assertEqual(result.total_files, 1)
        self.assertEqual(result.manual_files_count, 0)
        self.assertEqual(result.effort_band, EffortBand.TRIVIAL)
        self.assertLess(result.effort_score, 1.0)
        self.assertEqual(result.conflicted_files[0].path, "reports/test_timing_db.json")
        self.assertEqual(result.conflicted_files[0].conflict_class, ConflictClass.GENERATED)
        self.assertIn("[RESOLVE_CONFLICTS:TRIVIAL]", result.action_summary)

    def test_live_rca_llm_judge_cost_opt_triage(self):
        """feat/rca-llm-judge-cost-opt has 4 conflicts: 3 generated, 1 plan doc, 0 manual code files."""
        if not _branch_exists("feat/rca-llm-judge-cost-opt"):
            self.skipTest("Branch not in repo")
        result = triage_branch_conflicts("feat/rca-llm-judge-cost-opt", ROOT, base="main", use_cache=False)
        self.assertEqual(result.total_files, 4)
        self.assertEqual(result.manual_files_count, 0)
        self.assertEqual(result.effort_band, EffortBand.LOW)
        self.assertLess(result.effort_score, 4.0)
        self.assertEqual(result.class_counts.get("GENERATED"), 3)
        self.assertEqual(result.class_counts.get("PLAN_DOC"), 1)
        self.assertIn("[RESOLVE_CONFLICTS:LOW]", result.action_summary)

    def test_live_rca_output3_llm_call_council_triage(self):
        """feat/rca-output3-llm-call-council has 7 conflicts: 0 manual files, effort is LOW."""
        if not _branch_exists("feat/rca-output3-llm-call-council"):
            self.skipTest("Branch not in repo")
        result = triage_branch_conflicts("feat/rca-output3-llm-call-council", ROOT, base="main", use_cache=False)
        self.assertEqual(result.total_files, 7)
        self.assertEqual(result.manual_files_count, 0)
        self.assertEqual(result.effort_band, EffortBand.LOW)
        self.assertLess(result.effort_score, 4.0)
        self.assertEqual(result.class_counts.get("GENERATED"), 3)
        self.assertIn("[RESOLVE_CONFLICTS:LOW]", result.action_summary)

    def test_live_rca_mlx_whisper_council_triage(self):
        """feat/rca-mlx-whisper-council has 22 conflicts and effort is HIGH."""
        if not _branch_exists("feat/rca-mlx-whisper-council"):
            self.skipTest("Branch not in repo")
        result = triage_branch_conflicts("feat/rca-mlx-whisper-council", ROOT, base="main", use_cache=False)
        self.assertEqual(result.total_files, 22)
        self.assertEqual(result.effort_band, EffortBand.HIGH)
        self.assertGreaterEqual(result.effort_score, 12.0)
        self.assertGreaterEqual(result.manual_files_count, 2)
        paths = {fc.path for fc in result.conflicted_files}
        self.assertIn("dual_pipeline/audio_ingestion_core/cloud_transcriber.py", paths)
        self.assertIn("[RESOLVE_CONFLICTS:HIGH]", result.action_summary)

    def test_live_dociq_q4_scope_triage(self):
        """feat/live-dociq_q4_scope_jack_partner_advisory has 3 logic conflicts and effort is HIGH."""
        if not _branch_exists("feat/live-dociq_q4_scope_jack_partner_advisory"):
            self.skipTest("Branch not in repo")
        result = triage_branch_conflicts("feat/live-dociq_q4_scope_jack_partner_advisory", ROOT, base="main", use_cache=False)
        self.assertEqual(result.total_files, 3)
        self.assertEqual(result.manual_files_count, 3)
        self.assertEqual(result.effort_band, EffortBand.HIGH)
        self.assertGreaterEqual(result.effort_score, 12.0)
        self.assertEqual(result.class_counts.get("LOGIC"), 3)
        self.assertIn("[RESOLVE_CONFLICTS:HIGH]", result.action_summary)

    def test_e2e_synthetic_repo_generated_conflict(self):
        """End-to-end test on temporary real git repo with generated conflict."""
        temp_dir = tempfile.mkdtemp()
        repo = Path(temp_dir) / "repo"
        repo.mkdir()

        try:
            def run_g(*args):
                return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

            run_g("init", "-b", "main")
            run_g("config", "user.name", "Test User")
            run_g("config", "user.email", "test@example.com")
            run_g("config", "commit.gpgsign", "false")

            # Initial commit on main
            timing_file = repo / "reports" / "test_timing_db.json"
            timing_file.parent.mkdir(parents=True)
            timing_file.write_text('{"test_a": 1.0}\n', encoding="utf-8")
            run_g("add", ".")
            run_g("commit", "-m", "feat: initial commit")

            # Feature branch modifies test_timing_db.json
            run_g("checkout", "-b", "feat/my-feature")
            timing_file.write_text('{"test_a": 1.5, "test_b": 2.0}\n', encoding="utf-8")
            run_g("commit", "-am", "feat: feature update")

            # Main modifies test_timing_db.json divergently
            run_g("checkout", "main")
            timing_file.write_text('{"test_a": 3.0, "test_c": 4.0}\n', encoding="utf-8")
            run_g("commit", "-am", "feat: main update")

            result = triage_branch_conflicts("feat/my-feature", repo, base="main", use_cache=False)
            self.assertEqual(result.total_files, 1)
            self.assertEqual(result.manual_files_count, 0)
            self.assertEqual(result.effort_band, EffortBand.TRIVIAL)
            self.assertEqual(result.conflicted_files[0].conflict_class, ConflictClass.GENERATED)
            self.assertIn("[RESOLVE_CONFLICTS:TRIVIAL]", result.action_summary)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
