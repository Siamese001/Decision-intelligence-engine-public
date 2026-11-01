#!/usr/bin/env python3
"""
Tier 3: Instant Pre-Push Receipt Verification Gate (<2s).

Enforces the solo-developer backup model:
Since local main is the authoritative gate (where Tier 2 review and merge occur),
pushing local main to origin/main requires only confirming that a verified
Tier 2 quality receipt (readiness or merge receipt) exists with terminal outcome PASS.

Zero re-execution of test suites or linters.
Execution budget: < 2 seconds.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def run_git(args: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, check=False)


def check_origin_divergence(
    repo_root: Path = ROOT, branch: Optional[str] = None
) -> tuple[bool, int, str]:
    """Ensures remote origin does not have commits unmerged into local branch."""
    target_ref = branch or "main"
    div_res = run_git(["rev-list", "--count", f"{target_ref}..origin/{target_ref}"], cwd=repo_root)
    if div_res.returncode == 0 and div_res.stdout.strip().isdigit():
        behind = int(div_res.stdout.strip())
        if behind > 0:
            return (
                False,
                behind,
                f"Trunk divergence failure: origin/{target_ref} has {behind} commits not in local '{target_ref}'. Pull/rebase before pushing.",
            )
        return True, 0, "No remote divergence (behind: 0)."

    return True, 0, "Remote divergence check passed."


def check_origin_lag(
    repo_root: Path = ROOT, max_lag: int = 5, branch: Optional[str] = None
) -> tuple[bool, int, str]:
    """Ensures local commits ahead of origin do not exceed max_lag."""
    target_ref = branch or "main"
    lag_res = run_git(["rev-list", "--count", f"origin/{target_ref}..{target_ref}"], cwd=repo_root)
    if lag_res.returncode == 0 and lag_res.stdout.strip().isdigit():
        lag = int(lag_res.stdout.strip())
        if lag > max_lag:
            return (
                False,
                lag,
                f"Trunk synchronization failure: local '{target_ref}' is {lag} commits ahead of origin/{target_ref} (maximum allowed lag is {max_lag}).",
            )
        return True, lag, f"Origin lag is {lag} commits (within limit of {max_lag})."

    return True, 0, "Origin lag check passed."


def _check_dsse_attestation(repo_root: Path, head_sha: str) -> Optional[str]:
    try:
        from tools.attestation_core import compute_tree_digest, load_attestation_by_tree

        digest = compute_tree_digest(repo_root)
        stmt = load_attestation_by_tree(repo_root, digest, head_sha)
        if stmt:
            return f"Verified exact out-of-tree DSSE attestation for tree {digest[:12]}."
    except Exception:
        pass
    return None


def _check_readiness_receipt(reports_dir: Path, head_sha: str, repo_root: Path) -> Optional[str]:
    main_receipt_file = reports_dir / "local_main_readiness_receipt.json"
    if not main_receipt_file.is_file():
        return None
    try:
        data = json.loads(main_receipt_file.read_text(encoding="utf-8"))
        if data.get("terminal_outcome") != "PASS":
            return None
        receipt_sha = data.get("head_commit_sha", "")
        # Require exact match to current HEAD to reject stale ancestor evidence
        if receipt_sha == head_sha:
            return (
                f"Verified passing Tier 2 local main readiness receipt "
                f"(exact match for HEAD {head_sha[:8]})."
            )
    except Exception:
        pass
    return None


def _check_merge_commit_head(repo_root: Path, head_sha: str) -> Optional[str]:
    """Validates that current HEAD is a governed merge commit with an authentic merge receipt."""
    import re
    from tools.pr_governance_core.common import slugify_branch

    parents_res = run_git(["rev-list", "--parents", "-n", "1", head_sha], cwd=repo_root)
    if parents_res.returncode != 0:
        return None
    parts = parents_res.stdout.strip().split()
    if len(parts) < 3:
        return None
    parent1, parent2 = parts[1], parts[2]

    msg_res = run_git(["log", "-1", "--format=%s", head_sha], cwd=repo_root)
    if msg_res.returncode != 0:
        return None
    msg = msg_res.stdout.strip()
    m = re.search(r"Merge (?:remote-tracking )?branch ['\"]([^'\"]+)['\"]", msg)
    if not m:
        return None
    branch = m.group(1).strip()
    slug = slugify_branch(branch)

    # Load merge receipt from HEAD commit tree or reports/ directory
    receipt_data = None
    show_res = run_git(["show", f"{head_sha}:reports/pr_merge_{slug}_receipt.json"], cwd=repo_root)
    if show_res.returncode == 0 and show_res.stdout.strip():
        try:
            receipt_data = json.loads(show_res.stdout)
        except Exception:
            pass
    if not receipt_data:
        disk_receipt = repo_root / "reports" / f"pr_merge_{slug}_receipt.json"
        if disk_receipt.is_file():
            try:
                receipt_data = json.loads(disk_receipt.read_text(encoding="utf-8"))
            except Exception:
                pass

    if receipt_data and receipt_data.get("terminal_outcome") == "PASS":
        receipt_branch_head = receipt_data.get("branch_head_sha") or receipt_data.get("head_sha")
        if receipt_branch_head == parent2:
            return (
                f"Verified governed merge commit for '{branch}' "
                f"(feature HEAD: {parent2[:8]}, merge: {head_sha[:8]})."
            )
    return None


def verify_push_receipt(
    repo_root: Path = ROOT, max_commit_age: int = 20, max_lag: Optional[int] = None
) -> tuple[bool, str, float]:
    start = time.time()
    reports_dir = repo_root / "reports"

    # 1. Divergence check (fail closed if origin has unmerged commits)
    div_ok, _, div_msg = check_origin_divergence(repo_root)
    if not div_ok:
        if os.environ.get("GOVERNED_PUSH_FORCE") == "1":
            return (
                True,
                f"⚠️ Push authorized via GOVERNED_PUSH_FORCE override (bypassing: {div_msg}).",
                round(time.time() - start, 3),
            )
        return False, div_msg, round(time.time() - start, 3)

    # 2. Origin lag check (only when explicitly requested via max_lag)
    if max_lag is not None:
        lag_ok, _, lag_msg = check_origin_lag(repo_root, max_lag=max_lag)
        if not lag_ok:
            if os.environ.get("GOVERNED_PUSH_FORCE") == "1":
                return (
                    True,
                    f"⚠️ Push authorized via GOVERNED_PUSH_FORCE override (bypassing: {lag_msg}).",
                    round(time.time() - start, 3),
                )
            return (
                False,
                f"{lag_msg}\nPush after every governed merge to prevent trunk desynchronization.\n"
                f"To override for urgent manual sync: GOVERNED_PUSH_FORCE=1 git push",
                round(time.time() - start, 3),
            )

    # 3. Get current HEAD sha
    rev_res = run_git(["rev-parse", "HEAD"], cwd=repo_root)
    if rev_res.returncode != 0:
        return False, f"Not a valid git repository: {rev_res.stderr}", round(time.time() - start, 3)
    head_sha = rev_res.stdout.strip()

    # 4. Check out-of-tree DSSE tree attestation for exact HEAD
    att_msg = _check_dsse_attestation(repo_root, head_sha)
    if att_msg:
        return True, att_msg, round(time.time() - start, 3)

    # 5. Check local main readiness receipt for exact HEAD
    readiness_msg = _check_readiness_receipt(reports_dir, head_sha, repo_root)
    if readiness_msg:
        return True, readiness_msg, round(time.time() - start, 3)

    # 6. Check if current HEAD is an authentic governed merge commit
    merge_head_msg = _check_merge_commit_head(repo_root, head_sha)
    if merge_head_msg:
        return True, merge_head_msg, round(time.time() - start, 3)

    # 7. Allow bypass if explicitly authorized
    if os.environ.get("GOVERNED_PUSH_FORCE") == "1":
        return (
            True,
            "⚠️ Push authorized via GOVERNED_PUSH_FORCE override.",
            round(time.time() - start, 3),
        )

    return (
        False,
        "No passing Tier 2 governance receipt found in reports/ for current branch/commit.\n"
        "Run 'python tools/pr_governance.py review <branch>' and merge to local main before pushing to origin.\n"
        "To override for urgent manual sync: GOVERNED_PUSH_FORCE=1 git push",
        round(time.time() - start, 3),
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Tier 3 Instant Pre-Push Receipt Verification.")
    parser.add_argument("--root", type=Path, default=ROOT, help="Repository root")
    parser.add_argument("--max-lag", type=int, default=None, help="Maximum allowed unpushed commit lag")
    args = parser.parse_args(argv)

    passed, message, dur = verify_push_receipt(args.root, max_lag=args.max_lag)
    if passed:
        print(f"✅ TIER 3 PRE-PUSH: {message} ({dur}s)")
        return 0
    else:
        print(f"❌ TIER 3 PRE-PUSH BLOCKED: {message} ({dur}s)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
