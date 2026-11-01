"""
Worktree lifecycle commands for PR governance: start and finish.

Enforces isolated worktree development under .worktrees/<slug>/ to prevent
parallel workspace collisions and dirty-dump pollution in the repository root.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple

from .common import (
    CONVENTIONAL_BRANCH_RE,
    GENERATED_ARTIFACT_PATTERNS,
    ROOT,
    kill_ghost_processes,
    run_git,
    slugify_branch,
)

logger = logging.getLogger("tools.pr_governance_core.worktree")


@contextlib.contextmanager
def worktree_provisioning_lock(timeout_seconds: float = 120.0, root: Path = ROOT):
    """Acquires an advisory provisioning lock powered by filelock.FileLock."""
    from filelock import FileLock, Timeout

    lock_file = root / ".worktrees" / ".provisioning.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    flock = FileLock(str(lock_file), timeout=timeout_seconds)
    try:
        flock.acquire(timeout=timeout_seconds, poll_interval=0.05)
    except Timeout as exc:
        raise TimeoutError(
            f"Could not acquire worktree provisioning lock within {timeout_seconds}s at {lock_file}"
        ) from exc
    try:
        yield lock_file
    finally:
        with contextlib.suppress(Exception):
            flock.release()

def link_worktree_dependencies(root: Path, worktree_dir: Path) -> list[str]:
    """
    Symlinks runtime dependencies, local storage, editor configs, and agent governance
    into the isolated worktree using robust relative symlinks.
    """
    linked: list[str] = []
    candidates = (".venv", ".local", "env_di", ".vscode", ".agents")
    for link_name in candidates:
        src = root / link_name
        dst = worktree_dir / link_name
        if (src.exists() or src.is_symlink()) and not (dst.exists() or dst.is_symlink()):
            try:
                rel_target = os.path.relpath(src, worktree_dir)
                os.symlink(rel_target, dst)
                linked.append(link_name)
            except Exception as e:
                print(f"  ⚠️ Could not symlink {link_name}: {e}")
    return linked


def safely_unlink_worktree_dependencies(worktree_dir: Path) -> list[str]:
    """
    Safely unlinks all symlinks inside worktree_dir before rmtree.
    Prevents recursive traversal into target directories (e.g. root .venv or .local).
    """
    unlinked: list[str] = []
    if not worktree_dir.exists() or not worktree_dir.is_dir():
        return unlinked

    for item in list(worktree_dir.iterdir()):
        if item.is_symlink():
            try:
                item.unlink()
                unlinked.append(item.name)
            except Exception as e:
                print(f"  ⚠️ Could not unlink symlink {item.name}: {e}")
    return unlinked


def provision_isolated_worktree(
    root: Path,
    worktree_dir: Path,
    branch: str,
    base_branch: str = "main",
) -> Path:
    """
    Provisions a new git worktree with fail-closed semantics, relative dependency symlinks,
    and githooks configuration.
    """
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)

    # Check if branch already exists
    try:
        branches = run_git(
            ["branch", "--format=%(refname:short)"], cwd=root, check=True
        ).splitlines()
    except Exception:
        branches = []
    branch_exists = branch in branches

    if branch_exists:
        cmd = ["worktree", "add", str(worktree_dir), branch]
    else:
        cmd = ["worktree", "add", str(worktree_dir), "-b", branch, base_branch]

    try:
        run_git(cmd, cwd=root, check=True)
    except Exception as e:
        raise RuntimeError(
            f"Failed to provision git worktree for branch '{branch}' at '{worktree_dir}': {e}"
        ) from e

    # Symlink dependencies with relative targets
    link_worktree_dependencies(root, worktree_dir)

    # Configure githooks and engine performance in worktree
    for k, v in [
        ("core.hooksPath", ".githooks"),
        ("core.fsmonitor", "true"),
        ("core.untrackedCache", "true"),
        ("core.commitGraph", "true"),
    ]:
        with contextlib.suppress(Exception):
            run_git(["-C", str(worktree_dir), "config", k, v], cwd=root, check=False)

    return worktree_dir


def _auto_prune_and_enforce_cap_unlocked(branch: str, root: Path = ROOT) -> Optional[str]:
    """
    Checks active worktrees before provisioning. Auto-prunes safe superseded/merged worktrees,
    respecting a 30-minute minimum age to avoid TOCTOU races with newly created worktrees.
    Returns error string if cap (>30 active worktrees) is exceeded, else None.
    """
    try:
        from .merge_proof import BranchMergeState, classify_branch_merge_state

        raw = run_git(["worktree", "list", "--porcelain"], cwd=root)
        active_wts: list[dict[str, str]] = []
        cur: dict[str, str] = {}
        for line in raw.splitlines():
            if line.startswith("worktree "):
                if cur:
                    active_wts.append(cur)
                cur = {"path": line.split(" ", 1)[1].strip()}
            elif line.startswith("HEAD "):
                cur["head"] = line.split(" ", 1)[1].strip()
            elif line.startswith("branch refs/heads/"):
                cur["branch"] = line.split(" ", 1)[1].strip().replace("refs/heads/", "")
        if cur:
            active_wts.append(cur)

        main_head = run_git(["rev-parse", "main"], cwd=root).strip()
        now = time.time()
        active_count = 0

        for wt in active_wts:
            wt_path = Path(wt["path"])
            if wt_path.resolve() == root.resolve():
                continue
            wt_branch = wt.get("branch", "")
            if not wt_branch:
                continue

            # Check age of worktree directory
            try:
                mtime = wt_path.stat().st_mtime
                age_minutes = (now - mtime) / 60.0
            except Exception:
                age_minutes = 999.0

            head = wt.get("head") or run_git(["rev-parse", wt_branch], cwd=root, check=False).strip()
            state = classify_branch_merge_state(wt_branch, head, main_head, root=root)

            # Check if clean
            is_clean = _is_worktree_clean(wt_path)
            # Safe auto-prune conditions:
            # 1. State is GENUINELY_MERGED or SUPERSEDED_BY_MAIN, AND clean, AND age > 30 minutes
            if (state in (BranchMergeState.GENUINELY_MERGED, BranchMergeState.SUPERSEDED_BY_MAIN)) and is_clean and age_minutes >= 30.0:
                print(f"🧹 Auto-pruning superseded/merged worktree for '{wt_branch}' ({age_minutes:.1f}m old)...")
                _remove_worktree_files(wt_path, force=False, root=root)
                continue

            active_count += 1

        if active_count >= 30:
            return (
                f"Active worktree cap reached ({active_count} active workspaces, limit is 30). "
                "Commit and dehydrate or finish an existing worktree before starting a new one."
            )
    except Exception as e:
        logger.debug(f"Auto-prune advisory check skipped: {e}")
    return None


def cmd_dehydrate(args: argparse.Namespace) -> int:
    """Safely removes the .worktrees/<slug> folder while preserving the branch in git."""
    with worktree_provisioning_lock():
        branch = args.branch
        slug = slugify_branch(branch)
        print(f"  LOCAL PR GOVERNANCE: DEHYDRATE WORKTREE FOR {branch}")
        print(f"  Target Worktree Path: .worktrees/{slug}")

        worktree_dir = _find_worktree_dir(branch, slug, root=ROOT)
        if not worktree_dir.exists():
            print(f"ℹ️ Worktree directory does not exist: {worktree_dir} (already dehydrated)")
            return 0

        # Must be byte-clean to dehydrate without data loss
        if not _is_worktree_clean(worktree_dir):
            print(
                f"❌ Error: Worktree '{worktree_dir}' has uncommitted changes or valuable ignored files.\n"
                "   Dehydration is blocked to prevent data loss. Commit or stash all changes first."
            )
            return 1

        # Repatriate audio and sidecars safely before removing directory
        from .audio_repatriation import repatriate_worktree_audio_artifacts

        repat = repatriate_worktree_audio_artifacts(worktree_dir, ROOT)
        if not repat.success:
            print(f"❌ Error: Audio repatriation failed: {repat.error}")
            return 1

        print(f"Dehydrating worktree '{worktree_dir}' (preserving branch '{branch}')...")
        if not _remove_worktree_files(worktree_dir, force=False, root=ROOT):
            return 1

        print(f"✅ Worktree for '{branch}' successfully dehydrated. Branch remains active in PR queue.")
        return 0


def cmd_start(args: argparse.Namespace) -> int:
    """Creates a new conventional branch inside an isolated worktree."""
    with worktree_provisioning_lock():
        branch = args.branch
        slug = slugify_branch(branch)
        print(f"  LOCAL PR GOVERNANCE: START WORKTREE FOR {branch}")
        print(f"  Target Worktree Path: .worktrees/{slug}")

        if branch in ("main", "master") or "main_merge" in branch:
            print(f"❌ Error: Branch '{branch}' is strictly prohibited for isolated worktrees.")
            print("   Branch 'main' is reserved exclusively for the primary repository root.")
            return 1

        if not CONVENTIONAL_BRANCH_RE.match(branch):
            print(f"❌ Error: Branch name '{branch}' does not conform to conventional branch format.")
            print(
                "   Allowed patterns: feat/*, feature/*, fix/*, ingest/*, chore/*, docs/*, refactor/*, test/*, ci/*"
            )
            print("   Example: feat/my-feature or ingest/doc-billing-rate-card")
            return 1

        # Enforce active workspace cap
        cap_err = _auto_prune_and_enforce_cap_unlocked(branch, root=ROOT)
        if cap_err:
            print(f"❌ Error: {cap_err}")
            return 1

        worktree_dir = ROOT / ".worktrees" / slug
        if worktree_dir.exists():
            print(f"❌ Error: Worktree directory already exists at: {worktree_dir}")
            return 1

        print(f"Provisioning isolated worktree at '.worktrees/{slug}'...")
        try:
            provision_isolated_worktree(ROOT, worktree_dir, branch, "main")
        except Exception as e:
            print(f"❌ Failed to create worktree: {e}")
            return 1

        print("\n" + "=" * 80)
        print("✅ Isolated worktree created successfully!")
        print("   To begin development, run:")
        print(f"     cd .worktrees/{slug}")
        print("=" * 80)
        return 0


VALUABLE_IGNORED_EXTENSIONS = (".sqlite-wal", ".sqlite", ".db")
VALUABLE_IGNORED_PREFIXES = ("output/", "output\\")
BENIGN_IGNORED_SUBSTRINGS = (
    "__pycache__",
    ".pytest_cache",
    ".venv",
    ".local",
    ".git",
    ".DS_Store",
    ".pyc",
    ".sqlite-shm",
    "/proposals",
    "\\proposals",
    "/attempts",
    "\\attempts",
)


def parse_porcelain_v2_z(raw_data: str | bytes) -> list[dict[str, Any]]:
    """Parses `git status --porcelain=v2 -z` stream handling spaces, null bytes, and renames."""
    if isinstance(raw_data, bytes):
        raw_str = raw_data.decode("utf-8", errors="replace")
    else:
        raw_str = raw_data
    if not raw_str:
        return []

    tokens = raw_str.split("\0")
    entries: list[dict[str, Any]] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token:
            i += 1
            continue
        parts = token.split(" ")
        record_type = parts[0]
        if record_type == "1":  # Ordinary changed entry
            path = " ".join(parts[8:])
            entries.append({"type": "1", "xy": parts[1], "path": path})
            i += 1
        elif record_type == "2":  # Renamed/copied entry
            path = " ".join(parts[9:])
            orig_path = tokens[i + 1] if i + 1 < len(tokens) else ""
            entries.append({"type": "2", "xy": parts[1], "path": path, "orig_path": orig_path})
            i += 2
        elif record_type == "u":  # Unmerged entry
            path = " ".join(parts[10:])
            entries.append({"type": "u", "xy": parts[1], "path": path})
            i += 1
        elif record_type in ("?", "!"):  # Untracked or ignored entry
            path = token[2:]
            entries.append({"type": record_type, "path": path})
            i += 1
        else:
            i += 1
    return entries


def is_mid_rebase(wt_path: Path) -> bool:
    """Checks if worktree has an active rebase, merge, cherry-pick, or revert in progress."""
    if not wt_path.exists():
        return False
    try:
        git_dir_out = run_git(["rev-parse", "--git-dir"], cwd=wt_path, check=True).strip()
        git_dir = wt_path / git_dir_out if not Path(git_dir_out).is_absolute() else Path(git_dir_out)
        for indicator in ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD"):
            if (git_dir / indicator).exists():
                return True
    except Exception:
        return False
    return False


def find_valuable_ignored_files(wt_path: Path) -> list[str]:
    """
    Returns relative paths of non-empty valuable ignored files (*.sqlite-wal, output/*).
    Excludes benign runtime artifacts like __pycache__, .venv, .pytest_cache (Invariant I3).
    """
    if not wt_path.exists():
        return []
    try:
        proc = subprocess.run(
            [
                "git",
                "status",
                "--ignored=matching",
                "--porcelain=v2",
                "-z",
                "--",
                ".",
                ":(exclude).venv",
                ":(exclude).worktrees",
            ],
            cwd=wt_path,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            return []
        entries = parse_porcelain_v2_z(proc.stdout)
    except Exception:
        return []

    valuable: list[str] = []
    for entry in entries:
        if entry.get("type") != "!":
            continue
        rel_path = entry.get("path", "")
        if not rel_path:
            continue
        if any(b in rel_path for b in BENIGN_IGNORED_SUBSTRINGS):
            continue
        is_candidate = (
            rel_path.endswith(VALUABLE_IGNORED_EXTENSIONS)
            or rel_path.startswith(VALUABLE_IGNORED_PREFIXES)
        )
        if is_candidate:
            full_path = wt_path / rel_path
            try:
                if full_path.is_file() and full_path.stat().st_size > 0:
                    valuable.append(rel_path)
                elif full_path.is_dir():
                    has_valuable_content = any(
                        p.is_file() and p.stat().st_size > 0 and not any(b in p.as_posix() for b in BENIGN_IGNORED_SUBSTRINGS)
                        for p in full_path.rglob("*")
                    )
                    if has_valuable_content:
                        valuable.append(rel_path)
            except Exception:
                pass
    return valuable


_MAIN_ANCESTOR_CACHE: dict[str, bool] = {}
_BRANCH_ANCESTOR_CACHE: dict[tuple[str, str], bool] = {}
_STASH_ENTRIES_CACHE: list[tuple[str, str, str, str]] | None = None


def _is_base_in_main(base_commit: str, main_branch: str, root: Path) -> bool:
    if base_commit not in _MAIN_ANCESTOR_CACHE:
        proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", base_commit, main_branch],
            cwd=root,
            capture_output=True,
            check=False,
        )
        _MAIN_ANCESTOR_CACHE[base_commit] = (proc.returncode == 0)
    return _MAIN_ANCESTOR_CACHE[base_commit]


_BRANCH_UNIQUE_COMMITS_CACHE: Dict[Tuple[str, str, Path], Set[str]] = {}


def _get_branch_unique_commits(branch: str, main_branch: str, root: Path) -> Set[str]:
    key = (branch, main_branch, root)
    if key not in _BRANCH_UNIQUE_COMMITS_CACHE:
        try:
            proc = subprocess.run(
                ["git", "rev-list", f"{main_branch}..{branch}"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                _BRANCH_UNIQUE_COMMITS_CACHE[key] = set(proc.stdout.strip().splitlines())
            else:
                _BRANCH_UNIQUE_COMMITS_CACHE[key] = set()
        except Exception:
            _BRANCH_UNIQUE_COMMITS_CACHE[key] = set()
    return _BRANCH_UNIQUE_COMMITS_CACHE[key]


def _fetch_stash_entries(root: Path) -> list[tuple[str, str, str, str]]:
    try:
        proc = subprocess.run(
            ["git", "log", "-g", "--format=%gd%x00%H%x00%P%x00%gs", "refs/stash"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        entries = []
        if proc.returncode == 0 and proc.stdout.strip():
            for line in proc.stdout.strip().splitlines():
                parts = line.split("\x00")
                if len(parts) >= 4:
                    entries.append((parts[0], parts[1], parts[2], parts[3]))
        return entries
    except Exception:
        return []


def count_branch_stashes(
    branch: str,
    root: Path,
    main_branch: str = "main",
    cached_entries: Optional[list[tuple[str, str, str, str]]] = None,
) -> int:
    """
    Counts stash entries attributable to `branch` by ancestry and message (Invariant I8).
    - Stashes on `main` (e.g. 'WIP on main:') are never attributed to feature branches.
    - Suffix-named branches (feat/auth vs feat/auth-v2) do not false-match.
    - Detached-HEAD stashes on commits unique to `branch` are attributed.
    - Renamed branches whose base commit is in `branch` and not `main` match.
    """
    entries = cached_entries if cached_entries is not None else _fetch_stash_entries(root)
    stash_count = 0
    branch_token_pattern = re.compile(rf"\b(WIP on|On)\s+{re.escape(branch)}:")
    main_token_pattern = re.compile(rf"\b(WIP on|On)\s+{re.escape(main_branch)}:")
    unique_commits: Optional[Set[str]] = None

    for _gd, _commit_sha, parents_str, subject in entries:
        parent_shas = parents_str.split()
        if not parent_shas:
            continue
        base_commit = parent_shas[0]

        if branch == main_branch:
            if main_token_pattern.search(subject):
                stash_count += 1
            continue

        # Feature branch checks:
        # 1. Never attribute root main stashes to feature branches
        if main_token_pattern.search(subject):
            continue

        # 2. Exact branch name token match in message (prevents suffix collisions)
        if branch_token_pattern.search(subject):
            stash_count += 1
            continue

        # 3. Check ancestry: if base_commit is unique to branch (in branch but not in main)
        if unique_commits is None:
            unique_commits = _get_branch_unique_commits(branch, main_branch, root)

        if base_commit in unique_commits:
            stash_count += 1

    return stash_count


def list_orphan_worktree_directories(root: Path) -> list[Path]:
    """Finds directories in .worktrees/ that are NOT registered in git worktree list (Invariant I9)."""
    worktrees_dir = root / ".worktrees"
    if not worktrees_dir.exists() or not worktrees_dir.is_dir():
        return []
    try:
        wt_list = run_git(["worktree", "list", "--porcelain"], cwd=root, check=True)
    except Exception:
        return []
    registered_paths = set()
    for line in wt_list.splitlines():
        if line.startswith("worktree "):
            registered_paths.add(Path(line.split(" ", 1)[1].strip()).resolve())

    orphans: list[Path] = []
    for item in worktrees_dir.iterdir():
        if (
            item.is_dir()
            and item.name != ".provisioning.lock"
            and not item.name.startswith(".")
            and item.resolve() not in registered_paths
        ):
            orphans.append(item)
    return sorted(orphans)


def _is_worktree_clean(wt_path: Path) -> bool:
    """Return True only if the worktree has no modified, staged, untracked, valuable ignored files, and no active rebase."""
    if not wt_path.exists():
        return False
    if is_mid_rebase(wt_path):
        return False
    valuable_ignored = find_valuable_ignored_files(wt_path)
    if valuable_ignored:
        return False
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain=v2", "-z", "--untracked-files=all"],
            cwd=wt_path,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            return False
        entries = parse_porcelain_v2_z(proc.stdout)
        return len(entries) == 0
    except Exception:
        return False


def _find_worktree_dir(branch: str, slug: str, root: Path = ROOT) -> Path:
    """Finds worktree directory by checking registered git worktrees first to prevent collisions."""
    with contextlib.suppress(Exception):
        wt_list = run_git(["worktree", "list", "--porcelain"], cwd=root, check=True)
        for block in wt_list.split("\n\n"):
            lines = block.strip().splitlines()
            wt_path, wt_branch = None, None
            for line in lines:
                if line.startswith("worktree "):
                    wt_path = Path(line.split(" ", 1)[1].strip())
                elif line.startswith("branch "):
                    wt_branch = line.split(" ", 1)[1].strip().replace("refs/heads/", "")
            if wt_branch == branch and wt_path:
                return wt_path

    # Fallback to standard slug location
    return root / ".worktrees" / slug


def _is_branch_merged(branch: str, root: Path = ROOT) -> bool:
    try:
        from .merge_proof import BranchMergeState, classify_branch_merge_state

        branch_tip = run_git(["rev-parse", branch], cwd=root, check=True).strip()
        base_tip = run_git(["rev-parse", "main"], cwd=root, check=True).strip()
        merge_state = classify_branch_merge_state(branch, branch_tip, base_tip, root=root)
        return merge_state == BranchMergeState.GENUINELY_MERGED
    except Exception as e:
        print(f"⚠️ Error checking if branch is merged: {e}")
        return False


def _is_zero_commit_ancestor_branch(branch: str, root: Path = ROOT) -> bool:
    try:
        from .merge_proof import BranchMergeState, classify_branch_merge_state

        branch_tip = run_git(["rev-parse", branch], cwd=root, check=True).strip()
        base_tip = run_git(["rev-parse", "main"], cwd=root, check=True).strip()
        merge_state = classify_branch_merge_state(branch, branch_tip, base_tip, root=root)
        return merge_state == BranchMergeState.ZERO_COMMIT_ANCESTOR
    except Exception:
        return False


def _remove_worktree_files(worktree_dir: Path, force: bool, root: Path = ROOT) -> bool:
    from .audio_repatriation import repatriate_worktree_audio_artifacts
    from .worktree_daemon_reaper import reap_worktree_fsmonitor

    # 1. Terminate background processes and reap monitors first before touching files
    reap_worktree_fsmonitor(worktree_dir, root=root)
    kill_ghost_processes(worktree_dir, terminate_ghosts=True)

    # 2. Repatriate audio artifacts and sidecars safely
    repat = repatriate_worktree_audio_artifacts(worktree_dir, root)
    if not repat.success:
        print(
            f"❌ Audio repatriation failed for '{worktree_dir}': {repat.error}\n"
            "   Worktree left intact to prevent WAV data loss."
        )
        return False

    safely_unlink_worktree_dependencies(worktree_dir)
    preserve_cwd = False
    try:
        cur_cwd = Path.cwd().resolve()
        wt_res = worktree_dir.resolve()
        canonical_wts = (root / ".worktrees").resolve()
        if (cur_cwd == wt_res or wt_res in cur_cwd.parents) and wt_res.parent != canonical_wts:
            preserve_cwd = True
    except Exception:
        pass

    rm_args = ["worktree", "remove", str(worktree_dir)]
    if force:
        rm_args.append("--force")
    try:
        run_git(rm_args, cwd=root, check=True)
        if preserve_cwd:
            with contextlib.suppress(Exception):
                worktree_dir.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        # INVARIANT: Never fall back to shutil.rmtree on git failure (Invariant I10)
        print(
            f"❌ git worktree remove {'--force ' if force else ''}failed: {e}\n"
            "   Worktree left intact to protect data integrity."
        )
        return False


def cmd_finish(args: argparse.Namespace) -> int:
    """Removes a finished worktree after merge to main."""
    with worktree_provisioning_lock():
        branch = args.branch
        slug = slugify_branch(branch)
        force = getattr(args, "force", False)
        delete_branch = getattr(args, "delete_branch", True)

        print(f"  LOCAL PR GOVERNANCE: FINISH WORKTREE FOR {branch}")
        print(f"  Target Worktree Path: .worktrees/{slug}")

        worktree_dir = _find_worktree_dir(branch, slug, root=ROOT)
        if not worktree_dir.exists():
            print(f"ℹ️ Worktree directory does not exist: {worktree_dir} (already dehydrated)")
            if delete_branch:
                # If worktree directory is already gone, delete branch cleanly
                try:
                    recorded_sha = run_git(["rev-parse", f"refs/heads/{branch}"], cwd=ROOT, check=False).strip()
                    if recorded_sha:
                        run_git(["update-ref", "-d", f"refs/heads/{branch}", recorded_sha], cwd=ROOT, check=True)
                        print(f"✅ Branch '{branch}' deleted.")
                except Exception as e:
                    print(f"⚠️ Could not delete branch ref: {e}")
            return 0

        # Record branch tip for CAS check
        recorded_sha = ""
        with contextlib.suppress(Exception):
            recorded_sha = run_git(["rev-parse", f"refs/heads/{branch}"], cwd=ROOT, check=True).strip()

        confirm_token = getattr(args, "confirm_token", None)
        is_ingest = getattr(args, "ingest", False) or bool(confirm_token)
        if confirm_token:
            print(f"✅ Authorized worktree teardown via confirm-token: {confirm_token}")
        # Write immutable archive ref unconditionally before any removal to guarantee zero data loss
        if recorded_sha:
            archive_ref = f"refs/archive/{branch}/{recorded_sha}"
            try:
                run_git(["update-ref", archive_ref, recorded_sha, ""], cwd=ROOT, check=True)
                print(f"✅ Created immutable archive ref: {archive_ref}")
            except Exception as e:
                print(f"ℹ️ Archive ref {archive_ref} status: {e}")

        if not force and not is_ingest:
            is_merged = _is_branch_merged(branch, root=ROOT)
            is_clean = _is_worktree_clean(worktree_dir)
            is_clean_zero_commit = (
                is_clean
                and _is_zero_commit_ancestor_branch(branch, root=ROOT)
                and count_branch_stashes(branch, ROOT) == 0
            )
            if not is_merged and not is_clean_zero_commit:
                print(
                    f"❌ Error: Branch '{branch}' is not fully merged into main (or is a dirty zero-commit branch). Use --force, --ingest, or --confirm-token to remove anyway."
                )
                return 1
            if not is_clean:
                print(
                    f"❌ Error: Worktree '{worktree_dir}' has uncommitted changes or valuable ignored files.\n"
                    "   Automated teardown is blocked to prevent data loss.\n"
                    "   Commit or stash your changes, then re-run finish.\n"
                    "   To discard all uncommitted changes and force-remove, use --force or --ingest."
                )
                return 1
        else:
            # Force or Ingestion finish: protect authored code and deliverable files from destructive wipe
            if is_ingest and not force:
                dirty_status = run_git(["status", "--porcelain=v2", "-z", "--untracked-files=all"], cwd=worktree_dir, check=False)
                entries = parse_porcelain_v2_z(dirty_status)
                dirty_paths = [e.get("path") for e in entries if e.get("path")]
                partition = partition_worktree_dirty_files(dirty_paths)
                if partition.get("authored_code_files") or partition.get("deliverable_files"):
                    print(
                        f"❌ Error: Worktree '{worktree_dir}' contains uncommitted authored code or deliverable files: "
                        f"{partition.get('authored_code_files', []) + partition.get('deliverable_files', [])}.\n"
                        "   Ingestion teardown is blocked to prevent data loss. Commit changes or use --force."
                    )
                    return 1

        print(f"Removing worktree '{worktree_dir}'...")
        if not _remove_worktree_files(worktree_dir, force or is_ingest, root=ROOT):
            return 1

        if delete_branch and recorded_sha:
            print(f"Deleting branch '{branch}' (CAS verified)...")
            try:
                # Compare-and-swap deletion: only delete if branch still points to recorded_sha
                run_git(["update-ref", "-d", f"refs/heads/{branch}", recorded_sha], cwd=ROOT, check=True)
                print(f"✅ Branch '{branch}' deleted.")
            except Exception as e:
                print(f"⚠️ Compare-and-swap deletion failed (branch moved or locked): {e}")
                return 1

        print("✅ Worktree teardown complete.")
        return 0


REVIEW_ARTIFACT_IGNORE_PREFIXES = GENERATED_ARTIFACT_PATTERNS

EPHEMERAL_ATTEMPT_SUBSTRINGS = (
    "/attempts/",
    "\\attempts\\",
    "attempts/attempt_",
    ".ckpt.md",
    ".raw_asr.json",
    "copilot_attempts.jsonl",
    "judge_failure_diagnostics.json",
    "_16k_mono.wav",
)


def is_ephemeral_worktree_path(rel_path: str) -> bool:
    """Detects ephemeral attempt checkpoints and transient review artifacts. (SQLite WALs are excluded to prevent hiding active DB state)."""
    norm = rel_path.replace("\\", "/")
    if any(pat in norm for pat in EPHEMERAL_ATTEMPT_SUBSTRINGS):
        return True
    if any(norm.startswith(p) or f"/{p}" in norm for p in REVIEW_ARTIFACT_IGNORE_PREFIXES):
        return True
    return False


def partition_worktree_dirty_files(paths: list[str]) -> dict[str, list[str]]:
    """Partitions dirty paths into authored_code_files, deliverable_files, and ephemeral_attempt_files."""
    from .diff_semantics import is_code_file, is_deliverable_file, is_doc_file

    code_files: list[str] = []
    deliverables: list[str] = []
    ephemeral: list[str] = []
    other: list[str] = []

    for p in paths:
        if is_ephemeral_worktree_path(p):
            ephemeral.append(p)
        elif is_deliverable_file(p):
            deliverables.append(p)
        elif is_code_file(p) or is_doc_file(p):
            code_files.append(p)
        else:
            other.append(p)

    return {
        "authored_code_files": code_files,
        "deliverable_files": deliverables,
        "ephemeral_attempt_files": ephemeral,
        "other_files": other,
    }


def assert_safe_worktree_operation(worktree_dir: Path, target_paths: list[str]) -> tuple[bool, str]:
    """
    Guards against accidental sweeping wipes (e.g. git checkout HEAD -- .) when unstaged work exists.
    """
    if not worktree_dir.exists():
        return True, "Worktree does not exist"
    status = run_git(["status", "--porcelain"], cwd=worktree_dir, check=False)
    uncommitted = [line[3:].strip() for line in status.splitlines() if line.strip()]
    if not uncommitted:
        return True, "Worktree is clean"

    sweeping_targets = {".", "dual_pipeline", "tests", "tools", "*"}
    for t in target_paths:
        if t.rstrip("/") in sweeping_targets:
            return (
                False,
                f"Sweeping operation on '{t}' blocked: worktree has {len(uncommitted)} uncommitted files.",
            )
    return True, "Safe operation"


class WorktreeDirtyResult(tuple):
    """Backwards-compatible 2-tuple (dirty, summary) with attributes for file counts."""
    dirty: bool
    summary: str
    dirty_files_count: int
    authored_files_count: int
    authored_code_files: list[str]
    deliverable_files: list[str]
    ephemeral_attempt_files: list[str]

    def __new__(
        cls,
        dirty: bool,
        summary: str,
        dirty_files_count: int = 0,
        authored_files_count: int = 0,
        authored_code_files: list[str] | None = None,
        deliverable_files: list[str] | None = None,
        ephemeral_attempt_files: list[str] | None = None,
    ):
        obj = super().__new__(cls, (dirty, summary))
        obj.dirty = dirty
        obj.summary = summary
        obj.dirty_files_count = dirty_files_count
        obj.authored_files_count = authored_files_count
        obj.authored_code_files = authored_code_files or []
        obj.deliverable_files = deliverable_files or []
        obj.ephemeral_attempt_files = ephemeral_attempt_files or []
        return obj


def _inspect_worktree_dirty(wt_path: Path, ignore_review_artifacts: bool) -> WorktreeDirtyResult:
    """Audits whether a given worktree path has uncommitted modifications, valuable ignored files, or active rebase."""
    if not wt_path.exists():
        return WorktreeDirtyResult(False, "UNKNOWN (missing directory)", 0, 0)
    if is_mid_rebase(wt_path):
        return WorktreeDirtyResult(True, "UNKNOWN (mid-rebase in progress)", 0, 0)

    valuable_ignored = find_valuable_ignored_files(wt_path)

    entries = []
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain=v2", "-z", "--untracked-files=all"],
            cwd=wt_path,
            capture_output=True,
            check=False,
        )
        if proc.returncode == 0:
            entries = parse_porcelain_v2_z(proc.stdout)
        else:
            raw = run_git(["status", "--porcelain"], cwd=wt_path, check=False)
            if raw and raw.strip():
                for line in raw.splitlines():
                    if not line.strip() or len(line) < 3:
                        continue
                    xy = line[:2]
                    p = line[3:].strip()
                    entries.append({"type": "?" if "??" in xy else "1", "path": p, "xy": xy})
            else:
                return WorktreeDirtyResult(True, "status-check-failed", 0, 0)
    except Exception:
        raw = run_git(["status", "--porcelain"], cwd=wt_path, check=False)
        if raw and raw.strip():
            for line in raw.splitlines():
                if not line.strip() or len(line) < 3:
                    continue
                xy = line[:2]
                p = line[3:].strip()
                entries.append({"type": "?" if "??" in xy else "1", "path": p, "xy": xy})
        else:
            return WorktreeDirtyResult(True, "status-check-failed", 0, 0)

    all_paths = [e.get("path", "") for e in entries if e.get("path")] + list(valuable_ignored)
    partitioned = partition_worktree_dirty_files(all_paths)
    authored_code = partitioned["authored_code_files"]
    deliverables = partitioned["deliverable_files"]
    ephemeral = partitioned["ephemeral_attempt_files"]

    if ignore_review_artifacts:
        filtered = [
            e
            for e in entries
            if not is_ephemeral_worktree_path(e.get("path", ""))
        ]
        filtered_valuable = [
            v for v in valuable_ignored if not is_ephemeral_worktree_path(v)
        ]
    else:
        filtered = entries
        filtered_valuable = valuable_ignored

    authored_count = len(authored_code) + len(deliverables)
    all_dirty_count = len(filtered) + len(filtered_valuable)
    total_dirty_count = len(entries) + len(valuable_ignored)

    if all_dirty_count == 0:
        summary = "clean (review receipts ignored)" if len(entries) > 0 else "clean"
        return WorktreeDirtyResult(
            False,
            summary,
            total_dirty_count,
            0,
            authored_code_files=authored_code,
            deliverable_files=deliverables,
            ephemeral_attempt_files=ephemeral,
        )

    has_untracked = any(e.get("type") == "?" for e in filtered)
    has_modified = any(e.get("type") in ("1", "2", "u") for e in filtered)
    has_valuable_ignored = bool(filtered_valuable)

    parts: list[str] = []
    if has_modified:
        parts.append("modified")
    if has_untracked:
        parts.append("untracked")
    if has_valuable_ignored:
        parts.append(f"{len(filtered_valuable)} valuable ignored")
    summary = f"dirty ({all_dirty_count} files: {' + '.join(parts)})"
    return WorktreeDirtyResult(
        True,
        summary,
        total_dirty_count,
        authored_count,
        authored_code_files=authored_code,
        deliverable_files=deliverables,
        ephemeral_attempt_files=ephemeral,
    )


def _inspect_single_worktree_entry(
    wt_path: Path,
    branch_ref: str,
    head_sha: str,
    is_bare: bool,
    is_detached: bool,
    root: Path,
    ignore_review_artifacts: bool,
    cached_stash_entries: Optional[list[tuple[str, str, str, str]]] = None,
) -> dict[str, Any]:
    if not wt_path.exists():
        return {
            "path": str(wt_path),
            "branch": branch_ref or "unknown",
            "head": head_sha,
            "dirty": False,
            "summary": "UNKNOWN (missing directory)",
            "status": "UNKNOWN",
            "dirty_files_count": 0,
            "authored_files_count": 0,
            "stash_count": 0,
            "is_root": False,
        }

    if is_detached:
        with contextlib.suppress(Exception):
            git_dir_out = run_git(["rev-parse", "--git-dir"], cwd=wt_path, check=True)
            git_dir_path = wt_path / git_dir_out if not Path(git_dir_out).is_absolute() else Path(git_dir_out)
            head_name_file = git_dir_path / "rebase-merge" / "head-name"
            if head_name_file.exists():
                branch_ref = head_name_file.read_text().strip().replace("refs/heads/", "")

    dirty = False
    dirty_summary = "clean"
    dirty_files_count = 0
    authored_files_count = 0
    stash_count = 0
    mid_rebase = is_mid_rebase(wt_path)

    authored_code_files: list[str] = []
    deliverable_files: list[str] = []
    ephemeral_files: list[str] = []

    if not is_bare:
        dirty_res = _inspect_worktree_dirty(wt_path, ignore_review_artifacts)
        dirty = dirty_res.dirty
        dirty_summary = dirty_res.summary
        dirty_files_count = dirty_res.dirty_files_count
        authored_files_count = dirty_res.authored_files_count
        authored_code_files = dirty_res.authored_code_files
        deliverable_files = dirty_res.deliverable_files
        ephemeral_files = dirty_res.ephemeral_attempt_files
        stash_count = count_branch_stashes(branch_ref, root, cached_entries=cached_stash_entries)

    return {
        "path": str(wt_path),
        "branch": branch_ref
        or ("detached" if is_detached else ("bare" if is_bare else "unknown")),
        "head": head_sha,
        "dirty": dirty,
        "summary": dirty_summary,
        "status": "UNKNOWN" if mid_rebase else ("dirty" if dirty else "clean"),
        "mid_rebase": mid_rebase,
        "dirty_files_count": dirty_files_count,
        "authored_files_count": authored_files_count,
        "authored_code_files_count": len(authored_code_files),
        "deliverable_files_count": len(deliverable_files),
        "ephemeral_attempt_files_count": len(ephemeral_files),
        "has_uncommitted_deliverables": bool(deliverable_files),
        "has_stale_code_edits": bool(authored_code_files),
        "stash_count": stash_count,
        "is_root": str(wt_path.resolve()) == str(root.resolve()),
    }


def list_active_worktrees(
    root: Path, ignore_review_artifacts: bool = False, include_orphans: bool = False
) -> list[dict[str, Any]]:
    """
    Discovers all registered git worktrees and audits their branch and dirty state.
    Optionally ignores benign PR review and audit receipts generated during evaluation.
    """
    entries: list[dict[str, Any]] = []
    try:
        wt_list = run_git(["worktree", "list", "--porcelain"], cwd=root, check=True)
    except Exception:
        return entries

    from concurrent.futures import ThreadPoolExecutor

    worktree_items: list[tuple[Path, str, str, bool, bool]] = []
    for block in wt_list.split("\n\n"):
        if not block.strip():
            continue
        lines = block.strip().splitlines()
        wt_path: Path | None = None
        branch_ref = ""
        head_sha = ""
        is_bare = False
        is_detached = False

        for line in lines:
            if line.startswith("worktree "):
                wt_path = Path(line.split(" ", 1)[1].strip())
            elif line.startswith("HEAD "):
                head_sha = line.split(" ", 1)[1].strip()[:8]
            elif line.startswith("branch "):
                branch_ref = line.split(" ", 1)[1].strip().replace("refs/heads/", "")
            elif line.strip() == "bare":
                is_bare = True
            elif line.strip() == "detached":
                is_detached = True

        if wt_path:
            worktree_items.append((wt_path, branch_ref, head_sha, is_bare, is_detached))

    stash_entries = _fetch_stash_entries(root)

    def _worker(item: tuple[Path, str, str, bool, bool]) -> dict[str, Any]:
        p, b, h, bare, detached = item
        return _inspect_single_worktree_entry(
            p, b, h, bare, detached, root, ignore_review_artifacts, cached_stash_entries=stash_entries
        )

    with ThreadPoolExecutor(max_workers=min(8, len(worktree_items) or 1)) as ex:
        entries = list(ex.map(_worker, worktree_items))

    if include_orphans:
        for od in list_orphan_worktree_directories(root):
            entries.append(
                {
                    "path": str(od),
                    "branch": "ORPHAN",
                    "head": "",
                    "dirty": False,
                    "summary": "ORPHAN (unregistered directory)",
                    "status": "ORPHAN",
                    "dirty_files_count": 0,
                    "authored_files_count": 0,
                    "stash_count": 0,
                    "is_root": False,
                    "is_orphan": True,
                }
            )

    return entries


_BRANCH_WORKTREE_CACHE: Dict[Tuple[str, str, bool], Optional[dict[str, Any]]] = {}


def clear_branch_worktree_cache() -> None:
    """Clears in-process memoized branch-to-worktree mappings."""
    _BRANCH_WORKTREE_CACHE.clear()


def resolve_branch_worktree(
    root: Path, branch: str, ignore_review_artifacts: bool = True
) -> Optional[dict[str, Any]]:
    """O(1) direct worktree resolver targeting .worktrees/<slug> without full registry scan."""
    cache_key = (str(root.resolve()), branch, ignore_review_artifacts)
    if cache_key in _BRANCH_WORKTREE_CACHE:
        return _BRANCH_WORKTREE_CACHE[cache_key]

    slug = slugify_branch(branch)
    candidate_path = root / ".worktrees" / slug

    # If the worktree exists at canonical slug path, verify branch directly
    if candidate_path.is_dir() and (candidate_path / ".git").is_file():
        try:
            head_branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=candidate_path, check=True).strip()
            if head_branch == branch:
                head_sha = run_git(["rev-parse", "HEAD"], cwd=candidate_path, check=True).strip()[:8]
                entry = _inspect_single_worktree_entry(
                    candidate_path,
                    branch,
                    head_sha,
                    is_bare=False,
                    is_detached=False,
                    root=root,
                    ignore_review_artifacts=ignore_review_artifacts,
                )
                _BRANCH_WORKTREE_CACHE[cache_key] = entry
                return entry
        except Exception:
            pass

    # Root repo check
    try:
        root_branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root, check=True).strip()
        if root_branch == branch:
            root_sha = run_git(["rev-parse", "HEAD"], cwd=root, check=True).strip()[:8]
            entry = _inspect_single_worktree_entry(
                root,
                branch,
                root_sha,
                is_bare=False,
                is_detached=False,
                root=root,
                ignore_review_artifacts=ignore_review_artifacts,
            )
            _BRANCH_WORKTREE_CACHE[cache_key] = entry
            return entry
    except Exception:
        pass

    # Fallback to full worktree list if directory slug didn't match canonical pattern
    for wt in list_active_worktrees(root, ignore_review_artifacts=ignore_review_artifacts):
        if wt.get("branch") == branch:
            _BRANCH_WORKTREE_CACHE[cache_key] = wt
            return wt

    _BRANCH_WORKTREE_CACHE[cache_key] = None
    return None


def cmd_worktree_list(args: argparse.Namespace) -> int:
    """Lists all active worktrees and prints their live branch and dirty state."""
    worktrees = list_active_worktrees(ROOT, ignore_review_artifacts=True, include_orphans=True)
    print("=" * 80)
    print("  ACTIVE GIT WORKTREES & DIRTY STATE AUDIT")
    print("=" * 80)
    for wt in worktrees:
        tag = "[ROOT]" if wt.get("is_root") else ("[ORPHAN]" if wt.get("is_orphan") else "[WORKTREE]")
        status_icon = "⚠️" if wt["dirty"] or wt.get("mid_rebase") or wt.get("is_orphan") else "✅"
        print(f"{status_icon} {tag:<10} Branch: {wt['branch']:<52} Head: {wt['head']}")
        print(f"   Path:   {wt['path']}")
        print(f"   Status: {wt['summary']}")
        print()
    print("=" * 80)
    return 0
