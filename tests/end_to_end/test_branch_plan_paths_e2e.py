#!/usr/bin/env python3
"""End-to-End test for Branch-Isolated Plan Directories (Wave 5).

Exercises:
1. Dynamic plan file identification via is_plan_doc_file().
2. Priority plan resolution (branch-isolated first, root fallback, legacy fallback).
3. Zero-collision isolation between multiple concurrent branch plans.
4. Wave status and wave column computation from branch-isolated plans.
5. PR governance conflict triage and doc-only classification for branch plan paths.
6. Implementation plan governance validator integration on branch-isolated plans.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from tools.pr_governance_core.common import (
    is_plan_doc_file,
    resolve_branch_plan_path,
    slugify_branch,
)
from tools.pr_governance_core.conflict_triage import is_plan_doc
from tools.pr_governance_core.queue_actions import is_doc_only
from tools.pr_governance_core.wave_status import candidate_plan_paths, compute_wave_columns
from tools.validate_implementation_plan import validate_file

SAMPLE_VALID_PLAN = """# Feature Implementation Plan

## Wave 1: Core Engine Hardening
### Objective & Scope
Hardens the core storage and query engine.

### Milestones & Deliverables
- [NEW] storage/core.py: Implements lock-free read buffer.

### Acceptance Criteria
- Storage read latency under 5ms.

### Runtime Receipt & Completion Gate
Execute `pytest tests/test_core.py` producing receipt.

## Wave 2: Public Interface
### Objective & Scope
Exposes public API.

### Milestones & Deliverables
- [NEW] api/routes.py: Implements endpoints.

### Acceptance Criteria
- Status 200 on all health checks.

