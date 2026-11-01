"""
Adversarial / edge cases: PR queue ordering hardening (PRQ committee RCA).

Covers: glob boundary semantics, classification precedence & unclassified default, conventional-fix
spoofing, scoped hotfix bypass attempts, fail-closed facts, registry pin validation, review
reconciliation invariants, risk ladder, numstat parsing of hostile filenames, cache-key invalidation,
and production regression pins for the rows the committee flagged.
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import pr_priority_facts as facts
from tools.pr_governance_core.common import logic_digest
from tools.pr_governance_core.queue_order import assign_sub_ranks, sort_queue
from tools.pr_governance_core.queue_reconcile import (
    compute_risk_tier,
    reconcile_review,
    tier_from_disposition,
)
from tools.pr_priority_engine import derive_priority, load_rubric

RUBRIC = load_rubric()
RULES = RUBRIC["classification_rules"]


class GlobAndClassificationAdversarial(unittest.TestCase):
    def test_star_never_crosses_directory(self) -> None:
        self.assertFalse(facts.glob_matches("dual_pipeline/cache_dir/x.py", "**/*cache*.py"))
        self.assertTrue(facts.glob_matches("dual_pipeline/semantic_cache.py", "**/*cache*.py"))

    def test_double_star_matches_zero_directories(self) -> None:
        self.assertTrue(facts.glob_matches("cost_tracker.py", "**/cost_tracker.py"))
        self.assertTrue(facts.glob_matches("a/b/c/cost_tracker.py", "**/cost_tracker.py"))

    def test_slash_patterns_are_root_anchored(self) -> None:
        self.assertFalse(facts.glob_matches("vendor/storage/x.py", "storage/**"))

    def test_specific_rule_beats_generic(self) -> None:
        self.assertEqual(facts.classify_path("config/runtime/model_registry.json", RULES), "prod")
        self.assertEqual(facts.classify_path("config/pr_priority_rubric.json", RULES), "govcfg")

    def test_unmatched_paths_are_unclassified_not_prod(self) -> None:
        self.assertEqual(facts.classify_path("weird_dir/blob.bin", RULES), facts.UNCLASSIFIED)
        self.assertEqual(facts.classify_path("reports/graph_consolidation/plan.json", RULES), "artifact")

    def test_root_entrypoints_and_new_code_dirs_are_prod(self) -> None:
        for p in ("nee.py", "app.py", "pipeline_c_retrieval/r.py", "core/audio/q.py", "ingestion/i.py"):
            self.assertEqual(facts.classify_path(p, RULES), "prod", p)

    def test_llm_judge_core_is_not_a_cost_path(self) -> None:
        self.assertFalse(facts.match_path_globs("dual_pipeline/llm_judge_core/judge_clarification.py", RUBRIC["tiers"]["P0"]["path_globs"]))


class HotfixSpoofingAdversarial(unittest.TestCase):
    def test_conventional_fix_regex_rejects_lookalikes(self) -> None:
        for s in ("fixture: add", "fixup! feat: x", "fixes #12", "prefix: fix"):
            self.assertFalse(facts.is_conventional_fix(s), s)
        for s in ("fix: x", "fix(scope): x", "fix!: breaking", "Fix(qa): x"):
            self.assertTrue(facts.is_conventional_fix(s), s)

    def test_fix_commit_must_touch_tier_files(self) -> None:
        commits = [{"subject": "fix(qa): apostrophes", "files": ["dual_pipeline/transcript_qa_core/g.py"]}]
        self.assertFalse(facts.has_scoped_fix(commits, ["dual_pipeline/token_optimization/b.py"]))
        commits.append({"subject": "fix(cost): cap", "files": ["dual_pipeline/token_optimization/b.py"]})
        self.assertTrue(facts.has_scoped_fix(commits, ["dual_pipeline/token_optimization/b.py"]))

    def test_branch_wide_fix_cannot_promote_unrelated_tier(self) -> None:
        numstat = {"dual_pipeline/token_optimization/b.py": (3, 1), "dual_pipeline/x/y.py": (400, 0)}
        commits = [{"subject": "fix(x): y", "body": "", "files": ["dual_pipeline/x/y.py"]}]
        meta = derive_priority("feat/spoof", list(numstat), {}, RUBRIC, ahead=2, behind=0, clean_merge=True,
                               numstat=numstat, commits=commits, repo_root=Path("/nonexistent"))
        self.assertNotEqual(meta["base_tier"], "P0", meta)


class FailClosedAdversarial(unittest.TestCase):
    def test_missing_facts_without_git_fail_closed(self) -> None:
        meta = derive_priority("feat/x", ["dual_pipeline/llm_judge_core/a.py"], {}, RUBRIC, ahead=3, behind=0,
                               clean_merge=True, repo_root=Path("/nonexistent"))
        self.assertEqual(meta["rule_id"], "R0.facts_unavailable")

    def test_synthetic_placeholder_requires_explicit_flag(self) -> None:
        meta = derive_priority("feat/x", ["tools/a.py"], {}, RUBRIC, ahead=3, behind=0, clean_merge=True,
                               repo_root=Path("/nonexistent"), allow_synthetic=True)
        self.assertNotEqual(meta["rule_id"], "R0.facts_unavailable")

    def test_git_failure_ahead_negative_fails_closed(self) -> None:
        meta = derive_priority("feat/x", [], {}, RUBRIC, ahead=-1, behind=-1, clean_merge=False, numstat={}, commits=[])
        self.assertEqual(meta["rule_id"], "R0.git_error")

    def test_dirty_ahead_zero_keeps_domain_tier(self) -> None:
        numstat = {"dual_pipeline/token_optimization/b.py": (80, 0)}
        meta = derive_priority("feat/wip", [], {}, RUBRIC, ahead=0, behind=3, clean_merge=True,
                               worktree={"dirty": True}, numstat=numstat)
        self.assertEqual(meta["base_tier"], "P0")
        self.assertIn("WIP", meta["explanation"])


class RegistryPinAdversarial(unittest.TestCase):
    def test_invalid_pin_is_reported_not_silent(self) -> None:
        pins = {"feat/a": {"tier": "P0", "reason": "x", "owner": "", "expires_at": ""}}
        meta = derive_priority("feat/a", [], pins, RUBRIC, ahead=1, behind=0, clean_merge=True,
                               numstat={"tools/a.py": (5, 0)}, commits=[])
        self.assertFalse(meta["override_active"])
        self.assertIn("ignored", meta["override_warning"])

    def test_invalid_tombstone_pin_is_not_honoured(self) -> None:
        pins = {"feat/a": {"tier": "TOMBSTONE", "reason": "x"}}
        meta = derive_priority("feat/a", [], pins, RUBRIC, ahead=1, behind=0, clean_merge=True,
                               numstat={"tools/a.py": (5, 0)}, commits=[])
        self.assertNotEqual(meta["base_tier"], "TOMBSTONE")

    def test_valid_tombstone_pin_is_honoured(self) -> None:
        pins = {"feat/a": {"tier": "TOMBSTONE", "reason": "x", "owner": "amit", "expires_at": "2999-01-01"}}
        meta = derive_priority("feat/a", [], pins, RUBRIC, ahead=1, behind=0, clean_merge=True,
                               numstat={"tools/a.py": (5, 0)}, commits=[])
        self.assertEqual(meta["base_tier"], "TOMBSTONE")


class ReconciliationInvariants(unittest.TestCase):
    def test_review_matrix(self) -> None:
        self.assertEqual(reconcile_review("MERGE_READY", 0, False, {}).verdict, "—")
        self.assertEqual(reconcile_review("MERGE_READY", 3, True, {}).verdict, "REVIEW_READY")
        self.assertEqual(reconcile_review("SUPERSEDED_TOMBSTONE", 1, False, {"commits_in_main": 0}).verdict, "REVIEW_READY")
        proven = reconcile_review("SUPERSEDED_TOMBSTONE", 4, False, {"commits_in_main": 2, "unique_production_commits": 0})
        self.assertTrue(proven.tombstone_evidence)

    def test_tier_follows_disposition(self) -> None:
        self.assertEqual(tier_from_disposition("P2.1", "ARCHIVE"), "A.1")
        self.assertEqual(tier_from_disposition("P0.1", "PRUNE"), "D.1")
        self.assertEqual(tier_from_disposition("D.1", "KEEP"), "P3.1")
        self.assertEqual(tier_from_disposition("P1.1", "COMMIT_FIRST"), "P1.1")

    def test_risk_ladder(self) -> None:
        info = {"behind": 0, "clean_merge": True}
        self.assertEqual(compute_risk_tier("P1.1", info, False, "—", "MERGE_READY"), 0)
        self.assertEqual(compute_risk_tier("P1.1", info, False, "?", "MERGE_READY"), 1)
        self.assertEqual(compute_risk_tier("P1.1", info, False, "—", "GOVERNANCE_BLOCK"), 3)
        self.assertEqual(compute_risk_tier("P1.1", {"behind": 491, "clean_merge": False}, False, "—", "MERGE_READY"), 4)
        self.assertEqual(compute_risk_tier("A.1", info, False, "—", "MERGE_READY"), 5)

    def test_governance_block_sorts_below_ready_rows_in_same_level(self) -> None:
        rows = [
            {"branch": "blocked", "tier": "P1.1", "value_score": 99.0, "risk_tier": compute_risk_tier("P1.1", {"behind": 0, "clean_merge": True}, False, "—", "GOVERNANCE_BLOCK")},
            {"branch": "ready", "tier": "P1.1", "value_score": 1.0, "risk_tier": compute_risk_tier("P1.1", {"behind": 0, "clean_merge": True}, False, "—", "MERGE_READY")},
        ]
        assign_sub_ranks(sort_queue(rows))
        self.assertEqual([r["branch"] for r in rows], ["ready", "blocked"])


class ParsingAndCacheEdgeCases(unittest.TestCase):
    def test_numstat_z_handles_binary_and_tabs(self) -> None:
        raw = "3\t1\ta/b.py\0-\t-\timg.png\0" + "2\t0\tweird\tname.py\0"
        stats = facts.parse_numstat_z(raw)
        self.assertEqual(stats["img.png"], (0, 0))
        self.assertEqual(stats["weird\tname.py"], (2, 0))

    def test_dirty_numstat_counts_untracked(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"], ["config", "core.fsmonitor", "false"]):
                subprocess.run(["git", *args], cwd=root, check=True)
            (root / "a.py").write_text("1\n")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)
            (root / "new.py").write_text("a\nb\nc\n")
            self.assertEqual(facts.fetch_worktree_dirty_numstat(str(root))["new.py"], (3, 0))

    def test_cache_key_digest_changes_with_rules(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "rules.py"
            f.write_text("A = 1\n")
            first = logic_digest(str(f))
            logic_digest.cache_clear()
            f.write_text("A = 2\n")
            self.assertNotEqual(first, logic_digest(str(f)))


if __name__ == "__main__":
    unittest.main()
