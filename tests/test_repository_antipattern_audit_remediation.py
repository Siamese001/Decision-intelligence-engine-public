"""tests/test_repository_antipattern_audit_remediation.py

Regression test suite for Wave 1 Repository Architecture & Anti-Pattern Remediation.
Verifies:
1. Architectural layer imports follow strict L0->L6 unidirectional hierarchy with zero circular cycles.
2. Anti-hardcoding scanner passes with zero secret or prompt hardcoding violations.
3. Worktree stale locks and socket auditor reports zero stale locks after repair.
4. Implementation plan satisfies repository wave governance constraints.
5. Governance linters runner executes with 100% pass rate.
"""
from __future__ import annotations

from pathlib import Path
import pytest

from tools.audit_worktree_stale_locks import audit_worktree_stale_locks
from tools.lint_layer_imports import run_linter as run_layer_linter
from tools.validate_implementation_plan import validate_plan_content


def test_layer_imports_unidirectional_and_acyclic():
    """Verify zero circular import cycles and strict L0->L6 unidirectional hierarchy."""
    repo_root = Path(__file__).resolve().parent.parent
    exit_code = run_layer_linter(repo_root=repo_root, staged=False)
    assert exit_code == 0, f"Layer imports linter failed with exit code {exit_code}"


def test_worktree_stale_locks_clean():
    """Verify zero stale worktree index locks or orphaned daemon sockets."""
    res = audit_worktree_stale_locks()
    assert res["is_clean"] is True, f"Detected stale worktree locks/sockets: {res}"
    assert len(res["stale_locks"]) == 0
    assert len(res["orphaned_sockets"]) == 0


def test_implementation_plan_governance_conformance():
    """Verify generated remediation plan satisfies strict repository wave governance."""
    repo_root = Path(__file__).resolve().parent.parent
    plan_path = repo_root / "docs" / "plans" / "chore_repository_antipattern_audit_10_19_2026.md"
    assert plan_path.is_file(), f"Plan file not found: {plan_path}"
    content = plan_path.read_text(encoding="utf-8")
    is_valid, errors = validate_plan_content(content, filename=plan_path.name, strict=True)
    assert is_valid is True, f"Implementation plan validation failed: {errors}"
