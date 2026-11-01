#!/usr/bin/env python3
"""Git Worktree & Filesystem Hygiene Auditor.

Audits runtime evidence bindings, active git worktrees, orphaned .tmp files,
automatically cleans up worktrees for branches merged into main,
and verifies output directory hierarchy compliance.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

LOCAL_ROOT = Path(__file__).resolve().parents[1]
ROOT = LOCAL_ROOT
if "/.worktrees/" in str(ROOT):
    ROOT = Path(str(ROOT).split("/.worktrees/")[0]).resolve()
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
if str(LOCAL_ROOT) in sys.path:
    sys.path.remove(str(LOCAL_ROOT))
sys.path.insert(0, str(LOCAL_ROOT))

from tools.audit_worktree_hygiene_core.pruner import (  # noqa: E402
    _handle_clean_worktree,
    _handle_dirty_worktree,
    _should_process_worktree,
    cleanup_abandoned_zero_commit_worktrees,
    cleanup_orphaned_worktree_directories,
    preserve_worktree_outputs,  # noqa: F401
)
from tools.audit_worktree_hygiene_core.reporting import (  # noqa: E402
    build_hygiene_receipt,
    check_lingering_merged_worktrees,
)
from tools.pr_governance_core.registry import (  # noqa: E402
    verify_output_hierarchy,
    verify_worktree_registry,
)

logger = logging.getLogger("tools.audit_worktree_hygiene")


@dataclass
class WorktreeHygieneReport:
    timestamp_utc: str
    active_worktrees: List[Dict[str, str]]
    dangling_tmp_files: List[str]
    cleaned_tmp_files_count: int
    cleaned_merged_worktrees: List[str]
    output_hierarchy_violations: List[str]
    is_hygiene_compliant: bool
    worktree_registry_violations: List[str] = field(default_factory=list)
    cleaned_orphaned_worktrees: List[str] = field(default_factory=list)
    cleaned_zero_commit_worktrees: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _git_env() -> Dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("GIT_") or k in ("GIT_EXEC_PATH", "GIT_TEMPLATE_DIR")
    }


def get_git_worktrees(repo_root: Path) -> List[Dict[str, str]]:
    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
            env=_git_env(),
        )
        worktrees, current = [], {}
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line and current:
                worktrees.append(current)
                current = {}
            elif line.startswith("worktree "):
                current["path"] = line.split(" ", 1)[1]
            elif line.startswith("HEAD "):
                current["head"] = line.split(" ", 1)[1]
            elif line.startswith("branch "):
                current["branch"] = line.split(" ", 1)[1]
        if current:
            worktrees.append(current)
        return worktrees
    except Exception as exc:
        return [{"path": str(repo_root), "head": "unknown", "branch": str(exc)}]


def is_branch_merged(branch: str, repo_root: Path, base_branch: str = "main") -> bool:
    """Check if branch is strictly and genuinely merged into base_branch.

    Three conditions must ALL hold (RC-1 strict merged qualification):
    1. branch tip == merge-base(base_branch, branch)  — tip is an ancestor of base
    2. branch tip != base_branch tip                  — not a zero-commit branch off current base
    3. Proof of merge: explicit merge commit or PR merge receipt exists.
       Prevents fresh branches created off older main commits from false-positive qualification.
    If the branch ref was already deleted, checks for explicit merge proof.
    """
    try:
        env = _git_env()
        slug = branch.replace("/", "_").replace("-", "_")
        receipt_file = repo_root / "reports" / f"pr_merge_{slug}_receipt.json"

        def _has_merge_proof() -> bool:
            if receipt_file.is_file():
                return True
            merge_commit = subprocess.run(
                [
                    "git",
                    "log",
                    base_branch,
                    "--merges",
                    f"--grep=Merge.*{branch}",
                    "-n",
                    "1",
                    "--format=%H",
                ],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                check=False,
                env=env,
            ).stdout.strip()
            return bool(merge_commit)

        tip_res = subprocess.run(
            ["git", "rev-parse", branch],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        if tip_res.returncode != 0:
            return _has_merge_proof()

        tip = tip_res.stdout.strip()
        base_tip = subprocess.run(
            ["git", "rev-parse", base_branch],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
            env=env,
        ).stdout.strip()
        merge_base = subprocess.run(
            ["git", "merge-base", base_branch, branch],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
            env=env,
        ).stdout.strip()

        # Condition 1: tip must be an ancestor of base_branch
        if not (tip and merge_base and tip == merge_base):
            return False

        # Condition 2: exclude zero-commit branches (tip == current base HEAD)
        if tip == base_tip:
            return False

        # Condition 3: explicit merge proof
        return _has_merge_proof()
    except Exception:
        return False


def is_worktree_clean(wt_path: Path) -> bool:
    """Return True only if the worktree has no modified, staged, or untracked files."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(wt_path),
            capture_output=True,
            text=True,
            check=False,
            env=_git_env(),
        )
        return result.returncode == 0 and not result.stdout.strip()
    except Exception:
        return False


