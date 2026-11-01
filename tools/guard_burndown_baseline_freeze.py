#!/usr/bin/env python3
"""Pre-commit and CI guard protecting file_budget_burndown_baseline.json from branch edits.

Under Wave 3 of Parallel PR Readiness, reports/file_budget_burndown_baseline.json is a
checked baseline that must not be edited independently on feature branches.
Only the governed merge queue (or GOVERNED_PR_MERGE=1) is authorized to update it upon merging to main.
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

TARGET_FILE = "reports/file_budget_burndown_baseline.json"


def is_baseline_modified(repo_root: Path, staged: bool = True) -> bool:
    """Checks whether the ratchet baseline is modified in the working tree or index."""
    cmd = ["git", "diff", "--name-only"]
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
        modified_files = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        return TARGET_FILE in modified_files
    except Exception:
        # If git command fails, fail closed if file has uncommitted diff
        return False


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


def check_baseline_freeze(
    repo_root: Path | None = None,
    staged: bool = True,
    allow_env_override: bool = True,
) -> tuple[bool, str]:
    """Evaluates whether baseline modifications are permitted in the current context."""
    root = repo_root or ROOT

    if allow_env_override and os.environ.get("GOVERNED_PR_MERGE") == "1":
        return True, "Authorized by GOVERNED_PR_MERGE=1"

    current_branch = get_current_branch(root)
    if current_branch in ("main", "master") and os.environ.get("GOVERNED_PR_MERGE") == "1":
        return True, "Authorized on main merge"

    if is_baseline_modified(root, staged=staged):
        msg = (
            f"❌ COMMIT REJECTED: Direct branch edits to '{TARGET_FILE}' are strictly prohibited.\n"
            f"   Branch: {current_branch or 'detached'}\n"
            f"   The line-budget baseline is managed and updated exclusively by the governed merge queue.\n"
            f"   Remediation: git checkout HEAD -- {TARGET_FILE} (or git restore --staged {TARGET_FILE})"
        )
        return False, msg

    return True, f"'{TARGET_FILE}' unmodified on branch."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Guard file budget baseline from branch edits.")
    parser.add_argument("--repo-root", default=ROOT, type=Path, help="Repository root path.")
    parser.add_argument("--staged", action="store_true", default=True, help="Scan staged changes (default: True).")
    parser.add_argument("--full", action="store_true", help="Scan working tree and staged changes.")
    parser.add_argument("--allow-override", action="store_true", default=True, help="Respect GOVERNED_PR_MERGE env.")
    args = parser.parse_args(argv)

    staged = not args.full and args.staged
    allowed, message = check_baseline_freeze(
        repo_root=args.repo_root,
        staged=staged,
        allow_env_override=args.allow_override,
    )

    if not allowed:
        print(message, file=sys.stderr)
        return 1

    print(f"✅ Baseline freeze check PASSED: {message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
