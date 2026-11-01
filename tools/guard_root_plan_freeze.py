#!/usr/bin/env python3
"""Pre-commit and CI guard protecting root implementation_plan.md and walkthrough.md from branch edits.

Under Wave 5 of Parallel PR Readiness, implementation plans and walkthroughs must reside
in branch-isolated directories ('plans/<branch_slug>/implementation_plan.md') to eliminate
merge conflicts between parallel feature branches.

Direct modification or creation of root 'implementation_plan.md' or 'walkthrough.md'
is strictly prohibited on feature branches. Only the governed merge queue
(or GOVERNED_PR_MERGE=1) is authorized to perform legacy root plan updates if needed.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FROZEN_ROOT_PLAN_FILES = {
    "implementation_plan.md",
    "walkthrough.md",
}


def get_modified_plan_files(repo_root: Path, staged: bool = True) -> list[str]:
    """Checks whether any frozen root plan files are modified or added."""
    cmd = ["git", "diff", "--diff-filter=ACM", "--name-only"]
    if staged:
        cmd.append("--cached")
    try:
        res = subprocess.run(
            cmd,
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        modified = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        violations = [f for f in modified if f in FROZEN_ROOT_PLAN_FILES]
        if not staged:
            untracked_res = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            untracked = [line.strip() for line in untracked_res.stdout.splitlines() if line.strip()]
            violations.extend([f for f in untracked if f in FROZEN_ROOT_PLAN_FILES])
        return sorted(list(set(violations)))
    except Exception:
        return []


def get_current_branch(repo_root: Path) -> str:
    try:
        res = subprocess.run(
            ["git", "symbolic-ref", "--short", "-q", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        return res.stdout.strip()
    except Exception:
        return ""


def check_root_plan_freeze(
    repo_root: Path | None = None,
    staged: bool = True,
    allow_env_override: bool = True,
) -> tuple[bool, str]:
    """Evaluates whether root plan modifications are permitted in the current context."""
    root = repo_root or ROOT

    if allow_env_override and os.environ.get("GOVERNED_PR_MERGE") == "1":
        return True, "Authorized by GOVERNED_PR_MERGE=1"

    current_branch = get_current_branch(root)
    if current_branch in ("main", "master") and os.environ.get("GOVERNED_PR_MERGE") == "1":
        return True, "Authorized on main merge"

    violations = get_modified_plan_files(root, staged=staged)
    if violations:
        from tools.pr_governance_core.common import slugify_branch

        branch_slug = slugify_branch(current_branch) if current_branch else "<branch_slug>"
        viol_str = ", ".join(f"'{v}'" for v in violations)
        msg = (
            f"❌ COMMIT REJECTED: Direct branch edits to root plan files {viol_str} are strictly prohibited.\n"
            f"   Branch: {current_branch or 'detached'}\n"
            f"   Under Wave 5 (Parallel PR Readiness), plans and walkthroughs must reside in branch-isolated folders:\n"
            f"     plans/{branch_slug}/implementation_plan.md\n"
            f"     plans/{branch_slug}/walkthrough.md\n"
            f"   Remediation:\n"
            f"     mkdir -p plans/{branch_slug}\n"
            f"     git mv {violations[0]} plans/{branch_slug}/{violations[0]}\n"
            f"     git add plans/{branch_slug}/\n"
        )
        return False, msg

    return True, "Root plan files unmodified."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Guard root implementation_plan.md from branch edits.")
    parser.add_argument("--repo-root", default=ROOT, type=Path, help="Repository root path.")
    parser.add_argument("--staged", action="store_true", default=True, help="Scan staged changes (default: True).")
    parser.add_argument("--full", action="store_true", help="Scan working tree and staged changes.")
    parser.add_argument("--allow-override", action="store_true", default=True, help="Respect GOVERNED_PR_MERGE env.")
    args = parser.parse_args(argv)

    staged = not args.full and args.staged
    allowed, message = check_root_plan_freeze(
        repo_root=args.repo_root,
        staged=staged,
        allow_env_override=args.allow_override,
    )

    if not allowed:
        print(message, file=sys.stderr)
        return 1

    print(f"✅ Root plan freeze check PASSED: {message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