### Runtime Receipt & Completion Gate
Execute `pytest tests/test_api.py`.
"""


class BranchPlanPathsE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="branch_plan_e2e_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_01_is_plan_doc_file_identification(self) -> None:
        """Verify is_plan_doc_file correctly identifies branch plans and ignores non-plan files."""
        # Branch-isolated plans
        self.assertTrue(is_plan_doc_file("plans/feat_search_opt/implementation_plan.md"))
        self.assertTrue(is_plan_doc_file("plans/feat_search_opt/walkthrough.md"))
        self.assertTrue(is_plan_doc_file("plans/fix_deadlock/custom_plan.md"))
        self.assertTrue(is_plan_doc_file(Path("plans/feat_core/implementation_plan.md")))

        # Legacy root and legacy directory plans
        self.assertTrue(is_plan_doc_file("implementation_plan.md"))
        self.assertTrue(is_plan_doc_file("walkthrough.md"))
        self.assertTrue(is_plan_doc_file("plans/_legacy/implementation_plan.md"))

        # Non-plan files must evaluate to False
        self.assertFalse(is_plan_doc_file("dual_pipeline/cli/engine.py"))
        self.assertFalse(is_plan_doc_file("README.md"))
        self.assertFalse(is_plan_doc_file("documentation/ARCHITECTURE.md"))
        self.assertFalse(is_plan_doc_file("tests/test_plan.py"))
        self.assertFalse(is_plan_doc_file("reports/waves/parallel_pr_w5_receipt.json"))

    def test_02_resolve_branch_plan_path_preference_and_fallback(self) -> None:
        """Verify plan resolution prefers branch-isolated folder, then root, then legacy."""
        branch = "feat/semantic-cache"
        slug = slugify_branch(branch)
        branch_dir = self.tmp_dir / "plans" / slug
        branch_dir.mkdir(parents=True)
        branch_plan = branch_dir / "implementation_plan.md"
        root_plan = self.tmp_dir / "implementation_plan.md"
        legacy_dir = self.tmp_dir / "plans" / "_legacy"
        legacy_dir.mkdir(parents=True)
        legacy_plan = legacy_dir / "implementation_plan.md"

        # 1. When branch plan exists, it must be selected first
        branch_plan.write_text(SAMPLE_VALID_PLAN, encoding="utf-8")
        root_plan.write_text("# Root Plan", encoding="utf-8")
        legacy_plan.write_text("# Legacy Plan", encoding="utf-8")

        resolved = resolve_branch_plan_path(branch, self.tmp_dir)
        self.assertEqual(resolved, branch_plan)

        # 2. When branch plan is removed, fallback to root plan
        branch_plan.unlink()
        resolved_root = resolve_branch_plan_path(branch, self.tmp_dir)
        self.assertEqual(resolved_root, root_plan)

        # 3. When root plan is also removed, fallback to legacy plan
        root_plan.unlink()
        resolved_legacy = resolve_branch_plan_path(branch, self.tmp_dir)
        self.assertEqual(resolved_legacy, legacy_plan)

        # 4. When all are removed, returns default canonical branch target path (which doesn't exist yet)
        legacy_plan.unlink()
        resolved_none = resolve_branch_plan_path(branch, self.tmp_dir)
        self.assertEqual(resolved_none, branch_plan)
        self.assertFalse(resolved_none.exists())

    def test_03_two_branches_with_plans_parallel_isolation(self) -> None:
        """Verify two branches have isolated plan directories with zero path collisions."""
        branch_a = "feat/search-optimization"
        branch_b = "fix/wal-deadlock-remediation"

        slug_a = slugify_branch(branch_a)
        slug_b = slugify_branch(branch_b)
        self.assertNotEqual(slug_a, slug_b)

        dir_a = self.tmp_dir / "plans" / slug_a
        dir_b = self.tmp_dir / "plans" / slug_b
        dir_a.mkdir(parents=True)
        dir_b.mkdir(parents=True)

        plan_a = dir_a / "implementation_plan.md"
        plan_b = dir_b / "implementation_plan.md"
        plan_a.write_text(SAMPLE_VALID_PLAN.replace("Wave 1", "Wave 1: Search A"), encoding="utf-8")
        plan_b.write_text(SAMPLE_VALID_PLAN.replace("Wave 1", "Wave 1: Deadlock B"), encoding="utf-8")

        # Resolve plans for both branches independently
        resolved_a = resolve_branch_plan_path(branch_a, self.tmp_dir)
        resolved_b = resolve_branch_plan_path(branch_b, self.tmp_dir)

        self.assertEqual(resolved_a, plan_a)
        self.assertEqual(resolved_b, plan_b)
        self.assertIn("Search A", resolved_a.read_text(encoding="utf-8"))
        self.assertIn("Deadlock B", resolved_b.read_text(encoding="utf-8"))

        # Candidate paths for each branch start with their own isolated plan
        rel_a = f"plans/{slug_a}/implementation_plan.md"
        rel_b = f"plans/{slug_b}/implementation_plan.md"
        candidates_a = candidate_plan_paths([rel_a, "code_a.py"], branch=branch_a)
        candidates_b = candidate_plan_paths([rel_b, "code_b.py"], branch=branch_b)

        self.assertIn(rel_a, candidates_a)
        self.assertIn(rel_b, candidates_b)
        self.assertNotIn(rel_b, candidates_a)
        self.assertNotIn(rel_a, candidates_b)

    def test_04_wave_status_and_column_computation_integration(self) -> None:
        """Verify wave status column computation parses waves from branch plan folder."""
        branch = "feat/analytics-pipeline"
        slug = slugify_branch(branch)
        plan_dir = self.tmp_dir / "plans" / slug
        plan_dir.mkdir(parents=True)
        plan_file = plan_dir / "implementation_plan.md"
        plan_file.write_text(SAMPLE_VALID_PLAN, encoding="utf-8")

        rel_path = f"plans/{slug}/implementation_plan.md"

        def show_func(b: str, path: str) -> str:
            target = self.tmp_dir / path
            if target.is_file():
                return target.read_text(encoding="utf-8")
            return ""

        # Compute wave columns using branch name and show callback
        open_str, completed_str = compute_wave_columns(
            branch=branch,
            show=show_func,
            changed_files=[rel_path],
        )
        self.assertIn("1", open_str)
        self.assertIn("2", open_str)
        self.assertEqual(completed_str, "—")

    def test_05_conflict_triage_is_plan_doc_integration(self) -> None:
        """Verify conflict triage and queue actions classify branch plans as plan documents."""
        plan_path = "plans/feat_indexer/implementation_plan.md"
        walkthrough_path = "plans/feat_indexer/walkthrough.md"

        self.assertTrue(is_plan_doc(plan_path))
        self.assertTrue(is_plan_doc(walkthrough_path))
        self.assertTrue(is_doc_only([plan_path, walkthrough_path]))
        self.assertFalse(is_doc_only([plan_path, "dual_pipeline/engine.py"]))

    def test_06_validate_implementation_plan_discovery(self) -> None:
        """Verify validate_file successfully validates a branch-isolated plan."""
        branch_dir = self.tmp_dir / "plans" / "feat_governance_rules"
        branch_dir.mkdir(parents=True)
        plan_path = branch_dir / "implementation_plan.md"
        plan_path.write_text(SAMPLE_VALID_PLAN, encoding="utf-8")

        valid, errors = validate_file(plan_path)
        self.assertTrue(valid, f"Validation failed with errors: {errors}")
        self.assertEqual(len(errors), 0)


if __name__ == "__main__":
    unittest.main()
