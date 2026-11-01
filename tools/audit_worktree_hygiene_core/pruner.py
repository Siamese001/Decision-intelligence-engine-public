"""
tools/audit_worktree_hygiene_core/pruner.py

Pruning routines for orphaned worktree directories and abandoned zero-commit worktrees.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("tools.audit_worktree_hygiene.pruner")


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def _git_env() -> Dict[str, str]:
    env = os.environ.copy()
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(var, None)
    return env


def preserve_worktree_outputs(wt_path: Path, repo_root: Path) -> int:
    """
    Preserves output/ files from worktree into main repo before teardown.
    Protects against silent collisions by saving divergent files as _collision_<wt_name>.
    """
    wt_output = wt_path / "output"
    if not wt_output.is_dir():
        return 0
    preserved = 0
    repo_output = repo_root / "output"
    for src_file in wt_output.rglob("*"):
        if src_file.is_file() and not src_file.is_symlink():
            rel_path = src_file.relative_to(wt_output)
            dst_file = repo_output / rel_path
            if not dst_file.exists():
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)
                preserved += 1
            else:
                src_hash = _file_sha256(src_file)
                dst_hash = _file_sha256(dst_file)
                if src_hash and dst_hash and src_hash != dst_hash:
                    collision_name = f"{dst_file.stem}_collision_{wt_path.name}{dst_file.suffix}"
                    collision_target = dst_file.with_name(collision_name)
                    if not collision_target.exists():
                        collision_target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_file, collision_target)
                        preserved += 1
                        logger.warning(
                            f"Preserved divergent output collision: {rel_path} -> {collision_name}"
                        )
    return preserved


def cleanup_orphaned_worktree_directories(
    repo_root: Path, confirm_token: Optional[str] = None
) -> List[str]:
    """
    Find and quarantine directory entries under .worktrees that have no git worktree binding.
    Requires confirm_token=='CONFIRMED_STRICT_PRUNE'. Without token, runs in dry-run mode.
    Never quarantines directories holding unique deliverables.
    """
    wt_dir = repo_root / ".worktrees"
    if not wt_dir.is_dir():
        return []
    cleaned: List[str] = []
    from tools.audit_worktree_hygiene import get_git_worktrees
    from tools.pr_governance_core.worktree_inventory import classify_orphan_directory

    registered = {Path(wt["path"]).resolve() for wt in get_git_worktrees(repo_root) if "path" in wt}
    candidates: List[Path] = []
    for entry in sorted(wt_dir.iterdir()):
        if entry.is_dir() and entry.resolve() not in registered and not entry.name.startswith("."):
            candidates.append(entry)

    if not candidates:
        return []

    if confirm_token != "CONFIRMED_STRICT_PRUNE":
        logger.warning(
            "cleanup_orphaned_worktree_directories requires confirm_token='CONFIRMED_STRICT_PRUNE'. "
            f"Dry run: {len(candidates)} orphan directory(ies) identified."
        )
        return [f"{c.name} (dry-run: quarantine required)" for c in candidates]

    today_str = datetime.now(timezone.utc).strftime("%m-%d-%Y")
    quarantine_root = repo_root / ".local" / "quarantine" / "worktrees" / today_str

    for entry in candidates:
        classification, _unique_files = classify_orphan_directory(entry, repo_root)
        if classification == "ORPHAN_DELIVERABLES":
            logger.error(
                f"REFUSED: Cannot prune orphan directory '{entry.name}' holding unique deliverables. "
                "Recover deliverables first."
            )
            continue

        preserve_worktree_outputs(entry, repo_root)

        manifest_files = []
        for root, dirs, files in os.walk(entry):
            if ".git" in dirs:
                dirs.remove(".git")
            for f in files:
                fp = Path(root) / f
                if not fp.is_symlink() and fp.is_file():
                    try:
                        rel = str(fp.relative_to(entry))
                        manifest_files.append({
                            "path": rel,
                            "size": fp.stat().st_size,
                            "sha256": _file_sha256(fp),
                        })
                    except Exception:
                        pass

        manifest_data = {
            "directory_name": entry.name,
            "classification": classification,
            "quarantined_at": datetime.now(timezone.utc).isoformat(),
            "files_count": len(manifest_files),
            "files": manifest_files,
        }

        quarantine_target = quarantine_root / entry.name
        quarantine_target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(entry), str(quarantine_target))
            manifest_path = quarantine_target / "quarantine_manifest.json"
            manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
            cleaned.append(entry.name)
            logger.info(f"Quarantined orphan directory '{entry.name}' to {quarantine_target}")
        except Exception as e:
            logger.warning(f"Could not quarantine orphaned worktree dir {entry.name}: {e}")

    return cleaned


def purge_quarantine(
    repo_root: Path, target_date: str, confirm_token: Optional[str] = None
) -> List[str]:
    """Purges a specific quarantine date folder (.local/quarantine/worktrees/<date>). Requires token."""
    if confirm_token != "CONFIRMED_STRICT_PRUNE":
        logger.warning("purge_quarantine requires confirm_token='CONFIRMED_STRICT_PRUNE'.")
        return []
    quarantine_date_dir = repo_root / ".local" / "quarantine" / "worktrees" / target_date
    if not quarantine_date_dir.is_dir():
        return []
    purged: List[str] = []
    for item in quarantine_date_dir.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
            purged.append(item.name)
    with contextlib.suppress(Exception):
        quarantine_date_dir.rmdir()
    return purged


def _is_abandoned_live_worktree(
    wt_path: Path, branch: str, repo_root: Path, base_tip: str, max_age_hours: float = 0.0
) -> bool:
    """Checks whether a worktree has 0 unique commits vs base, 0 unique output artifacts, and exceeds TTL."""
    branch_tip = subprocess.run(
        ["git", "rev-parse", branch],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=_git_env(),
    ).stdout.strip()
    if not (branch_tip and base_tip):
        return False

    if branch_tip != base_tip:
        anc_res = subprocess.run(
            ["git", "merge-base", "--is-ancestor", branch_tip, base_tip],
            cwd=str(repo_root),
            capture_output=True,
            check=False,
            timeout=30,
            env=_git_env(),
        )
        if anc_res.returncode != 0:
            return False
        ahead_res = subprocess.run(
            ["git", "rev-list", "--left-right", "--count", f"{base_tip}...{branch_tip}"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env=_git_env(),
        ).stdout.split()
        ahead = int(ahead_res[1]) if len(ahead_res) >= 2 else 999
        if ahead != 0:
            return False

    if max_age_hours > 0:
        try:
            stat_time = wt_path.stat().st_mtime
            age_hours = (datetime.now(timezone.utc).timestamp() - stat_time) / 3600.0
            if age_hours < max_age_hours:
                return False
        except Exception:
            pass

    status_raw = subprocess.run(
        ["git", "status", "--porcelain=v2", "-z", "--untracked-files=all"],
        cwd=str(wt_path),
        capture_output=True,
        check=False,
        timeout=30,
        env=_git_env(),
    )
    if status_raw.returncode == 0 and status_raw.stdout:
        for line in status_raw.stdout.split(b"\x00"):
            if not line:
                continue
            return False

    output_dir = wt_path / "output"
    if output_dir.exists():
        artifacts = [
            f for f in output_dir.rglob("*")
            if f.is_file() and f.stat().st_size > 0 and (f.suffix in (".wav", ".md", ".json") and not f.name.startswith("."))
        ]
        for f in artifacts:
            rel = f.relative_to(output_dir)
            repo_equiv = repo_root / "output" / rel
            if not repo_equiv.exists() or _file_sha256(f) != _file_sha256(repo_equiv):
                return False

    return True


def cleanup_abandoned_zero_commit_worktrees(
    repo_root: Path, base_branch: str = "main", max_age_hours: float = 0.0
) -> List[str]:
    """Prunes non-root worktrees that have 0 unique commits, 0 dirty/output files, and were abandoned."""
    cleaned: List[str] = []
    from tools.audit_worktree_hygiene import get_git_worktrees

    worktrees = get_git_worktrees(repo_root)
    root_resolved = repo_root.resolve()
    base_tip = subprocess.run(
        ["git", "rev-parse", base_branch],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=_git_env(),
    ).stdout.strip()

    for wt in worktrees:
        wt_path_str = wt.get("path")
        wt_branch_ref = wt.get("branch")
        if not wt_path_str or not wt_branch_ref:
            continue
        wt_path = Path(wt_path_str).resolve()
        if wt_path == root_resolved:
            continue
        branch = wt_branch_ref.replace("refs/heads/", "")
        if branch in ("main", "master") or branch.startswith(("backup/", "archive/")):
            continue

        if _is_abandoned_live_worktree(wt_path, branch, repo_root, base_tip, max_age_hours=max_age_hours):
            logger.info(f"Pruning abandoned zero-commit worktree '{branch}' at {wt_path}...")
            subprocess.run(["git", "worktree", "remove", "--force", str(wt_path)], cwd=str(repo_root), check=False, timeout=30)
            subprocess.run(["git", "branch", "-D", branch], cwd=str(repo_root), check=False, timeout=30)
            cleaned.append(f"{branch} (abandoned zero-commit)")
    return cleaned


def _should_process_worktree(
    wt: Dict[str, str],
    root_resolved: Path,
    base_branch: str,
    scoped_branch: Optional[str],
) -> Optional[Tuple[Path, str]]:
    """Filters candidate worktree, returning (wt_path, branch) or None."""
    wt_path_str = wt.get("path")
    wt_branch_ref = wt.get("branch")
    if not wt_path_str or not wt_branch_ref:
        return None
    wt_path = Path(wt_path_str).resolve()
    if wt_path == root_resolved:
        return None
    branch = wt_branch_ref.replace("refs/heads/", "")
    if branch in (base_branch, "main"):
        return None
    if scoped_branch is not None and branch != scoped_branch:
        return None
    return wt_path, branch


def _delete_branch_ref(branch: str, repo_root: Path, force: bool = False) -> None:
    flag = "-D" if force else "-d"
    b_res = subprocess.run(
        ["git", "branch", flag, branch],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if b_res.returncode == 0:
        logger.info(f"Deleted merged branch '{branch}'.")
    else:
        logger.warning(f"Could not delete branch '{branch}': {b_res.stderr.strip()}")


def _handle_dirty_worktree(
    wt_path: Path, branch: str, repo_root: Path, delete_branch: bool, force: bool
) -> Optional[str]:
    if not force:
        logger.warning(
            f"Skipping merged worktree '{branch}' at {wt_path}: uncommitted changes. Run 'finish --force'."
        )
        return None
    preserve_worktree_outputs(wt_path, repo_root)
    logger.info(f"Force removing merged worktree '{branch}' at {wt_path}...")
    res = subprocess.run(
        ["git", "worktree", "remove", "--force", str(wt_path)],
        cwd=str(repo_root), capture_output=True, text=True, check=False,
    )
    if res.returncode != 0:
        return None
    if delete_branch:
        _delete_branch_ref(branch, repo_root, force=True)
    return f"{branch} ({wt_path.name})"


def _handle_clean_worktree(
    wt_path: Path, branch: str, repo_root: Path, delete_branch: bool
) -> Optional[str]:
    logger.info(f"Removing merged worktree '{branch}' at {wt_path}...")
    res = subprocess.run(
        ["git", "worktree", "remove", str(wt_path)],
        cwd=str(repo_root), capture_output=True, text=True, check=False,
    )
    if res.returncode != 0:
        logger.warning(f"git worktree remove failed for '{branch}': {res.stderr.strip()}")
        return None
    if delete_branch:
        _delete_branch_ref(branch, repo_root, force=False)
    return f"{branch} ({wt_path.name})"
