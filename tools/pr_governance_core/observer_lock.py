"""Kernel-backed exclusive lock for observer runs."""
from __future__ import annotations

import fcntl
import json
import os
import socket
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator
from uuid import uuid4


class ObserverLockCollisionError(RuntimeError):
    """Another process currently holds the observer lock."""


@dataclass
class ObserverLockInfo:
    pid: int
    acquired_at: str
    hostname: str
    lock_file: str
    token: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


@contextmanager
def acquire_observer_lock(lock_path: Path, stale_timeout_seconds: int = 1800) -> Generator[ObserverLockInfo, None, None]:
    """Use an OS advisory lock; stale timestamp metadata never grants lock ownership."""
    del stale_timeout_seconds
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    stream = os.fdopen(descriptor, "r+", encoding="utf-8")
    acquired = False
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError as exc:
            raise ObserverLockCollisionError(f"Observer lock is held: {lock_path}") from exc
        info = ObserverLockInfo(os.getpid(), datetime.now(timezone.utc).isoformat(), socket.gethostname(), str(lock_path), uuid4().hex)
        stream.seek(0)
        stream.truncate()
        json.dump(info.to_dict(), stream)
        stream.flush()
        os.fsync(stream.fileno())
        yield info
    except ObserverLockCollisionError:
        stream.close()
        raise
    finally:
        if not stream.closed:
            try:
                stream.seek(0)
                stream.truncate()
                stream.flush()
                os.fsync(stream.fileno())
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()
                if acquired:
                    try:
                        lock_path.unlink(missing_ok=True)
                    except OSError:
                        pass
