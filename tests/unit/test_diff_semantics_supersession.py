"""tests/unit/test_diff_semantics_supersession.py

Unit tests for Wave 1: Code Supersession Detection in Diff Intelligence.
Uses pure dependency-injected git_runner (zero third-party mocks/patching).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from tools.pr_governance_core.diff_semantics import (
    DiffSemantics,
    classify_branch_diff,
    detect_superseded_code,
)


def test_classify_branch_diff_empty() -> None:
    diff_sem = classify_branch_diff([])
    assert diff_sem.category == "EMPTY_STUB"
    assert diff_sem.is_docs_only is False
    assert diff_sem.is_superseded is False


def test_classify_branch_diff_docs_only_active() -> None:
    diff_sem = classify_branch_diff(["docs/guide.md", "README.md"])
    assert diff_sem.category == "DOCS_ONLY_ACTIVE"
    assert diff_sem.is_docs_only is True
    assert diff_sem.is_superseded is False


def test_classify_branch_diff_code_active() -> None:
    diff_sem = classify_branch_diff(
        ["dual_pipeline/cli/ingest.py", "tests/unit/test_foo.py"],
        clean_merge=True,
        behind=0,
    )
    assert diff_sem.category == "CODE_ACTIVE"
    assert diff_sem.is_superseded is False


def test_detect_superseded_code_cherry_picked() -> None:
    def fake_git_runner(cmd: list[str], root: Path) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="- sha1\n- sha2\n",
            stderr="",
        )

    is_sup, reason = detect_superseded_code(
        ["dual_pipeline/cli/ingest.py"],
        branch="fix/some-feature",
        clean_merge=False,
        behind=5,
        git_runner=fake_git_runner,
    )
    assert is_sup is True
    assert "cherry-picked" in str(reason).lower() or "merged" in str(reason).lower()


def test_detect_superseded_code_overlapping_merge() -> None:
    def fake_git_runner(cmd: list[str], root: Path) -> subprocess.CompletedProcess:
        if cmd[1] == "cherry":
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="+ commit1\n", stderr="")
        if cmd[1] == "log":
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="05124c464\tMerge branch 'fix/live-e2e-deterministic-certification' into main\n",
                stderr="",
            )
        if cmd[1] == "diff":
            if "-U0" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=0,
                    stdout="@@ -0,0 +1,2 @@\n+line1\n+line2\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="dual_pipeline/cli/ingest.py\ntools/run_live_e2e_matrix.py\ntests/e2e/test_live.py\n",
                stderr="",
            )
        if cmd[1] == "show":
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="line1\nline2\n",
                stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="")

    is_sup, reason = detect_superseded_code(
        ["dual_pipeline/cli/ingest.py", "tools/run_live_e2e_matrix.py"],
        branch="fix/ingestion-live-e2e-certification",
        clean_merge=False,
        behind=5,
        git_runner=fake_git_runner,
    )
    assert is_sup is True
    assert "05124c464" in str(reason)


def test_classify_branch_diff_superseded_category() -> None:
    def fake_git_runner(cmd: list[str], root: Path) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout="- sha1\n",
            stderr="",
        )

    diff_sem = classify_branch_diff(
        ["dual_pipeline/cli/ingest.py"],
        branch="fix/old-work",
        clean_merge=False,
        behind=5,
        git_runner=fake_git_runner,
    )
    assert diff_sem.category == "SUPERSEDED_CODE"
    assert diff_sem.is_superseded is True
    assert diff_sem.superseded_reason is not None
