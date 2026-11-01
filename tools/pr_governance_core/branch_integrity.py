"""
Branch and working tree integrity pre-flight checks for PR governance.
Enforces core.bare freeze and prevents direct unreviewed commits on main.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import List, Tuple


def run_branch_and_worktree_governance(root: Path) -> Tuple[bool, str, float]:
    """Execute Category 0 Pre-Flight: core.bare freeze and direct-to-main commit block."""
    start = time.time()
    errors: List[str] = []

    # 1. core.bare freeze check
    try:
        bare_res = subprocess.run(
            ["git", "config", "--bool", "core.bare"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if bare_res.returncode == 0 and bare_res.stdout.strip() == "true":
            errors.append(
                "[CORE_BARE_VIOLATION] Repository has core.bare = true. "
                "Primary working repository must never be bare.\n"
                "  Prescriptive Remediation: git config core.bare false"
            )
    except Exception as e:
        errors.append(f"Failed to check core.bare: {e}")

    # 2. Direct-to-main commit block
    try:
        branch_res = subprocess.run(
            ["git", "symbolic-ref", "--short", "-q", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        current_branch = branch_res.stdout.strip() if branch_res.returncode == 0 else ""
        if current_branch in ("main", "master"):
            is_merge = False
            for head_ref in ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD"):
                head_res = subprocess.run(
                    ["git", "rev-parse", "-q", "--verify", head_ref],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if head_res.returncode == 0 and head_res.stdout.strip():
                    is_merge = True
                    break

            git_dir = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=root, capture_output=True, text=True).stdout.strip()
            if (Path(root) / git_dir / "rebase-merge").exists() or (Path(root) / git_dir / "rebase-apply").exists():
                is_merge = True

            if not is_merge:
                errors.append(
                    f"[DIRECT_MAIN_COMMIT_BLOCKED] Direct commits to '{current_branch}' are strictly prohibited.\n"
                    f"  All development must occur in an isolated feature worktree (.worktrees/<slug>) "
                    f"and be merged via PR governance.\n"
                    f"  To start a feature branch: python tools/pr_governance.py start feat/<name>"
                )
    except Exception as e:
        errors.append(f"Failed to check current branch: {e}")

    if errors:
        return False, "\n".join(errors), round(time.time() - start, 3)
    return True, "Branch & Worktree Governance: PASS", round(time.time() - start, 3)