def cleanup_merged_worktrees(
    repo_root: Path,
    base_branch: str = "main",
    scoped_branch: Optional[str] = None,
    delete_branch: bool = False,
    force: bool = False,
) -> List[str]:
    """Identify and remove git worktrees whose branches are genuinely merged into base_branch."""
    cleaned: List[str] = []
    worktrees = get_git_worktrees(repo_root)
    root_resolved = repo_root.resolve()

    for wt in worktrees:
        target = _should_process_worktree(wt, root_resolved, base_branch, scoped_branch)
        if not target:
            continue
        wt_path, branch = target
        if not is_branch_merged(branch, repo_root, base_branch):
            continue

        if not is_worktree_clean(wt_path):
            entry = _handle_dirty_worktree(wt_path, branch, repo_root, delete_branch, force)
        else:
            entry = _handle_clean_worktree(wt_path, branch, repo_root, delete_branch)

        if entry:
            cleaned.append(entry)

    if cleaned:
        subprocess.run(
            ["git", "worktree", "prune"], cwd=str(repo_root), capture_output=True, check=False
        )

    return cleaned


def find_dangling_tmp_files(repo_root: Path) -> List[Path]:
    """Find all unmanaged .tmp or temporary writer files in repo."""
    tmp_files: List[Path] = []
    for p in repo_root.rglob("*.tmp*"):
        rel = str(p.relative_to(repo_root))
        if ".git" in rel or ".venv" in rel or "site-packages" in rel:
            continue
        if p.is_file():
            tmp_files.append(p)
    return tmp_files


