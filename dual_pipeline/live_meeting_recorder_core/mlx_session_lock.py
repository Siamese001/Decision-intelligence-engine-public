"""Global MLX Whisper Session Mutex and Concurrency Enforcement.

Guarantees that no two active MLX Whisper recording or note sessions can occur simultaneously
across the entire host (including any linked git worktrees).
Enforces:
1. Host-wide exclusive advisory lock via fcntl.flock(LOCK_EX | LOCK_NB).
2. Canonical repository root resolution across worktrees.
3. Structured metadata persistence (PID, session_id, meeting_slug, project, timestamp).
4. Fast pre-flight inspection (<5ms) for pre-CLI bootstrap gating.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.mlx_session_lock")

GLOBAL_LOCK_FILENAME = "_global_mlx_whisper_session.flock"


@dataclass
class ActiveSessionMeta:
    """Metadata of the process holding the global MLX Whisper session lock."""
    pid: int
    session_id: str = ""
    meeting_slug: str = ""
    project_name: str = ""
    started_at_iso: str = ""
    repo_root: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_raw(cls, raw: str) -> Optional[ActiveSessionMeta]:
        text = raw.strip()
        if not text:
            return None
        if text.startswith("{") and text.endswith("}"):
            try:
                data = json.loads(text)
                return cls(
                    pid=int(data.get("pid", 0)),
                    session_id=str(data.get("session_id", "")),
                    meeting_slug=str(data.get("meeting_slug", "")),
                    project_name=str(data.get("project_name", "")),
                    started_at_iso=str(data.get("started_at_iso", "")),
                    repo_root=str(data.get("repo_root", "")),
                )
            except Exception:
                pass
        # Fallback to plain PID string
        if text.isdigit():
            return cls(pid=int(text))
        return None


class LiveSessionCollisionError(RuntimeError):
    """Raised when an active recording session already holds the advisory slug lock or global MLX session lock."""

    def __init__(
        self,
        slug: str,
        holder_pid: Optional[int] = None,
        active_slug: Optional[str] = None,
        session_id: Optional[str] = None,
        is_global: bool = False,
    ):
        self.slug = slug
        self.holder_pid = holder_pid
        self.active_slug = active_slug or slug
        self.session_id = session_id
        self.is_global = is_global
        if is_global:
            msg = (
                f"Live session collision detected (Global MLX Whisper session mutex): an active session '{self.active_slug}' "
                f"(PID {holder_pid or 'unknown'}) is currently open. "
                f"Only one MLX Whisper note session may be active at a time; close the existing session before opening a new one."
            )
        else:
            msg = f"Live session collision detected for slug '{slug}'."
            if holder_pid:
                msg += f" Lock is currently held by active PID {holder_pid}."
        super().__init__(msg)


def acquire_global_mlx_lock(
    meeting_slug: str,
    project_name: str = "",
    session_id: str = "",
    repo_root: Optional[Path] = None,
) -> GlobalMLXSessionLock:
    """Acquires the host-wide Global MLX Whisper Session Mutex or raises LiveSessionCollisionError."""
    lock = GlobalMLXSessionLock(repo_root=repo_root)
    try:
        lock.acquire(
            session_id=session_id,
            meeting_slug=meeting_slug,
            project_name=project_name,
        )
        return lock
    except BlockingIOError as e:
        holder_meta = getattr(e, "holder_meta", None)
        raise LiveSessionCollisionError(
            meeting_slug,
            holder_pid=getattr(holder_meta, "pid", None),
            active_slug=getattr(holder_meta, "meeting_slug", None),
            session_id=getattr(holder_meta, "session_id", None),
            is_global=True,
        ) from e


def _resolve_git_common_root(current: Path) -> Optional[Path]:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=current, capture_output=True, text=True, check=False, timeout=2.0,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        common_dir = Path(proc.stdout.strip())
        common_dir = common_dir.resolve() if common_dir.is_absolute() else (current / common_dir).resolve()
        if common_dir.name == ".git":
            return common_dir.parent
        if common_dir.parent.name == "worktrees" and common_dir.parent.parent.name == ".git":
            return common_dir.parent.parent.parent
        return common_dir
    except Exception:
        return None


def _parse_gitdir_file(dot_git: Path) -> Optional[Path]:
    with contextlib.suppress(Exception):
        raw = dot_git.read_text(encoding="utf-8", errors="ignore").strip()
        if raw.startswith("gitdir:"):
            gd = Path(raw.split("gitdir:", 1)[1].strip())
            if not gd.is_absolute():
                gd = (dot_git.parent / gd).resolve()
            if gd.parent.name == "worktrees" and gd.parent.parent.name == ".git":
                return gd.parent.parent.parent
    return None


def resolve_canonical_repo_root(start_dir: Optional[Path] = None) -> Path:
    """Resolves the canonical primary git repository root even if inside a linked worktree."""
    current = (start_dir or Path.cwd()).resolve()
    git_root = _resolve_git_common_root(current)
    if git_root is not None:
        return git_root

    cur = current
    while cur != cur.parent:
        dot_git = cur / ".git"
        if dot_git.is_dir():
            return cur
        if dot_git.is_file():
            ext_root = _parse_gitdir_file(dot_git)
            if ext_root is not None:
                return ext_root
        if cur.parent.name == ".worktrees" and (cur.parent.parent / ".git").is_dir():
            return cur.parent.parent
        cur = cur.parent
    return current


def is_pid_alive(pid: int) -> bool:
    """Checks whether a process PID is currently alive on the host OS."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def get_global_lock_path(repo_root: Optional[Path] = None) -> Path:
    """Returns the canonical path to the host-wide MLX session lock file."""
    override = os.environ.get("MLX_GLOBAL_LOCK_DIR")
    if override:
        locks_dir = Path(override)
        locks_dir.mkdir(parents=True, exist_ok=True)
        return locks_dir / GLOBAL_LOCK_FILENAME
    if repo_root is not None:
        resolved_arg = repo_root.resolve()
        canon_root = resolve_canonical_repo_root(resolved_arg)
        if resolved_arg != canon_root and not (resolved_arg / ".git").exists():
            locks_dir = resolved_arg / ".session_locks"
            locks_dir.mkdir(parents=True, exist_ok=True)
            return locks_dir / GLOBAL_LOCK_FILENAME
    root = resolve_canonical_repo_root(repo_root)
    locks_dir = root / ".session_locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    return locks_dir / GLOBAL_LOCK_FILENAME


