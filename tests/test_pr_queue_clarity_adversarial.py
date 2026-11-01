"""
tests/test_pr_queue_clarity_adversarial.py

Adversarial/edge cases for queue ordering and next-action text:
malformed input, unknown tiers, boundary payloads, invariant-bypass, production regression pin.
"""

from __future__ import annotations

import unittest
from unittest import mock

from tools.pr_governance_core.executive_render import format_executive_disposition
from tools.pr_governance_core.observer_actions import observer_next_action
from tools.pr_governance_core.queue_actions import (
    explain_next_action,
    get_conflict_files,
    is_doc_only,
    sibling_stem,
)
from tools.pr_governance_core.queue_order import assign_sub_ranks, queue_sort_key, sort_queue


def _item(branch, behind=0, open_waves="—", dirty=False):
    return {"branch": branch, "behind": behind, "open_waves": open_waves, "dirty": dirty}


class TestPrQueueClarityAdversarial(unittest.TestCase):
    def test_regression_pin_p3_never_above_p0(self):
        """2026-10-03 prod defect: P3.1 rendered above P0.1 because behind>=20 was sorted globally."""
        rows = [
            {"branch": "a", "tier": "P3.1", "risk_tier": 0, "risk_score": 0},
            {"branch": "b", "tier": "P0.1", "risk_tier": 4, "risk_score": 80},
        ]
        sort_queue(rows)
        self.assertEqual(rows[0]["branch"], "b")

    def test_decommission_always_last_even_if_lowest_risk(self):
        rows = [
            {"branch": "d", "tier": "D.1", "risk_tier": 0, "risk_score": 0},
            {"branch": "p3", "tier": "P3.1", "risk_tier": 4, "risk_score": 99},
        ]
        sort_queue(rows)
        self.assertEqual([r["branch"] for r in rows], ["p3", "d"])

    def test_malformed_and_unknown_tiers_do_not_crash(self):
        rows = [
            {"branch": "x", "tier": None},
            {"branch": "y", "tier": "ZZ9.1", "risk_tier": "2"},
            {"branch": "z"},
        ]
        assign_sub_ranks(sort_queue(rows))
        self.assertEqual(len({r["branch"] for r in rows}), 3)
        self.assertTrue(all("sub_rank" in r for r in rows))

    def test_sort_is_deterministic_for_ties(self):
        rows = [{"branch": n, "tier": "P2.1", "risk_tier": 1, "risk_score": 10} for n in ("c", "a", "b")]
        sort_queue(rows)
        self.assertEqual([r["branch"] for r in rows], ["a", "b", "c"])
        self.assertEqual(queue_sort_key(rows[0])[0], 0)

    def test_conflict_file_parser_boundary_payloads(self):
        def fake(stdout, rc):
            return mock.Mock(returncode=rc, stdout=stdout)

        with mock.patch("subprocess.run", return_value=fake("", 1)):
            self.assertEqual(get_conflict_files("b", "."), [])  # empty output
        with mock.patch("subprocess.run", return_value=fake("deadbeef\n", 0)):
            self.assertEqual(get_conflict_files("b", "."), [])  # clean merge
        out = "deadbeef\na.py\nb.py\n\nAuto-merging a.py\nCONFLICT (content): x\n"
        with mock.patch("subprocess.run", return_value=fake(out, 1)):
            self.assertEqual(get_conflict_files("b", "."), ["a.py", "b.py"])  # messages not parsed as files
        with mock.patch("subprocess.run", side_effect=OSError("git missing")):
            self.assertEqual(get_conflict_files("b", "."), [])  # git unavailable

    def test_many_conflicts_are_truncated(self):
        text = explain_next_action(_item("b"), [f"f{i}.py" for i in range(40)], [], {"b": ["f0.py"]})
        self.assertIn("40 conflicting", text)
        self.assertIn("(+37 more)", text)
        self.assertLess(len(text), 400)

    def test_doc_only_does_not_match_code_plus_plan(self):
        """Invariant bypass: a branch touching code AND the plan must not be treated as doc-only."""
        self.assertFalse(is_doc_only(["implementation_plan.md", "tools/x.py"]))
        self.assertFalse(is_doc_only([]))
        files = {"f/x": ["implementation_plan.md", "a.py"], "f/x-v2": ["implementation_plan.md"]}
        text = explain_next_action(_item("f/x-v2"), ["implementation_plan.md"], ["f/x"], files)
        self.assertNotIn("CONFIRM_TOMBSTONE", text)

    def test_stem_and_version_edge_cases(self):
        self.assertEqual(sibling_stem("feat/x-v12"), "feat/x")
        self.assertEqual(sibling_stem("feat/x-v2-hotfix"), "feat/x-v2-hotfix")
        self.assertEqual(sibling_stem("feat/x"), "feat/x")

    def test_never_returns_generic_text_and_covers_every_state(self):
        files = {"b": ["a.py"]}
        cases = {
            "[REBASE]": explain_next_action(_item("b", behind=30), [], [], files),
            "[CONTINUE_WAVES]": explain_next_action(_item("b", open_waves="3"), [], [], files),
            "[COMMIT_FIRST]": explain_next_action(_item("b", dirty=True), [], [], files),
            "[REVIEW]": explain_next_action(_item("b"), [], [], files),
            "[ARCHIVE]": explain_next_action(_item("backup/p03"), [], [], {"backup/p03": ["a.py"]}),
        }
        for prefix, text in cases.items():
            self.assertTrue(text.startswith(prefix), (prefix, text))
            self.assertNotIn("Active branch with unique work", text)

    def test_overlap_next_action_strict_precedence(self):
        """
        Wave 6 / Wave 9 Invariant: Strict Precedence
        [COMMIT_FIRST] > [RESOLVE_CONFLICTS] > [DEDUP] > [REORDER] > [CHERRY_PICK] > [REBASE] > [REVIEW]
        """
        files = {"b": ["a.py"]}
        full_overlap = {
            "duplicate_of": "feat/sibling",
            "duplicate_sha": "abc12345",
            "reorder_after": "feat/prereq",
            "cherry_pick_extract": {"clean_count": 2, "total_count": 5},
            "rebase_onto": "feat/upstream",
        }

        # 1. [COMMIT_FIRST] outranks all conflicts and overlap findings
        text = explain_next_action(
            _item("b", dirty=True),
            conflict_files=["a.py"],
            siblings=[],
            all_files=files,
            overlap_context=full_overlap,
        )
        self.assertTrue(text.startswith("[COMMIT_FIRST]"), text)

        # 2. [RESOLVE_CONFLICTS] outranks all overlap findings when clean
        text = explain_next_action(
            _item("b", dirty=False),
            conflict_files=["a.py"],
            siblings=[],
            all_files=files,
            overlap_context=full_overlap,
        )
        self.assertTrue(text.startswith("[RESOLVE_CONFLICTS]"), text)

        # 3. [DEDUP] outranks REORDER, CHERRY_PICK, REBASE when no conflicts
        text = explain_next_action(
            _item("b", dirty=False),
            conflict_files=[],
            siblings=[],
            all_files=files,
            overlap_context=full_overlap,
        )
        self.assertTrue(text.startswith("[DEDUP]"), text)

        # 4. [REORDER] outranks CHERRY_PICK, REBASE when no dedup
        overlap_no_dup = dict(full_overlap)
        del overlap_no_dup["duplicate_of"]
        text = explain_next_action(
            _item("b", dirty=False),
            conflict_files=[],
            siblings=[],
            all_files=files,
            overlap_context=overlap_no_dup,
        )
        self.assertTrue(text.startswith("[REORDER]"), text)

        # 5. [CHERRY_PICK] outranks REBASE when no reorder
        overlap_no_reorder = dict(overlap_no_dup)
        del overlap_no_reorder["reorder_after"]
        text = explain_next_action(
            _item("b", dirty=False),
            conflict_files=[],
            siblings=[],
            all_files=files,
            overlap_context=overlap_no_reorder,
        )
        self.assertTrue(text.startswith("[CHERRY_PICK]"), text)

        # 6. [REBASE] (stacked) when no cherry-pick
        overlap_stacked = {"rebase_onto": "feat/upstream"}
        text = explain_next_action(
            _item("b", dirty=False),
            conflict_files=[],
            siblings=[],
            all_files=files,
            overlap_context=overlap_stacked,
        )
        self.assertTrue(text.startswith("[REBASE]"), text)
        self.assertIn("stacked branch", text)

        # 7. [REVIEW] when clean, up to date, no overlap actions
        text = explain_next_action(
            _item("b", dirty=False, behind=0),
            conflict_files=[],
            siblings=[],
            all_files=files,
            overlap_context={},
        )
        self.assertTrue(text.startswith("[REVIEW]"), text)

    def test_adversarial_zero_overlap_boundary(self):
        # behind = 19 with 0 overlap -> Merge + PR
        item_19 = {"branch": "feat/clean-lag", "ahead": 2, "behind": 19, "clean_merge": True, "dirty": False, "overlap_files": [], "file_numstat": {"a.py": (10, 0)}}
        disp_19, act_19 = format_executive_disposition(item_19)
        self.assertEqual(disp_19, "Merge + PR")
        self.assertIn("0 shared files modified on main", act_19)
        self.assertIn("(+10/-0)", act_19)

        # behind = 20 with 0 overlap -> Rebase & PR to Main (severely diverged)
        item_20 = {"branch": "feat/clean-lag", "ahead": 2, "behind": 20, "clean_merge": True, "dirty": False, "overlap_files": []}
        disp_20, act_20 = format_executive_disposition(item_20)
        self.assertEqual(disp_20, "Rebase & PR to Main")
        self.assertIn("severely diverged", act_20)

    def test_adversarial_conflicted_files_exact_citations(self):
        item = {
            "branch": "feat/conflicted",
            "clean_merge": False,
            "rebase_effort": "LOW",
            "conflicted_files": [{"path": "config/registry.json", "conflict_class": "REGISTRY_APPEND"}],
        }
        disp, act = format_executive_disposition(item)
        self.assertEqual(disp, "Rebase, Resolve Conflicts & PR")
        self.assertIn("config/registry.json [REGISTRY_APPEND]", act)

    def test_adversarial_partial_vs_full_absorption(self):
        # full absorption
        item_full = {
            "branch": "feat/full-abs",
            "clean_merge": True,
            "dirty": False,
            "absorbed_files": ["foo.py", "bar.py"],
            "salvageable_files": [],
        }
        disp_full, act_full = format_executive_disposition(item_full)
        self.assertEqual(disp_full, "Prune / Decommission")
        self.assertIn("2 file(s) fully absorbed into main (foo.py, bar.py)", act_full)

        # partial absorption
        item_part = {
            "branch": "feat/part-abs",
            "clean_merge": True,
            "dirty": False,
            "absorbed_files": ["foo.py"],
            "salvageable_files": ["unique.py"],
            "file_numstat": {"unique.py": (5, 1)},
        }
        disp_part, act_part = format_executive_disposition(item_part)
        self.assertEqual(disp_part, "Extract & Archive")
        self.assertIn("Extract 1 unique file(s) (unique.py)", act_part)
        self.assertIn("1 file(s) already absorbed into main (foo.py)", act_part)
        self.assertIn("(+5/-1)", act_part)

    def test_adversarial_numstat_metric_injection(self):
        item = {
            "branch": "feat/metrics",
            "ahead": 3,
            "behind": 2,
            "clean_merge": True,
            "dirty": False,
            "overlap_files": ["core.py"],
            "file_numstat": {"core.py": (150, 20), "extra.py": (30, 0)},
        }
        disp, act = format_executive_disposition(item)
        self.assertEqual(disp, "Rebase & PR to Main")
        self.assertIn("core.py", act)
        self.assertIn("(+180/-20)", act)


if __name__ == "__main__":
    unittest.main()
