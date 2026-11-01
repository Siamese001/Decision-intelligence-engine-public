"""
dual_pipeline/canonical_pipeline/concurrency.py

Cross-process mutual exclusion for meeting ingestion.
Prevents concurrent execution of the same meeting across worker processes.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from dual_pipeline.core import ROOT
from dual_pipeline.exceptions import ConcurrentIngestionError

logger = logging.getLogger(__name__)

_MEETING_LOCK_STATE = threading.local()


def derive_meeting_lock_key(
    input_path: Optional[Any] = None,
    meta: Optional[dict[str, Any]] = None,
    output_dir: Optional[str] = None,
) -> str:
    """Derives a deterministic lock key identifying a specific meeting."""
    meta = meta or {}
    slug = meta.get("meeting_name") or meta.get("meeting") or meta.get("filename_slug")
    date_val = meta.get("date") or meta.get("date_filename")
    project = meta.get("project")

    if slug and date_val:
        raw_key = f"{project or 'default'}:{slug}:{date_val}"
    elif input_path:
        p = Path(str(input_path)).resolve()
        raw_key = f"file:{p}"
    elif output_dir and output_dir != "auto":
        raw_key = f"out:{Path(output_dir).resolve()}"
    else:
        raise ValueError("Cannot derive deterministic meeting lock key from provided arguments.")

    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:32]


class MeetingIngestionLock:
    """Advisory inter-process file lock preventing concurrent ingestion of the same meeting."""

    def __init__(
        self,
        lock_key: str,
        input_path: Optional[Any] = None,
        lock_dir: Optional[Path] = None,
    ) -> None:
        self.lock_key = lock_key
        self.input_path = str(input_path) if input_path else ""
        from storage.credentials import CredentialManager
        test_sandbox = CredentialManager.get_env("TEST_SANDBOX_DIR")
        if test_sandbox:
            sandbox_locks = (Path(test_sandbox) / "locks").resolve()
            repo_locks = (ROOT / ".local" / "dual_pipeline" / "locks").resolve()
            if lock_dir is None or lock_dir.resolve() == repo_locks or (ROOT / ".local" / "dual_pipeline") in lock_dir.resolve().parents:
                self.lock_dir = sandbox_locks
            else:
                self.lock_dir = lock_dir.resolve()
        else:
            self.lock_dir = (lock_dir or (ROOT / ".local" / "dual_pipeline" / "locks")).resolve()
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.lock_file = self.lock_dir / f"meeting_{self.lock_key}.lock"
        self._fd: Optional[int] = None
        self._reentrant_depth: int = 0

    def acquire(self) -> None:
        """Acquires exclusive, non-blocking lock or raises ConcurrentIngestionError."""
        held = getattr(_MEETING_LOCK_STATE, "held_locks", None)
        if held is None:
            held = {}
            _MEETING_LOCK_STATE.held_locks = held

        canonical_key = str(self.lock_file.resolve())
        if canonical_key in held:
            held[canonical_key] += 1
            self._reentrant_depth += 1
            return

        try:
            self._fd = os.open(str(self.lock_file), os.O_CREAT | os.O_RDWR, 0o644)
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Write diagnostic owner info
            info = {
                "pid": os.getpid(),
                "acquired_at": time.time(),
                "lock_key": self.lock_key,
                "input_path": self.input_path,
            }
            os.ftruncate(self._fd, 0)
            os.write(self._fd, json.dumps(info).encode("utf-8"))
            os.fsync(self._fd)
            held[canonical_key] = 1
            self._reentrant_depth = 1
        except (BlockingIOError, OSError) as e:
            # Another process holds the lock
            owner_info = self._read_owner_info()
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None
            msg = (
                f"Concurrent ingestion rejected for meeting key '{self.lock_key}'. "
                f"Another process (PID {owner_info.get('pid', 'unknown')}) holds the lock "
                f"since {owner_info.get('acquired_at', 'unknown')} for input '{owner_info.get('input_path', self.input_path)}'."
            )
            logger.error(msg)
            raise ConcurrentIngestionError(msg) from e

    def release(self) -> None:
        """Releases the lock and cleans up file descriptor."""
        held = getattr(_MEETING_LOCK_STATE, "held_locks", None)
        canonical_key = str(self.lock_file.resolve())
        if held and canonical_key in held:
            held[canonical_key] -= 1
            if held[canonical_key] > 0:
                return
            held.pop(canonical_key, None)

        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
            finally:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None

    def _read_owner_info(self) -> dict[str, Any]:
        try:
            if self.lock_file.is_file():
                content = self.lock_file.read_text(encoding="utf-8")
                if content.strip():
                    return json.loads(content)
        except Exception:
            pass
        return {}

    def __enter__(self) -> MeetingIngestionLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.release()
