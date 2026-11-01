"""
Post-mutation teardown, deliverable auto-commit, and safe dehydration routines for isolated ingestion worktrees.
"""
from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.ingest_worktree_cleanup")


def run_git(cmd: list[str], cwd: Optional[Path] = None, check: bool = True) -> str:
    """Executes git command with sanitized environment and returns stripped stdout."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX")
    }
    res = subprocess.run(
        ["git"] + cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        check=check,
        timeout=30.0,
    )
    return res.stdout.strip()


def _resolve_primary_repo_root(path: Path) -> Path:
    """Resolves primary repo root even if current path is inside an isolated worktree."""
    try:
        common_dir = run_git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=path)
        if common_dir and Path(common_dir).name == ".git":
            return Path(common_dir).parent
    except Exception:
        pass
    p = path.resolve()
    for parent in (p, *p.parents):
        if (parent / ".git").is_dir():
            return parent
    return p


@contextlib.contextmanager
def git_worktree_provision_lock(repo_root: Path, timeout_seconds: float = 30.0):
    """Advisory file lock around git worktree add/remove and branch operations."""
    lock_dir = (repo_root / ".git") if (repo_root / ".git").is_dir() else (repo_root / ".session_locks")
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "nee_worktree_provision.flock"

    fd = open(lock_path, "a+")  # noqa: SIM115
    start_t, acquired = time.monotonic(), False
    while time.monotonic() - start_t < timeout_seconds:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            break
        except (BlockingIOError, OSError):
            time.sleep(0.02)

    if not acquired:
        fd.close()
        raise TimeoutError(f"Could not acquire git worktree provision lock within {timeout_seconds}s at {lock_path}")
    try:
        yield lock_path
    finally:
        with contextlib.suppress(Exception):
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()


def _auto_commit_worktree_deliverables(worktree_dir: Path, branch_name: str) -> bool:
    """Stages and commits uncommitted deliverable files inside worktree_dir before dehydration."""
    try:
        from tools.pr_governance_core.worktree import parse_porcelain_v2_z, partition_worktree_dirty_files

        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX")
        }
        proc = subprocess.run(
            ["git", "status", "--porcelain=v2", "-z", "--untracked-files=all"],
            cwd=str(worktree_dir),
            env=env,
            capture_output=True,
            check=False,
            timeout=30.0,
        )
        if proc.returncode != 0:
            return True
        entries = parse_porcelain_v2_z(proc.stdout)
        dirty_paths = [e.get("path", "") for e in entries if e.get("path")]
        if not dirty_paths:
            return True

        partition = partition_worktree_dirty_files(dirty_paths)
        if partition.get("authored_code_files"):
            logger.error(
                "Aborting worktree teardown for %s: uncommitted authored code files present: %s",
                worktree_dir,
                partition["authored_code_files"],
            )
            return False

        to_stage = list(partition.get("deliverable_files", [])) + [
            p for p in partition.get("other_files", []) if p.startswith("reports/graph_mutation_proofs/")
        ]
        if not to_stage:
            return True

        run_git(["add", "--"] + to_stage, cwd=worktree_dir, check=True)
        run_git(
            ["commit", "-m", f"feat(ingest): auto-commit ingestion deliverables on {branch_name}"],
            cwd=worktree_dir,
            check=True,
        )
        return True
    except Exception as exc:
        logger.error("Failed auto-committing deliverables in %s: %s", worktree_dir, exc)
        return False


def _record_immutable_archive_ref(primary_root: Path, branch_name: str) -> str:
    """Writes refs/archive/<branch>/<sha> to guarantee zero commit lineage loss."""
    recorded_sha = ""
    with contextlib.suppress(Exception):
        recorded_sha = run_git(["rev-parse", f"refs/heads/{branch_name}"], cwd=primary_root, check=True).strip()
    if recorded_sha:
        archive_ref = f"refs/archive/{branch_name}/{recorded_sha}"
        with contextlib.suppress(Exception):
            run_git(["update-ref", archive_ref, recorded_sha, ""], cwd=primary_root, check=True)
    return recorded_sha


def _is_branch_safely_deletable(primary_root: Path, branch_name: str) -> bool:
    """Returns True only if branch_name has 0 unique commits beyond main or is genuinely merged into main."""
    try:
        unique = run_git(["rev-list", f"main..{branch_name}"], cwd=primary_root, check=False).strip()
        if not unique:
            return True
        from tools.pr_governance_core.worktree import _is_branch_merged

        return _is_branch_merged(branch_name, root=primary_root)
    except Exception:
        return False


def _repatriate_audio_safe(worktree_dir: Path, primary_root: Path) -> bool:
    """Safely repatriate audio artifacts before worktree removal."""
    try:
        from tools.pr_governance_core.audio_repatriation import repatriate_worktree_audio_artifacts

        repat = repatriate_worktree_audio_artifacts(worktree_dir, primary_root)
        if not repat.success:
            logger.error("Aborting worktree teardown for %s: audio repatriation failed (%s)", worktree_dir, repat.error)
            return False
        return True
    except Exception as exc:
        logger.error("Aborting worktree teardown for %s: audio repatriation error (%s)", worktree_dir, exc)
        return False


def _remove_worktree_and_finalize_branch(
    worktree_dir: Path,
    primary_root: Path,
    branch_name: str,
    recorded_sha: str,
    can_delete_branch: bool,
) -> bool:
    """Remove the worktree directory and delete branch ref only if safely merged."""
    success = True
    with contextlib.suppress(Exception):
        from tools.pr_governance_core.worktree import safely_unlink_worktree_dependencies

        safely_unlink_worktree_dependencies(worktree_dir)
    try:
        run_git(["worktree", "remove", "--force", str(worktree_dir)], cwd=primary_root, check=False)
    except Exception:
        success = False
    if worktree_dir.exists():
        shutil.rmtree(worktree_dir, ignore_errors=True)
    run_git(["worktree", "prune"], cwd=primary_root, check=False)

    if can_delete_branch and recorded_sha:
        try:
            run_git(["update-ref", "-d", f"refs/heads/{branch_name}", recorded_sha], cwd=primary_root, check=False)
        except Exception:
            success = False
    else:
        logger.info(
            "[WORKTREE_DEHYDRATED] Preserved unmerged branch '%s' (%s) in PR queue after worktree directory removal.",
            branch_name,
            recorded_sha[:8] if recorded_sha else "unknown",
        )
    return success


def teardown_ingest_worktree(
    worktree_dir: Optional[Path],
    branch_name: Optional[str],
    repo_root: Optional[Path] = None,
    force: bool = True,
) -> bool:
    """Safely commits deliverables, records archive ref, removes worktree dir, and deletes branch ONLY if merged."""
    if not worktree_dir or not branch_name or branch_name in ("main", "master"):
        return False
    worktree_dir = Path(worktree_dir).resolve()
    if not worktree_dir.exists():
        return False
    base_root = (repo_root or Path.cwd()).resolve()
    primary_root = _resolve_primary_repo_root(base_root)
    if worktree_dir in (primary_root, base_root) or worktree_dir.parent.name != ".worktrees":
        return False

    with git_worktree_provision_lock(primary_root):
        if not _repatriate_audio_safe(worktree_dir, primary_root):
            return False
        if not _auto_commit_worktree_deliverables(worktree_dir, branch_name):
            return False
        recorded_sha = _record_immutable_archive_ref(primary_root, branch_name)
        can_delete_branch = _is_branch_safely_deletable(primary_root, branch_name)
        return _remove_worktree_and_finalize_branch(
            worktree_dir, primary_root, branch_name, recorded_sha, can_delete_branch
        )
