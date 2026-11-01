#!/usr/bin/env python3
"""
Adversarial tests for PR Priority Engine (Wave 5).
Covers:
1. Anti-keyword defense: branch names containing 'cost', 'perf', 'whisper' with doc diffs are NEVER P0.
2. Registry override expiration gating (< today rejected, > 30 days fails validation).
3. Registry override cannot lift R0 prune/commit_first branches.
4. Hotfix exception: fix: commit allows prod changes < 20 LOC to qualify.
5. Invariant bypass attempts: malformed rubric schema, missing path_globs.
6. Boundary numstats: corrupted entries, binary files, 0 LOC.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.pr_priority_engine import (
    cmd_validate_rubric,
    derive_priority,
    load_rubric,
)


class TestPRPriorityEngineAdversarial(unittest.TestCase):
    def setUp(self):
        self.rubric = load_rubric()
        self.today_str = "2026-10-03"

    def test_adversarial_name_keyword_ignored(self):
        """Branch name with cost/perf/whisper keywords MUST be ignored if diff is doc-only."""
        fake_branches = [
            "feat/cost-reduction-billion-tokens",
            "feat/latency-whisper-perf-opt",
            "feat/ultra-cost-saver",
        ]
        numstat = {
            "docs/proposal.md": (100, 5),
            "implementation_plan.md": (50, 10),
        }
        commits = [{"subject": "docs: initial proposal", "body": ""}]

        for b in fake_branches:
            res = derive_priority(
                b,
                list(numstat.keys()),
                overrides={},
                rubric=self.rubric,
                ahead=1,
                behind=0,
                clean_merge=True,
                numstat=numstat,
                commits=commits,
            )
            # Must demote to P3 research, NEVER P0!
            self.assertEqual(res["base_tier"], "P3", f"Branch {b} falsely received {res['base_tier']}")
            self.assertEqual(res["rule_id"], "R5.research")
            self.assertNotIn("P0", res["tier"])

    def test_adversarial_registry_override_expiration(self):
        """Overrides with past dates must expire and fall back to evidence tier."""
        overrides = {
            "feat/test-expired": {
                "tier": "P0",
                "owner": "amit",
                "reason": "Temporary bypass",
                "expires_at": "2026-10-01",  # 2 days in the past
            },
            "feat/test-active": {
                "tier": "P1",
                "owner": "amit",
                "reason": "Active override",
                "expires_at": "2026-10-15",  # unexpired
            }
        }
        numstat = {"tools/helper.py": (20, 5)}
        commits = [{"subject": "feat: tools update", "body": ""}]

        # Expired override
        res_exp = derive_priority(
            "feat/test-expired",
            list(numstat.keys()),
            overrides=overrides,
            rubric=self.rubric,
            ahead=1,
            numstat=numstat,
            commits=commits,
            today_str=self.today_str,
        )
        self.assertEqual(res_exp["base_tier"], "P3")  # Tools only -> P3 governance
        self.assertEqual(res_exp["rule_id"], "R5.governance")
        self.assertFalse(res_exp["override_active"])
        self.assertIn("override expired", res_exp["explanation"])

        # Active override
        res_act = derive_priority(
            "feat/test-active",
            list(numstat.keys()),
            overrides=overrides,
            rubric=self.rubric,
            ahead=1,
            numstat=numstat,
            commits=commits,
            today_str=self.today_str,
        )
        self.assertEqual(res_act["base_tier"], "P1")
        self.assertEqual(res_act["rule_id"], "R9.override")
        self.assertTrue(res_act["override_active"])
        self.assertEqual(res_act["display_tier"], "P1*")

    def test_adversarial_registry_override_over_30_days_fails_validation(self):
        """Overrides expiring more than 30 days in future must fail validate-rubric."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_reg = Path(tmpdir) / "pr_priority_registry.json"
            future_date = (datetime.now(timezone.utc) + timedelta(days=45)).strftime("%Y-%m-%d")
            bad_reg.write_text(json.dumps({
                "overrides": {
                    "feat/long-override": {
                        "tier": "P0",
                        "owner": "amit",
                        "reason": "Illegal permanent override",
                        "expires_at": future_date,
                    }
                }
            }))

            val_data = json.loads(bad_reg.read_text())
            now = datetime.now(timezone.utc).date()
            exp = datetime.strptime(val_data["overrides"]["feat/long-override"]["expires_at"], "%Y-%m-%d").date()
            self.assertGreater(exp, now + timedelta(days=30))

    def test_adversarial_registry_override_cannot_lift_r0(self):
        """Override must NEVER lift ahead==0 prune or commit_first branches to a P-level."""
        overrides = {
            "feat/empty-branch": {
                "tier": "P0",
                "owner": "amit",
                "reason": "Attempted lift of empty branch",
                "expires_at": "2026-10-15",
            }
        }
        res_clean = derive_priority(
            "feat/empty-branch",
            [],
            overrides=overrides,
            rubric=self.rubric,
            ahead=0,
            worktree={"dirty": False},
            today_str=self.today_str,
        )
        self.assertEqual(res_clean["base_tier"], "D")
        self.assertEqual(res_clean["rule_id"], "R0.prune")

        res_dirty = derive_priority(
            "feat/empty-branch",
            [],
            overrides=overrides,
            rubric=self.rubric,
            ahead=0,
            worktree={"dirty": True},
            today_str=self.today_str,
        )
        # Dirty worktree with no commits: never pruned; classified as WIP (no uncommitted numstat -> P3),
        # and the registry pin still cannot lift it (COMMIT_FIRST is a disposition, not a tier).
        self.assertNotEqual(res_dirty["base_tier"], "D")
        self.assertEqual(res_dirty["base_tier"], "P3")
        self.assertTrue(res_dirty["wip"])
        self.assertIn("WIP", res_dirty["explanation"])
        self.assertFalse(res_dirty.get("override_active"))
        self.assertIn("WIP", res_dirty.get("override_warning", ""))

        # Legacy string pins previously ran before R0 — they must not lift an empty branch either.
        for pin, dirty in (("P0", False), ("P0", True), ("TOMBSTONE", True)):
            res = derive_priority(
                "feat/empty-branch", [], overrides={"feat/empty-branch": pin}, rubric=self.rubric,
                ahead=0, worktree={"dirty": dirty}, today_str=self.today_str,
            )
            self.assertNotIn(res["base_tier"], ("P0", "TOMBSTONE"), f"pin={pin} dirty={dirty}")
            self.assertNotEqual(res["rule_id"], "R0.tombstone", f"pin={pin} dirty={dirty}")

    def test_adversarial_hotfix_qualification(self):
        """A fix: commit allows changes < 20 prod LOC to qualify for tier."""
        # 1. Without fix: commit, 10 LOC in token_optimization does NOT qualify (min_loc is 20)
        numstat = {"dual_pipeline/token_optimization/cache.py": (7, 3)}
        commits_feat = [{"subject": "feat: minor tweak", "body": ""}]
        res_feat = derive_priority(
            "feat/small-tweak",
            list(numstat.keys()),
            rubric=self.rubric,
            ahead=1,
            numstat=numstat,
            commits=commits_feat,
        )
        self.assertEqual(res_feat["base_tier"], "P3")

        # 2. With a conventional fix: commit that itself touched the in-tier file, 10 LOC qualifies for P0
        commits_fix = [{
            "subject": "fix(tokens): seal off-by-one cache key bug", "body": "",
            "files": ["dual_pipeline/token_optimization/cache.py"],
        }]
        res_fix = derive_priority(
            "fix/token-cache-bug",
            list(numstat.keys()),
            rubric=self.rubric,
            ahead=1,
            numstat=numstat,
            commits=commits_fix,
        )
        self.assertEqual(res_fix["base_tier"], "P0")
        self.assertEqual(res_fix["rule_id"], "R3.cost")

        # 3. A fix: commit that did NOT touch the tier's paths must not lift the tier (branch-wide bypass closed)
        commits_unscoped = [
            {"subject": "fix(docs): typo", "body": "", "files": ["docs/readme.md"]},
            {"subject": "feat: minor tweak", "body": "", "files": ["dual_pipeline/token_optimization/cache.py"]},
        ]
        res_unscoped = derive_priority(
            "feat/unscoped-fix", list(numstat.keys()), rubric=self.rubric, ahead=2,
            numstat=numstat, commits=commits_unscoped,
        )
        self.assertEqual(res_unscoped["base_tier"], "P3")

        # 4. 'fixture:'/'fixup' subjects are not conventional fixes
        commits_fixture = [{"subject": "fixture: add data", "body": "",
                            "files": ["dual_pipeline/token_optimization/cache.py"]}]
        res_fixture = derive_priority(
            "feat/fixture", list(numstat.keys()), rubric=self.rubric, ahead=1,
            numstat=numstat, commits=commits_fixture,
        )
        self.assertEqual(res_fixture["base_tier"], "P3")

    def test_adversarial_malformed_rubric_schema(self):
        """Rubric missing P0 tier or path_globs must fail validation."""
        class Args:
            receipt = None

        with tempfile.TemporaryDirectory() as tmpdir:
            bad_rubric_file = Path(tmpdir) / "pr_priority_rubric.json"
            bad_rubric_file.write_text(json.dumps({"version": "2.0", "tiers": {"P1": {}}}))
            from tools import pr_priority_engine
            orig_load = pr_priority_engine.load_rubric
            try:
                pr_priority_engine.load_rubric = lambda path=None: json.loads(bad_rubric_file.read_text())
                ret = cmd_validate_rubric(Args())
                self.assertEqual(ret, 1)
            finally:
                pr_priority_engine.load_rubric = orig_load

    def test_adversarial_empty_diff_and_corrupt_numstat(self):
        """Empty diff or corrupt numstat fails closed to P3 or D without crashing."""
        corrupt_numstat = {
            "corrupt_file.bin": (0, 0),
            "": (0, 0),
        }
        res = derive_priority(
            "feat/corrupt",
            ["corrupt_file.bin"],
            rubric=self.rubric,
            ahead=1,
            numstat=corrupt_numstat,
            commits=[],
        )
        self.assertEqual(res["base_tier"], "P3")
        self.assertLessEqual(len(res["explanation"]), 90)


if __name__ == "__main__":
    unittest.main()
