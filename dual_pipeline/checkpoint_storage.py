"""Checksum-verified checkpoint envelopes and serialized crash-safe file persistence."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterator

VERSION = "2.0.0"
BINDING_VERSION = "1.0.0"


class CheckpointIntegrityError(RuntimeError):
    """A checkpoint cannot be reused; callers may record the structured reason."""

    def __init__(self, reason: str, message: str):
        self.reason = reason
        super().__init__(message)


def digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def binding_digest(binding: dict[str, Any]) -> str:
    return digest({"binding_version": BINDING_VERSION, "binding": binding})


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _validate_current(data: dict[str, Any]) -> None:
    required = {"version", "binding_version", "binding", "binding_digest", "epoch",
                "revision", "stages", "metadata", "invalidations", "checksum"}
    if not required <= data.keys():
        raise ValueError("incomplete versioned envelope")
    _validate_metadata(data)
    _validate_binding(data)
    checksum = data["checksum"]
    expected = digest({key: value for key, value in data.items() if key != "checksum"})
    if not isinstance(checksum, str) or not hmac.compare_digest(checksum, expected):
        raise ValueError("checkpoint checksum mismatch")


def _validate_metadata(data: dict[str, Any]) -> None:
    if not isinstance(data["epoch"], str) or not data["epoch"]:
        raise ValueError("missing checkpoint epoch")
    if type(data["revision"]) is not int or data["revision"] < 0:
        raise ValueError("invalid revision")
    if not isinstance(data["metadata"], dict) or not isinstance(data["invalidations"], list):
        raise ValueError("invalid checkpoint metadata or audit history")
    if any(not isinstance(event, dict) for event in data["invalidations"]):
        raise ValueError("invalid audit event")


def _validate_binding(data: dict[str, Any]) -> None:
    if data["binding_version"] != BINDING_VERSION:
        raise CheckpointIntegrityError("UNSUPPORTED_BINDING_VERSION", "Unsupported checkpoint binding version")
    binding = data["binding"]
    if binding is not None and (not isinstance(binding, dict) or not binding):
        raise ValueError("invalid binding")
    expected = binding_digest(binding) if binding is not None else None
    if data["binding_digest"] != expected:
        raise ValueError("binding digest mismatch")


def read_checkpoint(path: Path) -> dict[str, Any] | None:
    try:
        encoded = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except UnicodeError as exc:
        raise CheckpointIntegrityError(
            "CORRUPT_CHECKPOINT", f"Fail-Fast: Checkpoint file '{path}' is corrupted: invalid UTF-8"
        ) from exc
    try:
        data = json.loads(encoded, object_pairs_hook=_unique_keys)
        if not isinstance(data, dict):
            raise ValueError(f"Fail-Fast: Checkpoint file '{path}' contains non-dict JSON")
        version = data.get("version", "1.0.0")
        if version not in {"1.0.0", VERSION}:
            raise CheckpointIntegrityError("UNSUPPORTED_VERSION", "Unsupported checkpoint version")
        if not isinstance(data.get("metadata", {}), dict):
            raise ValueError("invalid checkpoint metadata")
        stages = data.get("stages", {})
        if not isinstance(stages, dict) or any(not isinstance(value, dict) for value in stages.values()):
            raise ValueError("invalid stage mapping")
        if version == VERSION:
            _validate_current(data)
        return data
    except CheckpointIntegrityError:
        raise
    except (ValueError, TypeError) as exc:
        raise CheckpointIntegrityError(
            "CORRUPT_CHECKPOINT", f"Fail-Fast: Checkpoint file '{path}' is corrupted: {exc}"
        ) from exc


def fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def checkpoint_lock(path: Path, timeout_sec: float = 30.0) -> Iterator[None]:
    """Keep the lock inode stable; unlinking it would permit concurrent lock holders."""
    import contextlib
    import time
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path.with_name(path.name + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
    deadline = time.monotonic() + timeout_sec
    acquired = False
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting {timeout_sec}s for checkpoint lock at {path}.lock")
                time.sleep(0.05)
        yield
    finally:
        if acquired:
            with contextlib.suppress(Exception):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def atomic_write_checkpoint(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    """Called under checkpoint_lock; never expose partial bytes at the destination."""
    payload = {key: value for key, value in data.items() if key != "checksum"}
    payload["checksum"] = digest(payload)
    encoded = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        fsync_directory(path.parent)
    finally:
        temporary_path.unlink(missing_ok=True)
    return payload
