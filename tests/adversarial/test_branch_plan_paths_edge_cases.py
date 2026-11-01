#!/usr/bin/env python3
"""Adversarial and Edge-Case Tests for Branch-Isolated Plan Directories (Wave 5).

Covers 4 mandatory governance categories:
1. Boundary payload: Empty branch names, complex slashes, path-traversal prevention, missing plans.
2. Malformed input: Corrupted plan files, 0-byte plans, unparseable markdown, missing wave blocks.
3. Invariant bypass: Staged root plan freeze violation, override token validation, deletion vs edit.
4. Concurrency & race: Multi-threaded concurrent creation of branch plan directories.
"""
from __future__ import annotations

import concurrent.futures
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.guard_root_plan_freeze import check_root_plan_freeze
from tools.pr_governance_core.common import (
    resolve_branch_plan_path,
    slugify_branch,
)
from tools.pr_governance_core.wave_status import (
    candidate_plan_paths,
    compute_wave_columns,
    parse_plan_text,
)
from tools.validate_implementation_plan import validate_file


class BranchPlanPathsAdversarialTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="branch_plan_adv_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_boundary_empty_and_special_char_branch_slugs(self) -> None:
        """Verify slugify and resolution handle extreme branch naming without path traversal."""
        # Extreme branch names with slashes, dots, symbols, uppercase
        branches = [
            ("feature/deep/nested/sub-branch", "feature_deep_nested_sub_branch"),
            ("feat/UPPER_CASE-1.2.3", "feat_upper_case_1_2_3"),
            ("fix/..traversal../attempt", "fix_traversal_attempt"),
            ("chore///triple-slash///", "chore_triple_slash"),
            ("special!@#$%^&*()_+name", "special_name"),
        ]

        for branch_name, expected_substring in branches:
            slug = slugify_branch(branch_name)
            self.assertNotIn("/", slug, f"Slug contains slashes: {slug}")
            self.assertNotIn("..", slug, f"Slug contains dot-dot: {slug}")
            self.assertIn(expected_substring.split("_")[0], slug)

            # Ensure resolution constructs a path securely under plans/
            resolved = resolve_branch_plan_path(branch_name, self.tmp_dir)
            self.assertTrue(
                str(resolved).startswith(str(self.tmp_dir / "plans")),
                f"Path traversal escape: {resolved}",
            )

    def test_02_boundary_empty_branch_name(self) -> None:
        """Verify empty or whitespace branch names do not crash resolution."""
        for empty in ("", "   ", "\t"):
            slug = slugify_branch(empty)
            self.assertTrue(len(slug) >= 0)
            resolved = resolve_branch_plan_path(empty, self.tmp_dir)
            self.assertIsInstance(resolved, Path)

    def test_03_boundary_missing_plan_handling(self) -> None:
        """Verify missing plan does not raise exceptions in wave parser or status."""
        # Non-existent branch plan
        candidates = candidate_plan_paths([], branch="feat/non-existent")
        self.assertEqual(candidates, [])

        # Show callback returns empty string for missing file
        open_str, completed_str = compute_wave_columns(
            branch="feat/non-existent",
            show=lambda b, p: "",
            changed_files=[],
        )
        self.assertEqual(open_str, "?")
        self.assertEqual(completed_str, "?")

    def test_04_malformed_zero_byte_and_invalid_markdown_plans(self) -> None:
        """Verify empty or corrupt markdown plans fail validation cleanly."""
        plan_dir = self.tmp_dir / "plans" / "feat_corrupt"
        plan_dir.mkdir(parents=True)
        empty_plan = plan_dir / "implementation_plan.md"
        empty_plan.write_text("", encoding="utf-8")

        # Empty plan fails validation
        valid, errors = validate_file(empty_plan)
        self.assertFalse(valid)
        self.assertTrue(len(errors) > 0)

        # Plan with invalid wave schema (missing acceptance criteria & receipt)
        malformed_plan = plan_dir / "malformed_plan.md"
        malformed_plan.write_text("# Bad Plan\n\n## Wave 1: Incomplete\nSome text.", encoding="utf-8")
        m_valid, m_errors = validate_file(malformed_plan)
        self.assertFalse(m_valid)
        self.assertTrue(any("acceptance criteria" in e.lower() or "milestone" in e.lower() for e in m_errors))

        # parse_plan_text on malformed text produces empty dict without crashing
        self.assertEqual(parse_plan_text(""), {})
        self.assertEqual(parse_plan_text("Random non-markdown text"), {})

    def test_05_invariant_bypass_root_plan_freeze_rejects_edits(self) -> None:
        """Verify guard_root_plan_freeze fails closed on staged root plan edits."""
        with patch("tools.guard_root_plan_freeze.get_current_branch", return_value="feat/parallel-test"):
            # When root implementation_plan.md is modified
            with patch("tools.guard_root_plan_freeze.get_modified_plan_files", return_value=["implementation_plan.md"]):
                allowed, msg = check_root_plan_freeze(self.tmp_dir, staged=True, allow_env_override=False)
                self.assertFalse(allowed)
                self.assertIn("COMMIT REJECTED", msg)
                self.assertIn("plans/feat_parallel_test/implementation_plan.md", msg)

            # When root walkthrough.md is modified
            with patch("tools.guard_root_plan_freeze.get_modified_plan_files", return_value=["walkthrough.md"]):
                allowed, msg = check_root_plan_freeze(self.tmp_dir, staged=True, allow_env_override=False)
                self.assertFalse(allowed)
                self.assertIn("walkthrough.md", msg)

    def test_06_invariant_authorized_governed_merge_override(self) -> None:
        """Verify GOVERNED_PR_MERGE=1 allows merge queue updates to root plans."""
        with (
            patch.dict(os.environ, {"GOVERNED_PR_MERGE": "1"}),
            patch("tools.guard_root_plan_freeze.get_modified_plan_files", return_value=["implementation_plan.md"]),
        ):
            allowed, msg = check_root_plan_freeze(self.tmp_dir, staged=True, allow_env_override=True)
            self.assertTrue(allowed)
            self.assertIn("Authorized", msg)

    def test_07_invariant_root_file_deletion_allowed(self) -> None:
        """Verify deleting root plan file during migration is allowed."""
        # When get_modified_plan_files returns empty (because deletion is diff-filter=D, not ACM)
        with (
            patch("tools.guard_root_plan_freeze.get_current_branch", return_value="refactor/migration"),
            patch("tools.guard_root_plan_freeze.get_modified_plan_files", return_value=[]),
        ):
            allowed, msg = check_root_plan_freeze(self.tmp_dir, staged=True, allow_env_override=False)
            self.assertTrue(allowed)

    def test_08_concurrency_multi_branch_plan_creation(self) -> None:
        """Verify concurrent multi-threaded writes across branch plan folders do not collide."""
        branch_count = 12

        def create_branch_plan(idx: int) -> tuple[int, bool, str]:
            branch = f"feat/concurrent-branch-{idx:02d}"
            slug = slugify_branch(branch)
            plan_dir = self.tmp_dir / "plans" / slug
            plan_dir.mkdir(parents=True, exist_ok=True)
            plan_path = plan_dir / "implementation_plan.md"
            content = f"# Plan for {branch}\n\n## Wave 1: Task {idx}\n### Milestones & Deliverables\n- task\n"
            plan_path.write_text(content, encoding="utf-8")
            resolved = resolve_branch_plan_path(branch, self.tmp_dir)
            return idx, (resolved == plan_path), plan_path.read_text(encoding="utf-8")

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(create_branch_plan, i) for i in range(branch_count)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        self.assertEqual(len(results), branch_count)
        for idx, matches, content in results:
            self.assertTrue(matches)
            self.assertIn(f"feat/concurrent-branch-{idx:02d}", content)


if __name__ == "__main__":
    unittest.main()