def audit_hygiene(
    repo_root: Path,
    clean_tmp: bool = False,
    clean_merged_wt: bool = False,
    delete_branch: bool = False,
    scoped_branch: Optional[str] = None,
    check_merged: bool = False,
    strict_prune: bool = False,
    confirm_token: Optional[str] = None,
) -> WorktreeHygieneReport:
    """Execute complete worktree and filesystem hygiene audit."""
    cleaned_orphaned: List[str] = []
    cleaned_zero_commit: List[str] = []
    cleaned_wts: List[str] = []

    if strict_prune:
        cleaned_orphaned = cleanup_orphaned_worktree_directories(
            repo_root, confirm_token=confirm_token
        )
        if confirm_token == "CONFIRMED_STRICT_PRUNE":
            cleaned_zero_commit = cleanup_abandoned_zero_commit_worktrees(repo_root)
            clean_merged_wt = True
            clean_tmp = True
            delete_branch = True
        else:
            logger.warning(
                "Strict prune requested without valid confirm_token='CONFIRMED_STRICT_PRUNE'. "
                "Operating in safe dry-run mode."
            )

    if clean_merged_wt:
        cleaned_wts = cleanup_merged_worktrees(
            repo_root,
            scoped_branch=scoped_branch,
            delete_branch=delete_branch,
            force=strict_prune,
        )

    tmp_files: List[Path] = []
    cleaned_count = 0

    if clean_tmp or check_merged or strict_prune:
        tmp_files = find_dangling_tmp_files(repo_root)
        if clean_tmp:
            for tf in tmp_files:
                try:
                    tf.unlink()
                    cleaned_count += 1
                except Exception as e:
                    logger.warning(f"Failed to remove {tf}: {e}")

    output_violations = verify_output_hierarchy(repo_root)
    worktrees = get_git_worktrees(repo_root)
    target_worktrees = [
        wt for wt in worktrees
        if not scoped_branch or wt.get("branch", "").replace("refs/heads/", "") == scoped_branch or wt.get("branch", "") == scoped_branch
    ]
    registry_violations = verify_worktree_registry(repo_root, target_worktrees)
    errors: List[str] = list(output_violations) + list(registry_violations)
    remaining_tmp = len(tmp_files) - cleaned_count
    if remaining_tmp > 0:
        errors.append(f"Found {remaining_tmp} dangling temporary files")

    if check_merged:
        check_lingering_merged_worktrees(
            repo_root,
            worktrees,
            scoped_branch,
            is_branch_merged,
            is_worktree_clean,
            errors,
        )

    return WorktreeHygieneReport(
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        active_worktrees=worktrees,
        dangling_tmp_files=[str(p.relative_to(repo_root)) for p in tmp_files],
        cleaned_tmp_files_count=cleaned_count,
        cleaned_merged_worktrees=cleaned_wts,
        output_hierarchy_violations=output_violations,
        worktree_registry_violations=registry_violations,
        is_hygiene_compliant=(len(errors) == 0),
        cleaned_orphaned_worktrees=cleaned_orphaned,
        cleaned_zero_commit_worktrees=cleaned_zero_commit,
        errors=errors,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Git Worktree & Filesystem Hygiene Auditor")
    parser.add_argument("--repo-root", "--repository-root", dest="repo_root", type=Path, default=ROOT, help="Repository root path")
    parser.add_argument("--clean-dangling-tmp", action="store_true", help="Clean orphaned temporary files")
    parser.add_argument("--clean-merged-worktrees", action="store_true", help="Clean worktrees whose branches are merged into main")
    parser.add_argument("--delete-branch", action="store_true", help="Delete branch ref on merged worktree cleanup")
    parser.add_argument("--branch", default=None, help="Scope merged-worktree cleanup/check to a single branch (RC-4).")
    parser.add_argument("--check", action="store_true", help="Exit non-zero if hygiene violations exist")
    parser.add_argument("--strict-prune", action="store_true", help="Clean orphaned directories and abandoned zero-commit worktrees")
    parser.add_argument("--confirm-token", default=None, help="Explicit confirmation token required for destructive strict pruning")
    parser.add_argument("--purge-quarantine", default=None, help="Purge quarantine older than date MM-DD-YYYY (requires --confirm-token)")
    parser.add_argument("--receipt", type=Path, default=None, help="Output path for hygiene receipt")
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    args = parser.parse_args(argv)

    if args.purge_quarantine:
        from tools.audit_worktree_hygiene_core import purge_quarantine

        purged = purge_quarantine(args.repo_root, args.purge_quarantine, confirm_token=args.confirm_token)
        print(f"Purged {len(purged)} quarantined worktree(s) from {args.purge_quarantine}")
        return 0

    report = audit_hygiene(
        args.repo_root,
        clean_tmp=args.clean_dangling_tmp,
        clean_merged_wt=args.clean_merged_worktrees,
        delete_branch=args.delete_branch,
        scoped_branch=args.branch,
        check_merged=args.check,
        strict_prune=args.strict_prune,
        confirm_token=args.confirm_token,
    )

    if args.receipt:
        receipt = build_hygiene_receipt(report, command=" ".join(sys.argv))
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        status_line = "PASSED" if report.is_hygiene_compliant else "FAILED"
        print(
            f"Worktree Hygiene: {len(report.active_worktrees)} active worktrees, "
            f"{len(report.dangling_tmp_files)} tmp files ({report.cleaned_tmp_files_count} cleaned), "
            f"{len(report.cleaned_merged_worktrees)} merged worktrees cleaned, "
            f"{len(report.output_hierarchy_violations)} hierarchy violations, "
            f"{len(report.worktree_registry_violations)} registry violations -> {status_line}"
        )
        for wt_cleaned in report.cleaned_merged_worktrees:
            print(f"  🧹 Cleaned merged worktree: {wt_cleaned}")
        for err in report.errors:
            print(f"  ❌ {err}")

    return 1 if (args.check and not report.is_hygiene_compliant) else 0


if __name__ == "__main__":
    sys.exit(main())