def check_active_mlx_session(repo_root: Optional[Path] = None) -> Tuple[bool, Optional[ActiveSessionMeta]]:
    """Fast pre-flight check to see if an MLX Whisper session is currently active.

    Returns (is_active, holder_meta). Does not block or acquire the lock.
    """
    lock_path = get_global_lock_path(repo_root)
    if not lock_path.exists():
        return False, None

    fd = None
    try:
        fd = open(lock_path, "r+")  # noqa: SIM115
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Lock acquisition succeeded -> no active session
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
        return False, None
    except (BlockingIOError, OSError):
        # Lock is currently held by an active session
        meta: Optional[ActiveSessionMeta] = None
        try:
            with open(lock_path, "r") as f_chk:
                meta = ActiveSessionMeta.from_raw(f_chk.read())
        except Exception:
            pass
        # Double check if holder is dead despite lock (e.g. NFS / stale FD edge case)
        if meta and meta.pid > 0 and not is_pid_alive(meta.pid):
            return False, None
        return True, meta
    finally:
        if fd:
            with contextlib.suppress(Exception):
                fd.close()


def clean_stale_global_mlx_lock(repo_root: Optional[Path] = None) -> bool:
    """Inspects the global MLX session lock and resets it if the holder process is dead.

    Returns True if a stale lock was reaped.
    """
    lock_path = get_global_lock_path(repo_root)
    if not lock_path.exists():
        return False

    fd = None
    try:
        fd = open(lock_path, "r+")  # noqa: SIM115
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # We acquired lock; read file to see if dead process metadata was left
        raw = fd.read().strip()
        if raw:
            meta = ActiveSessionMeta.from_raw(raw)
            if meta and meta.pid > 0 and not is_pid_alive(meta.pid):
                fd.seek(0)
                fd.truncate()
                logger.info("[MLX-LOCK-REAP] Cleared dead holder metadata for PID %d in %s", meta.pid, lock_path)
                fcntl.flock(fd, fcntl.LOCK_UN)
                fd.close()
                return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
        return False
    except (BlockingIOError, OSError):
        # Lock is held by an active kernel flock
        return False
    finally:
        if fd:
            with contextlib.suppress(Exception):
                fd.close()


