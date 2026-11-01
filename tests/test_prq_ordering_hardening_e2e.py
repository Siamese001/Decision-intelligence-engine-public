"""
E2E: PR queue ordering hardening (PRQ committee RCA, Waves 1-3).

Builds a throwaway git repository with realistic branches, then drives the real priority engine
exactly the way `pr_governance.py list` does (explicit ahead/behind, no numstat/commits supplied),
and pushes the results through reconciliation + ordering to verify the rendered order end to end.
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import pr_priority_facts as facts
from tools.pr_governance_core.queue_order import assign_sub_ranks, sort_queue
from tools.pr_governance_core.queue_reconcile import (
    compute_risk_tier,
    reconcile_review,
    tier_from_disposition,
)
from tools.pr_priority_engine import _git_sync_status, derive_priority, load_rubric


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def _write(root: Path, rel: str, lines: int, token: str = "x") -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(f"{token}_{i} = {i}\n" for i in range(lines)), encoding="utf-8")


def _commit(root: Path, msg: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", msg)


class PRQHardeningE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = cls.root = Path(cls._tmp.name)
        _git(root, "init", "-q", "-b", "main")
        _git(root, "config", "user.email", "prq@test")
        _git(root, "config", "user.name", "prq")
        _git(root, "config", "core.fsmonitor", "false")
        _write(root, "dual_pipeline/llm_judge_core/judge.py", 40)
        _write(root, "dual_pipeline/transcript_pipeline_core/normalizer.py", 40)
        _write(root, "tools/helper.py", 10)
        _commit(root, "chore: seed")

        # Judge threshold edit + client deliverable, with fix-prefixed commits that do NOT touch P0 paths.
        _git(root, "checkout", "-q", "-b", "feat/live-dociq")
        _write(root, "dual_pipeline/llm_judge_core/judge.py", 48, token="y")
        _commit(root, "feat(nea): lower clarification floor")
        _write(root, "dual_pipeline/transcript_pipeline_core/normalizer.py", 44, token="z")
        _commit(root, "fix(transcript-qa): allow apostrophes")
        _write(root, "output/nea/09-23-2026/recap.md", 300)
        _write(root, "reports/huge_plan.json", 5000)
        _commit(root, "fix(pipeline): heal projections")

        _git(root, "checkout", "-q", "main")
        _git(root, "checkout", "-q", "-b", "feat/token-savings")
        _write(root, "dual_pipeline/token_optimization/budget.py", 120)
        _write(root, "tests/test_budget_e2e.py", 30)
        _commit(root, "feat(cost): cut tokens by 30%")

        _git(root, "checkout", "-q", "main")
        _git(root, "checkout", "-q", "-b", "feat/style-only")
        text = (root / "tools/helper.py").read_text().replace(" = ", "  =  ")
        (root / "tools/helper.py").write_text(text)
        _commit(root, "style: fix trailing whitespaces")
        _git(root, "checkout", "-q", "main")
        cls.rubric = load_rubric()

    @classmethod
    def tearDownClass(cls) -> None:
        facts.clear_facts_cache()
        cls._tmp.cleanup()

    def _as_list_calls(self, branch: str) -> dict:
        ahead, behind, clean = _git_sync_status(branch, self.root)
        return derive_priority(branch, [], {}, self.rubric, ahead=ahead, behind=behind, clean_merge=clean, repo_root=self.root)

    def test_list_call_path_matches_direct_call_path(self) -> None:
        """Root cause #1: explicit ahead (cmd_list) must not switch to placeholder LOC."""
        for b in ("feat/live-dociq", "feat/token-savings", "feat/style-only"):
            via_list = self._as_list_calls(b)
            direct = derive_priority(b, [], {}, self.rubric, repo_root=self.root)
            self.assertEqual(via_list["tier"], direct["tier"], b)
            self.assertEqual(via_list["in_set_prod_loc"], direct["in_set_prod_loc"], b)

    def test_judge_threshold_branch_is_not_cost_tier(self) -> None:
        meta = self._as_list_calls("feat/live-dociq")
        self.assertNotEqual(meta["base_tier"], "P0", meta["explanation"])
        self.assertEqual(meta["base_tier"], "P2")
        self.assertLess(meta["total_prod_loc"], 200, "reports/** must not inflate prod LOC")

    def test_real_cost_branch_reaches_p0(self) -> None:
        meta = self._as_list_calls("feat/token-savings")
        self.assertEqual(meta["base_tier"], "P0")
        self.assertEqual(meta["in_set_prod_loc"], 120)

    def test_style_only_commit_is_cosmetic(self) -> None:
        meta = self._as_list_calls("feat/style-only")
        self.assertEqual(meta["rule_id"], "R5.cosmetic")

    def test_full_queue_pipeline_order(self) -> None:
        rows = []
        specs = [
            ("feat/token-savings", "KEEP", "MERGE_READY", False, "—", {}),
            ("feat/live-dociq", "KEEP", "MERGE_READY", True, "—", {}),
            ("feat/style-only", "KEEP", "SUPERSEDED_TOMBSTONE", False, "—", {"commits_in_main": 0}),
            ("backup/p03", "ARCHIVE", "RESOLVE_CONFLICTS", False, "—", {}),
            ("feat/empty", "PRUNE", "MERGE_READY", False, "—", {}),
        ]
        for b, action, verdict, dirty, waves, sig in specs:
            meta = self._as_list_calls(b) if b.startswith("feat/") and b != "feat/empty" else {"tier": "P1.1", "value": 1.0}
            ahead = 0 if b == "feat/empty" else 1
            tier = tier_from_disposition(meta["tier"], action)
            recon = reconcile_review(verdict, ahead, dirty, sig)
            info = {"behind": 0, "clean_merge": True}
            rows.append({"branch": b, "tier": tier, "value_score": meta.get("value", 0.0),
                         "risk_tier": compute_risk_tier(tier, info, dirty, waves, recon.verdict), "review": recon.verdict})
        sort_queue(rows)
        assign_sub_ranks(rows)
        order = [(r["sub_rank"], r["branch"], r["review"]) for r in rows]
        self.assertEqual(order[0][1], "feat/token-savings", order)
        self.assertEqual(order[-1][0], "A.1", order)
        self.assertEqual(order[-2][0], "D.1", order)
        dociq = next(r for r in rows if r["branch"] == "feat/live-dociq")
        self.assertEqual(dociq["review"], "REVIEW_READY", "dirty rows are never MERGE_READY")
        style = next(r for r in rows if r["branch"] == "feat/style-only")
        self.assertEqual(style["review"], "REVIEW_READY", "tombstone without cherry evidence is downgraded")
        self.assertFalse(any(r["sub_rank"].startswith("COMMIT_FIRST") for r in rows))


if __name__ == "__main__":
    unittest.main()
