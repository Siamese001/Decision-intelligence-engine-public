"""Wave 6 Adversarial Tests: Import Cycles, Syntax-Broken Files, Empty Scopes & --skip-readiness Block."""
from __future__ import annotations

import argparse
from pathlib import Path

from tools.ast_dependency_cone import (
    compute_reverse_dependency_cone,
    resolve_impacted_tests,
)


def test_adversarial_circular_import_cycle_terminates_cleanly(tmp_path: Path) -> None:
    """Proves cyclic imports (A -> B -> C -> A) terminate in bounded time without infinite loop."""
    pkg = tmp_path / "dual_pipeline"
    tst = tmp_path / "tests"
    pkg.mkdir(parents=True)
    tst.mkdir(parents=True)

    (pkg / "cycle_a.py").write_text("import dual_pipeline.cycle_b\n", encoding="utf-8")
    (pkg / "cycle_b.py").write_text("from dual_pipeline import cycle_c\n", encoding="utf-8")
    (pkg / "cycle_c.py").write_text("from dual_pipeline.cycle_a import *\n", encoding="utf-8")
    (tst / "test_cycle.py").write_text("import dual_pipeline.cycle_c\n", encoding="utf-8")

    cone = compute_reverse_dependency_cone(["dual_pipeline/cycle_a.py"], repo_root=tmp_path)
    assert cone == {
        "dual_pipeline/cycle_a.py",
        "dual_pipeline/cycle_b.py",
        "dual_pipeline/cycle_c.py",
        "tests/test_cycle.py",
    }
    tests = resolve_impacted_tests(["dual_pipeline/cycle_a.py"], repo_root=tmp_path)
    assert tests == ["tests.test_cycle"]


def test_adversarial_syntax_broken_module_regex_fallback(tmp_path: Path) -> None:
    """Proves syntax-invalid Python files still resolve imports via regex fallback without crashing."""
    pkg = tmp_path / "dual_pipeline"
    tst = tmp_path / "tests"
    pkg.mkdir(parents=True)
    tst.mkdir(parents=True)

    (pkg / "target_mod.py").write_text("X = 1\n", encoding="utf-8")
    (tst / "test_broken_caller.py").write_text(
        "from dual_pipeline import target_mod\ndef test_broken(:\n",
        encoding="utf-8",
    )
    tests = resolve_impacted_tests(["dual_pipeline/target_mod.py"], repo_root=tmp_path)
    assert "tests.test_broken_caller" in tests


def test_adversarial_boundary_empty_and_nonexistent_paths(tmp_path: Path) -> None:
    """Handles empty changed list and nonexistent paths gracefully."""
    assert compute_reverse_dependency_cone([], repo_root=tmp_path) == set()
    assert resolve_impacted_tests(["dual_pipeline/does_not_exist.py"], repo_root=tmp_path) == []


def test_adversarial_sync_rejects_skip_readiness_bypass(tmp_path: Path, monkeypatch) -> None:
    """Proves cmd_sync fails closed with exit code 1 if --skip-readiness is passed."""
    from tools.pr_governance_core import sync as sync_mod

    monkeypatch.setattr(sync_mod, "get_feature_branches", lambda repo_root=None: ["feat/test-sync"])
    monkeypatch.setattr(sync_mod, "_resolve_worktree", lambda branch, repo_root: tmp_path)
    monkeypatch.setattr(sync_mod, "compute_first_parent_sync", lambda base, branch, cwd=None: (0, 1))

    args = argparse.Namespace(
        branch="feat/test-sync",
        repo_root=tmp_path,
        skip_readiness=True,
        lock_rebase=False,
    )
    rc = sync_mod.cmd_sync(args)
    assert rc == 1