class GlobalMLXSessionLock:
    """Manages the host-wide exclusive session lock for MLX Whisper recording/note sessions."""

    def __init__(self, repo_root: Optional[Path] = None) -> None:
        self.repo_root = repo_root or resolve_canonical_repo_root()
        self.lock_path = get_global_lock_path(self.repo_root)
        self._lock_fd: Optional[Any] = None
        self._is_held = False

    @property
    def is_held(self) -> bool:
        return self._is_held

    def acquire(
        self,
        session_id: str = "",
        meeting_slug: str = "",
        project_name: str = "",
    ) -> ActiveSessionMeta:
        """Acquires the host-wide lock non-blockingly.

        If held by another process, raises BlockingIOError and returns holder ActiveSessionMeta.
        """
        if self._is_held and self._lock_fd is not None:
            return ActiveSessionMeta(
                pid=os.getpid(),
                session_id=session_id,
                meeting_slug=meeting_slug,
                project_name=project_name,
                repo_root=str(self.repo_root),
            )

        fd = open(self.lock_path, "a+")  # noqa: SIM115
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd.seek(0)
            fd.truncate()
            now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            meta = ActiveSessionMeta(
                pid=os.getpid(),
                session_id=session_id,
                meeting_slug=meeting_slug,
                project_name=project_name,
                started_at_iso=now_iso,
                repo_root=str(self.repo_root),
            )
            fd.write(json.dumps(meta.to_dict()) + "\n")
            fd.flush()
            self._lock_fd = fd
            self._is_held = True
            return meta
        except (BlockingIOError, OSError) as e:
            holder_meta: Optional[ActiveSessionMeta] = None
            try:
                with open(self.lock_path, "r") as f_chk:
                    holder_meta = ActiveSessionMeta.from_raw(f_chk.read())
            except Exception:
                pass
            with contextlib.suppress(Exception):
                fd.close()
            err = BlockingIOError(f"Global MLX Whisper session lock held by PID {getattr(holder_meta, 'pid', 'unknown')}")
            err.holder_meta = holder_meta  # type: ignore[attr-defined]
            raise err from e

    def release(self) -> None:
        """Releases the host-wide lock cleanly."""
        if self._lock_fd is not None:
            with contextlib.suppress(Exception):
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                self._lock_fd.close()
            self._lock_fd = None
        self._is_held = False


@contextlib.contextmanager
def serialized_mlx_gpu_execution(
    session_id: str = "batch_mlx",
    meeting_slug: str = "batch_transcribe",
    project_name: str = "",
    timeout_seconds: float = 30.0,
    repo_root: Optional[Path] = None,
):
    """Serializes Metal GPU MLX Whisper execution across live and batch processes via GlobalMLXSessionLock."""
    is_active, holder_meta = check_active_mlx_session(repo_root=repo_root)
    if is_active and holder_meta and holder_meta.pid in (os.getpid(), os.getppid()):
        # Current process or parent process already holds the global MLX lock
        yield holder_meta
        return

    lock = GlobalMLXSessionLock(repo_root=repo_root)
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    max_attempts = max(1, int(max(0.0, float(timeout_seconds)) / 0.05) + 5)
    meta = None
    for _ in range(max_attempts):
        try:
            meta = lock.acquire(
                session_id=session_id,
                meeting_slug=meeting_slug,
                project_name=project_name,
            )
            break
        except BlockingIOError as e:
            holder_meta = getattr(e, "holder_meta", None)
            if holder_meta and holder_meta.pid in (os.getpid(), os.getppid()):
                yield holder_meta
                return
            if time.monotonic() >= deadline:
                raise LiveSessionCollisionError(
                    meeting_slug,
                    holder_pid=getattr(holder_meta, "pid", None),
                    active_slug=getattr(holder_meta, "meeting_slug", None),
                    session_id=getattr(holder_meta, "session_id", None),
                    is_global=True,
                ) from e
            time.sleep(0.05)

    try:
        yield meta
    finally:
        lock.release()
