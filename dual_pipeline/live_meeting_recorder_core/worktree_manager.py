"""Worktree Branch Manager for Live Transcription (Master Invariant 14)."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, List, Optional, Tuple, Union

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.worktree_manager")


@contextlib.contextmanager
def git_worktree_provision_lock(repo_root: Path, timeout_seconds: float = 120.0):
    """Advisory file lock around git worktree add/remove to prevent index.lock collisions."""
    lock_dir = (repo_root / ".git") if (repo_root / ".git").is_dir() else (repo_root / ".session_locks")
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "nee_worktree_provision.flock"

    fd = open(lock_path, "a+")  # noqa: SIM115
    start_t, acquired, backoff, last_log = time.monotonic(), False, 0.02, time.monotonic()
    while time.monotonic() - start_t < timeout_seconds:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd.seek(0)
            fd.truncate()
            fd.write(f"{os.getpid()}\n")
            fd.flush()
            acquired = True
            break
        except (BlockingIOError, OSError):
            now = time.monotonic()
            if now - last_log >= 10.0:
                logger.warning("Waiting for worktree lock at %s (%.1fs elapsed)", lock_path, now - start_t)
                last_log = now
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 0.25)

    if not acquired:
        fd.close()
        raise TimeoutError(f"Could not acquire git worktree provision lock within {timeout_seconds}s at {lock_path}")
    try:
        yield lock_path
    finally:
        with contextlib.suppress(Exception):
            fcntl.flock(fd, fcntl.LOCK_UN)
            fd.close()


def is_authentic_git_worktree(path: Path) -> bool:
    """Verifies that path is an authentic Git worktree or repo root with its own top-level gitdir."""
    try:
        p = path.resolve()
        if not p.is_dir() or not ((p / ".git").is_file() or (p / ".git").is_dir()):
            return False
        toplevel = run_git(["rev-parse", "--show-toplevel"], cwd=p)
        return bool(toplevel and Path(toplevel).resolve() == p)
    except Exception:
        return False


def _is_active_capture_dir(path: Path) -> bool:
    """Checks whether a directory contains markers of an active recording session."""
    if not path.exists():
        return False
    if (path / ".active_session").exists():
        return True
    with contextlib.suppress(Exception):
        for p in path.rglob("*"):
            if p.name == ".active_session" or p.suffix in (".flock", ".lock"):
                return True
    return False


def _backup_unmanaged_dir(worktree_dir: Path) -> Optional[Path]:
    """Moves existing unmanaged directory contents to temporary backup, protecting active capture directories."""
    if not (worktree_dir.exists() and not (worktree_dir / ".git").exists()):
        return None
    if _is_active_capture_dir(worktree_dir):
        logger.warning("Directory %s contains active capture files/locks; protecting from unmanaged backup purge.", worktree_dir)
        return None
    import tempfile
    unmanaged_backup = Path(tempfile.mkdtemp(prefix="wt_unmanaged_bak_"))
    for item in list(worktree_dir.iterdir()):
        shutil.move(str(item), str(unmanaged_backup / item.name))
    shutil.rmtree(worktree_dir, ignore_errors=True)
    return unmanaged_backup


def _restore_unmanaged_backup(unmanaged_backup: Optional[Path], worktree_dir: Path) -> None:
    """Restores pre-existing unmanaged directory contents into worktree."""
    if not unmanaged_backup or not unmanaged_backup.exists():
        return
    worktree_dir.mkdir(parents=True, exist_ok=True)
    for item in list(unmanaged_backup.iterdir()):
        dest = worktree_dir / item.name
        if dest.exists() and dest.is_dir() and item.is_dir():
            for sub in item.rglob("*"):
                if sub.is_file():
                    rel = sub.relative_to(item)
                    (dest / rel.parent).mkdir(parents=True, exist_ok=True)
                    shutil.copy2(sub, dest / rel)
        elif not dest.exists():
            shutil.move(str(item), str(dest))
    shutil.rmtree(unmanaged_backup, ignore_errors=True)


def _run_worktree_add(root: Path, worktree_dir: Path, branch_name: str) -> None:
    """Executes git worktree add command with appropriate branch flag."""
    existing_branches = run_git(["branch", "--list", branch_name], cwd=root)
    cmd = ["worktree", "add", str(worktree_dir), branch_name] if branch_name in existing_branches else ["worktree", "add", str(worktree_dir), "-b", branch_name, "main"]
    run_git(cmd, cwd=root, check=True)


def _provision_git_worktree(root: Path, worktree_dir: Path, branch_name: str) -> None:
    """Creates git worktree for branch and configures dependencies with fail-closed semantics."""
    with git_worktree_provision_lock(root):
        if is_authentic_git_worktree(worktree_dir):
            return

        unmanaged_backup = _backup_unmanaged_dir(worktree_dir)
        try:
            _run_worktree_add(root, worktree_dir, branch_name)
        except Exception as e:
            _restore_unmanaged_backup(unmanaged_backup, worktree_dir)
            raise RuntimeError(f"Failed to provision git worktree for {branch_name} at {worktree_dir}: {e}") from e

        _restore_unmanaged_backup(unmanaged_backup, worktree_dir)
        _link_dependencies_and_hooks(root, worktree_dir)


def slugify_for_branch(text: str) -> str:
    """Converts a meeting or document title into a valid conventional branch slug component."""
    from dual_pipeline.canonical_pipeline.worktree_naming import slugify_branch_name

    return slugify_branch_name(text, max_len=40)


def run_git(cmd: list[str], cwd: Optional[Path] = None, check: bool = True, timeout: float = 120.0) -> str:
    """Executes git command and returns stripped stdout with heartbeat logging."""
    logger.debug("Executing git %s (cwd=%s, timeout=%.1fs)", " ".join(cmd), cwd, timeout)
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX")}
    res = subprocess.run(
        ["git"] + cmd, cwd=str(cwd) if cwd else None, env=env, capture_output=True, text=True, check=check, timeout=timeout
    )
    return res.stdout.strip()


def is_inside_isolated_worktree(path: Path) -> bool:
    """Checks whether the given path is located inside an isolated .worktrees container."""
    try:
        resolved = str(path.resolve())
        return "/.worktrees/" in resolved or resolved.endswith("/.worktrees")
    except Exception:
        return False


def get_current_git_branch(repo_root: Path) -> str:
    """Returns the currently active branch name for the exact repository/worktree root, preventing parent directory traversal."""
    try:
        p = repo_root.resolve()
        toplevel_str = run_git(["rev-parse", "--show-toplevel"], cwd=p)
        if not toplevel_str or Path(toplevel_str).resolve() != p:
            # Traversed up into a parent git repository! Fail closed.
            return ""
        return run_git(["branch", "--show-current"], cwd=p)
    except Exception:
        return ""


def _link_dependencies_and_hooks(root: Path, worktree_dir: Path) -> None:
    """Symlinks virtualenv, configs, and sets githooks using unified governance logic."""
    try:
        from tools.pr_governance_core.worktree import link_worktree_dependencies
        link_worktree_dependencies(root, worktree_dir)
    except Exception:
        for link_name in (".venv", ".local", "env_di", ".vscode", ".agents"):
            src, dst = root / link_name, worktree_dir / link_name
            if src.exists() and not dst.exists():
                with contextlib.suppress(Exception):
                    os.symlink(os.path.relpath(src, worktree_dir), dst)
                if link_name in (".venv", ".local") and not dst.exists():
                    raise RuntimeError(f"Fail-Fast: Critical symlink {link_name} failed to create in worktree.") from None
    try:
        run_git(["-C", str(worktree_dir), "config", "core.hooksPath", ".githooks"], cwd=root, check=True)
    except Exception as e:
        raise RuntimeError(f"Fail-Fast: Failed to configure githooks in worktree: {e}") from e


def ensure_ingest_worktree(
    modality: Any = None, project_name: str = "", title: str = "", date_dirname: str = "",
    clean_slug: Optional[str] = None, repo_root: Optional[Path] = None, lazy: bool = False, **kwargs: Any,
) -> Tuple[Path, str]:
    """Guarantees document or meeting ingestion executes in an isolated Git worktree."""
    from dual_pipeline.canonical_pipeline.worktree_naming import derive_worktree_identity

    root = _resolve_primary_repo_root((repo_root or Path.cwd()).resolve())
    effective_slug = clean_slug or kwargs.get("clean_slug")
    raw_title = title or kwargs.get("meeting_name") or kwargs.get("meeting") or effective_slug or "session"
    caller_root = (repo_root or Path.cwd()).resolve()
    if is_inside_isolated_worktree(caller_root):
        curr_branch = get_current_git_branch(caller_root)
        if curr_branch and curr_branch.startswith(("audio/", "feat/live-", "live/", "transcript/", "ingest/")):
            return caller_root, curr_branch

    worktree_dir, branch_name = derive_worktree_identity(
        modality, raw_title, project=project_name, date=date_dirname, repo_root=root, clean_slug=effective_slug
    )
    (root / ".worktrees").mkdir(parents=True, exist_ok=True)
    if not lazy:
        _provision_git_worktree(root, worktree_dir, branch_name)
    return worktree_dir, branch_name


def ensure_live_transcription_worktree(
    project_name: str = "", meeting_name: str = "", date_dirname: str = "",
    clean_slug: Optional[str] = None, repo_root: Optional[Path] = None,
    lazy: bool = False, **kwargs: Any,
) -> Tuple[Path, str]:
    """Guarantees that live transcription executes in an isolated Git worktree branch."""
    return ensure_ingest_worktree(
        modality="AUDIO", project_name=project_name, title=meeting_name,
        date_dirname=date_dirname, clean_slug=clean_slug, repo_root=repo_root,
        lazy=lazy, **kwargs,
    )


def provision_live_transcription_worktree(
    worktree_dir: Path,
    branch_name: str,
    repo_root: Optional[Path] = None,
) -> Tuple[Path, str]:
    """Provisions deferred git worktree on disk."""
    caller_root = (repo_root or Path.cwd()).resolve()
    if is_inside_isolated_worktree(caller_root):
        curr_branch = get_current_git_branch(caller_root)
        if curr_branch and curr_branch.startswith(("audio/", "feat/live-", "live/", "transcript/", "ingest/")):
            return caller_root, branch_name
    root = _resolve_primary_repo_root(caller_root)
    (root / ".worktrees").mkdir(parents=True, exist_ok=True)
    _provision_git_worktree(root, worktree_dir, branch_name)
    return worktree_dir, branch_name


def _resolve_primary_repo_root(path: Path) -> Path:
    """Resolves primary repo root even if current path is inside an isolated worktree."""
    try:
        common_dir = run_git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=path)
        if common_dir:
            c_path = Path(common_dir).resolve()
            if c_path.name == ".git":
                return c_path.parent
            if c_path.parent.name == "worktrees" and c_path.parent.parent.name == ".git":
                return c_path.parent.parent.parent
    except Exception:
        pass
    p = path.resolve()
    for parent in (p, *p.parents):
        if (parent / ".git").is_dir():
            return parent
    return p


def _worktree_has_audio_or_commits(worktree_dir: Path, branch_name: Optional[str], root: Path) -> bool:
    """Checks if worktree contains non-empty audio or commits beyond main."""
    if not worktree_dir.exists():
        return False
    for p in worktree_dir.rglob("*"):
        if p.is_file() and p.suffix in (".wav", ".mp3", ".m4a") and p.stat().st_size > 0:
            return True
    if branch_name:
        with contextlib.suppress(Exception):
            commits = run_git(["log", f"main..{branch_name}", "--oneline"], cwd=root)
            if commits.strip():
                return True
    return False


def teardown_empty_live_worktree(
    worktree_dir: Optional[Path], branch_name: Optional[str], repo_root: Optional[Path] = None,
) -> bool:
    """Tears down an unneeded or empty live worktree and deletes its branch."""
    if not worktree_dir or not branch_name:
        return False
    valid_branch = branch_name.startswith(("feat/live-", "audio/", "live/", "transcript/", "ingest/meeting-"))
    valid_dir = worktree_dir.name.startswith(("live_", "audio_", "transcript_", "ingest_"))
    if not (valid_branch and valid_dir):
        return False
    worktree_dir, base_root = worktree_dir.resolve(), (repo_root or Path.cwd()).resolve()
    primary_root = _resolve_primary_repo_root(base_root)
    if worktree_dir in (primary_root, base_root) or _worktree_has_audio_or_commits(worktree_dir, branch_name, primary_root):
        return False

    success = True
    with git_worktree_provision_lock(primary_root):
        if worktree_dir.exists():
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

        try:
            run_git(["branch", "-D", branch_name], cwd=primary_root, check=False)
        except Exception:
            success = False
    return success


from .ingest_worktree_cleanup import teardown_ingest_worktree  # noqa: E402



def is_auto_output_dir(output_dir: Optional[Union[str, Path]]) -> bool:
    """Returns True if output_dir is None, empty, or 'auto'."""
    if output_dir is None:
        return True
    s = str(output_dir).strip().lower()
    return s in ("", "auto")


def resolve_recorder_paths(
    project_name: str, date_dirname: str, clean_slug: str,
    output_dir: Optional[Union[str, Path]], repo_root: Path,
    enforce_worktree: bool = True, meeting_name: str = "", lazy: bool = False,
) -> Tuple[Path, Path, Path, Optional[Path], Optional[str]]:
    """
    Resolves output_dir, cache_dir, chunks_dir, worktree_dir, worktree_branch.
    Guarantees worktree isolation when output_dir is not explicitly specified or is 'auto'.
    """
    worktree_dir: Optional[Path] = None
    worktree_branch: Optional[str] = None
    base_root = repo_root

    is_auto = is_auto_output_dir(output_dir)
    if enforce_worktree and is_auto:
        wt_dir, wt_branch = ensure_live_transcription_worktree(
            project_name=project_name, meeting_name=meeting_name or clean_slug,
            date_dirname=date_dirname, clean_slug=clean_slug, repo_root=repo_root, lazy=lazy,
        )
        worktree_dir, worktree_branch, base_root = wt_dir, wt_branch, wt_dir
    if not is_auto and output_dir is not None:
        p_out = Path(output_dir)
        resolved_output = (p_out / clean_slug) if p_out.name == date_dirname else p_out
    else:
        resolved_output = base_root / "output" / project_name / date_dirname / clean_slug
    try:
        in_repo = resolved_output.resolve().is_relative_to((base_root / "output").resolve())
    except Exception:
        in_repo = False
    resolved_cache = (base_root / "output" / "transcript_cache" / project_name / date_dirname) if in_repo else resolved_output
    resolved_chunks = resolved_cache / f".chunks_{clean_slug}"
    return resolved_output, resolved_cache, resolved_chunks, worktree_dir, worktree_branch

def resolve_splicing_worktree_output_dir(
    project_name: str, date_dirname: str, clean_slug: str,
    output_dir: Optional[Union[str, Path]], repo_root: Path,
    enforce_worktree: bool = True, meeting_name: str = "",
) -> Tuple[Path, Optional[Path], Optional[str]]:
    """Resolves governed output directory for Stage U0 ingestion, splicing, and synthesis."""
    resolved_output, _, _, worktree_dir, worktree_branch = resolve_recorder_paths(
        project_name=project_name, date_dirname=date_dirname, clean_slug=clean_slug,
        output_dir=output_dir if not is_auto_output_dir(output_dir) else None,
        repo_root=repo_root, enforce_worktree=enforce_worktree, meeting_name=meeting_name,
    )
    return resolved_output, worktree_dir, worktree_branch


def _is_dest_valid(f: Path, dest: Path) -> bool:
    try:
        return dest.exists() and dest.stat().st_size == f.stat().st_size and (
            hashlib.sha256(dest.read_bytes()).hexdigest() == hashlib.sha256(f.read_bytes()).hexdigest()
        )
    except Exception:
        return False


def _copy_single_attempt(f: Path, target_dir: Path, dest: Path) -> Tuple[bool, str]:
    token = f"{os.getpid()}_{threading.get_ident()}_{uuid.uuid4().hex[:6]}"
    tmp = target_dir / f".tmp_{f.name}_{token}"
    try:
        shutil.copy2(f, tmp)
        src_sha, dest_sha = hashlib.sha256(f.read_bytes()).hexdigest(), hashlib.sha256(tmp.read_bytes()).hexdigest()
        if src_sha != dest_sha:
            tmp.unlink(missing_ok=True)
            return False, f"Integrity check failed: SHA256 mismatch copying {f.name} to {target_dir} ({src_sha} != {dest_sha})"
        tmp.replace(dest)
        return True, ""
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return False, str(e)


def _copy_chunk_if_missing(f: Path, target_dir: Path, is_final: bool = False) -> bool:
    if not f.name.startswith("chunk_"):
        return True
    dest = target_dir / f.name
    if dest.exists() and dest.stat().st_size == f.stat().st_size:
        return True
    max_attempts = 3 if is_final else 1
    last_err = ""
    for _ in range(max_attempts):
        if _is_dest_valid(f, dest):
            return True
        ok, err = _copy_single_attempt(f, target_dir, dest)
        if ok:
            return True
        last_err = err
        time.sleep(0.04)
    if not is_final:
        logger.warning("Non-fatal deferral copying audio chunk %s to %s: %s", f.name, target_dir, last_err)
        return False
    logger.error("Failed to copy chunk %s to %s: %s", f.name, target_dir, last_err)
    if "Integrity check failed: SHA256 mismatch" in last_err:
        raise RuntimeError(last_err)
    raise RuntimeError(f"Fail-Fast: Failed to copy audio chunk {f.name} to {target_dir}: {last_err}")


def _copy_metadata_if_different(src: Path, dest_dir: Path) -> None:
    if src.exists() and src.resolve() != (dest_dir / src.name).resolve():
        try:
            shutil.copy2(src, dest_dir / src.name)
        except Exception as e:
            logger.error("Failed to copy metadata %s to %s: %s", src.name, dest_dir, e)
            raise RuntimeError(f"Fail-Fast: Failed to copy metadata {src.name} to {dest_dir}: {e}") from e


def _migrate_chunks(chunks_dir: Path, target_chunks_dir: Path, is_final: bool) -> None:
    if not (chunks_dir.exists() and chunks_dir.resolve() != target_chunks_dir.resolve()):
        return
    target_chunks_dir.mkdir(parents=True, exist_ok=True)
    chunk_files = sorted(chunks_dir.glob("chunk_*.wav"))
    chunks_to_copy = chunk_files if is_final else chunk_files[:-1]
    for f in chunks_to_copy:
        _copy_chunk_if_missing(f, target_chunks_dir, is_final=is_final)
        if is_final and f.exists():
            with contextlib.suppress(Exception):
                f.unlink()


def _migrate_metadata(metadata_pairs: List[Tuple[Path, Path]], is_final: bool) -> None:
    for src, dest_dir in metadata_pairs:
        if not src.exists():
            continue
        _copy_metadata_if_different(src, dest_dir)
        if is_final and src.exists() and src.resolve() != (dest_dir / src.name).resolve():
            with contextlib.suppress(Exception):
                src.unlink()


def migrate_staged_files(
    chunks_dir: Path, target_chunks_dir: Path, metadata_pairs: List[Tuple[Path, Path]], is_final: bool = False
) -> None:
    """Migrates pre-allocated local chunks and metadata into the active worktree."""
    _migrate_chunks(chunks_dir, target_chunks_dir, is_final)
    _migrate_metadata(metadata_pairs, is_final)


from .worktree_repointer import repoint_recorder_paths
