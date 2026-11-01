"""
tests/test_pr_queue_clarity_e2e.py

E2E: real temporary git repo -> conflict-file detection -> explicit next actions -> P-level ordering.
Reproduces the operator-reported defects:
  1. v1/v2/v3 cost-opt branches showed an identical generic "[KEEP] Active branch with unique work".
  2. P3 rows were listed above P0 rows (priority not in order).
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.pr_governance_core.conflict_triage import analyze_branch_file_diffs
from tools.pr_governance_core.executive_render import format_executive_disposition
from tools.pr_governance_core.observer_actions import observer_next_action
from tools.pr_governance_core.queue_actions import (
    build_siblings_map,
    explain_next_action,
    get_conflict_files,
)
from tools.pr_governance_core.queue_order import assign_sub_ranks, sort_queue


class TestPrQueueClarityE2E(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="pr_queue_clarity_"))
        self.git("init", "-b", "main")
        self.git("config", "user.name", "T")
        self.git("config", "user.email", "t@example.com")
        (self.dir / "implementation_plan.md").write_text("base\n")
        (self.dir / "evaluator.py").write_text("x = 1\n")
        self.git("add", "-A")
        self.git("commit", "-m", "chore: base")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.dir, capture_output=True, text=True, check=True
        ).stdout.strip()

    def _branch(self, name, fname, content, base="main"):
        self.git("checkout", "-q", "-b", name, base)
        (self.dir / fname).write_text(content)
        self.git("add", "-A")
        self.git("commit", "-m", f"docs: {name}")
        self.git("checkout", "-q", "main")

    def _advance_main(self):
        (self.dir / "implementation_plan.md").write_text("main changed\n")
        (self.dir / "evaluator.py").write_text("x = 2\n")
        self.git("add", "-A")
        self.git("commit", "-m", "chore: main moves")

    def test_conflict_files_are_named_and_actions_are_distinct(self):
        self._branch("feat/cost", "evaluator.py", "x = 99\n")
        self._branch("feat/cost-v2", "implementation_plan.md", "plan v2\n")
        self._branch("feat/cost-v3", "implementation_plan.md", "plan v3\n")
        self._advance_main()

        conflicts = {b: get_conflict_files(b, self.dir) for b in ("feat/cost", "feat/cost-v2", "feat/cost-v3")}
        self.assertEqual(conflicts["feat/cost"], ["evaluator.py"])
        self.assertEqual(conflicts["feat/cost-v2"], ["implementation_plan.md"])

        files = {
            "feat/cost": ["evaluator.py"],
            "feat/cost-v2": ["implementation_plan.md"],
            "feat/cost-v3": ["implementation_plan.md"],
        }
        sibs = build_siblings_map(files)
        actions = {
            b: explain_next_action(
                {"branch": b, "behind": 1, "open_waves": "—", "dirty": False},
                conflicts[b], sibs[b], files,
            )
            for b in files
        }

        self.assertTrue(actions["feat/cost"].startswith("[RESOLVE_CONFLICTS]"))
        self.assertIn("evaluator.py", actions["feat/cost"])
        self.assertIn("doc-only sibling plans", actions["feat/cost"])
        self.assertTrue(actions["feat/cost-v2"].startswith("[CONFIRM_TOMBSTONE]"))
        self.assertIn("feat/cost-v3", actions["feat/cost-v2"])
        self.assertTrue(actions["feat/cost-v3"].startswith("[RESOLVE_CONFLICTS]"))
        self.assertEqual(len(set(actions.values())), 3, "each branch must get a distinct action")
        for text in actions.values():
            self.assertNotIn("Active branch with unique work", text)

    def test_clean_branch_has_no_conflict_files(self):
        self._branch("feat/clean", "other.py", "y = 1\n")
        self.assertEqual(get_conflict_files("feat/clean", self.dir), [])

    def test_priority_order_reads_top_to_bottom(self):
        rows = [
            {"branch": "p3-fresh", "tier": "P3.1", "risk_tier": 0, "risk_score": 0},
            {"branch": "p0-diverged", "tier": "P0.1", "risk_tier": 4, "risk_score": 80},
            {"branch": "p0-conflict", "tier": "P0.1", "risk_tier": 3, "risk_score": 100},
            {"branch": "d-prune", "tier": "D.1", "risk_tier": 5, "risk_score": 80},
            {"branch": "p1", "tier": "P1.1", "risk_tier": 4, "risk_score": 80},
        ]
        assign_sub_ranks(sort_queue(rows))
        self.assertEqual(
            [(r["sub_rank"], r["branch"]) for r in rows],
            [("P0.1", "p0-conflict"), ("P0.2", "p0-diverged"), ("P1.1", "p1"),
             ("P3.1", "p3-fresh"), ("D.1", "d-prune")],
        )

    def test_clean_branch_disjoint_main_advance_recommends_merge_plus_pr(self):
        self._branch("feat/disjoint", "disjoint_feature.py", "def disjoint(): return True\n")
        (self.dir / "unrelated_main.py").write_text("unrelated = 1\n")
        self.git("add", "-A")
        self.git("commit", "-m", "chore: unrelated main advance")

        topo = analyze_branch_file_diffs("feat/disjoint", self.dir)
        self.assertEqual(topo.overlap_files, [])
        self.assertEqual(topo.branch_files, ["disjoint_feature.py"])
        self.assertIn("disjoint_feature.py", topo.file_numstat)

        item = {
            "branch": "feat/disjoint",
            "ahead": 1,
            "behind": 1,
            "clean_merge": True,
            "dirty": False,
            "overlap_files": topo.overlap_files,
            "file_numstat": topo.file_numstat,
            "open_waves": "—",
            "readiness_receipt_pass": True,
        }
        disp, action = format_executive_disposition(item)
        self.assertEqual(disp, "Merge + PR")
        self.assertIn("0 shared files modified on main", action)
        self.assertIn("(+1/-0)", action)

        obs_act = observer_next_action(item)
        self.assertTrue(obs_act.startswith("[MERGE]"))
        self.assertIn("0 overlapping files with main", obs_act)

    def test_overlapping_shared_file_recommends_rebase_citing_exact_files_and_numstat(self):
        self._branch("feat/overlap", "shared_logic.py", "def foo(): return 'branch'\n")
        (self.dir / "shared_logic.py").write_text("def foo(): return 'main'\n")
        self.git("add", "-A")
        self.git("commit", "-m", "chore: modify shared on main")

        topo = analyze_branch_file_diffs("feat/overlap", self.dir)
        self.assertEqual(topo.overlap_files, ["shared_logic.py"])
        self.assertEqual(topo.branch_files, ["shared_logic.py"])

        item = {
            "branch": "feat/overlap",
            "ahead": 1,
            "behind": 1,
            "clean_merge": True,
            "dirty": False,
            "overlap_files": topo.overlap_files,
            "file_numstat": topo.file_numstat,
            "open_waves": "—",
        }
        disp, action = format_executive_disposition(item)
        self.assertEqual(disp, "Rebase & PR to Main")
        self.assertIn("shared_logic.py", action)
        self.assertIn("1 shared file(s) changed on main", action)

        explain_act = explain_next_action(item)
        self.assertTrue(explain_act.startswith("[REBASE]"))
        self.assertIn("shared_logic.py", explain_act)

    def test_partial_supersession_recommends_extract_and_archive(self):
        self._branch("feat/partial", "file_unique.py", "unique = True\n")
        self.git("checkout", "-q", "feat/partial")
        (self.dir / "file_absorbed.py").write_text("absorbed_line = 123\n")
        self.git("add", "-A")
        self.git("commit", "-m", "feat: add absorbed file to branch")
        self.git("checkout", "-q", "main")
        (self.dir / "file_absorbed.py").write_text("absorbed_line = 123\n")
        self.git("add", "-A")
        self.git("commit", "-m", "feat: main absorbed this file")

        topo = analyze_branch_file_diffs("feat/partial", self.dir)
        self.assertIn("file_absorbed.py", topo.absorbed_files)
        self.assertIn("file_unique.py", topo.salvageable_files)

        item = {
            "branch": "feat/partial",
            "ahead": 2,
            "behind": 1,
            "clean_merge": True,
            "dirty": False,
            "overlap_files": topo.overlap_files,
            "absorbed_files": topo.absorbed_files,
            "salvageable_files": topo.salvageable_files,
            "file_numstat": topo.file_numstat,
            "open_waves": "—",
        }
        disp, action = format_executive_disposition(item)
        self.assertEqual(disp, "Extract & Archive")
        self.assertIn("file_unique.py", action)
        self.assertIn("file_absorbed.py", action)

        obs_act = observer_next_action(item)
        self.assertTrue(obs_act.startswith("[EXTRACT_AND_ARCHIVE]"))
        self.assertIn("file_unique.py", obs_act)
        self.assertIn("file_absorbed.py", obs_act)


if __name__ == "__main__":
    unittest.main()
