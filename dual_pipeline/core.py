"""Shared deterministic primitives for the NEE dual-pipeline runtime."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import threading
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_VERSION = "nee-dual-pipeline-1.0.0"
POLICY_VERSION = "55.0+dual-pipeline-1.0.0"
CONTRACT_VERSION = "1.0.0"
ZERO_HASH = "0" * 64
CANONICAL_EMBEDDING_MODEL_ID = "BAAI/bge-m3"  # noqa: SSOT_EXEMPT


def is_link_or_reparse(path: Path) -> bool:
    """Return true for symbolic links, junctions, or Windows reparse points."""

    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        attributes = int(getattr(path.lstat(), "st_file_attributes", 0))
        return bool(
            attributes
            & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
        )
    except OSError:
        return False


def assert_safe_mutable_path(
    root: Path,
    target: Path,
    *,
    label: str = "mutable path",
) -> Path:
    """Reject path escapes and existing link/reparse components without writing.

    Both lexical and resolved containment are required.  Inspecting the raw
    components is essential on Windows because resolving a junction first
    discards the evidence that the mutable subtree was redirected.
    """

    raw_root = Path(os.path.abspath(root))
    raw_target = Path(os.path.abspath(target))
    resolved_root = raw_root.resolve()
    resolved_target = raw_target.resolve()
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError as exc:
        raise RuntimeError(f"{label} resolves outside its runtime root") from exc
    candidates: list[Path] = []
    candidate = raw_target
    reached_root = False
    while True:
        candidates.append(candidate)
        if candidate.resolve() == resolved_root:
            reached_root = True
            break
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    if not reached_root:
        raise RuntimeError(f"{label} escapes its runtime root")
    if raw_root not in candidates:
        candidates.append(raw_root)
    for candidate in candidates:
        if is_link_or_reparse(candidate):
            raise RuntimeError(f"{label} contains a link or reparse point: {candidate}")
    return resolved_target


def utc_now() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _format_date_parts(parts: list[str]) -> str | None:
    if len(parts) == 3 and len(parts[2]) == 4:
        return f"{parts[2]}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
    if len(parts) == 3 and len(parts[0]) == 4:
        return f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}"
    return None


def normalize_iso_date(value: Any) -> str | None:
    """Normalize date strings (e.g. MM/DD/YYYY, MM-DD-YYYY, ISO) to YYYY-MM-DD."""
    if not value or not isinstance(value, str) or (val := value.strip()).lower() in ("", "null", "none"):
        return None
    candidate = val[:10] if len(val) >= 10 and val[4] == "-" and val[7] == "-" else None
    if not candidate and (sep := "/" if "/" in val else ("-" if "-" in val else None)):
        candidate = _format_date_parts(val[:10].split(sep))
    if candidate:
        try:
            return parsed.isoformat() if (parsed := dt.date.fromisoformat(candidate)).year > 0 else None
        except (ValueError, TypeError):
            pass
    return None


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest(value: Any) -> str:
    return sha256_bytes(canonical_bytes(value))


def sha256_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def artifact_digest(value: dict[str, Any]) -> str:
    payload = copy.deepcopy(value)
    payload.pop("canonical_digest", None)
    return digest(payload)


def seal_artifact(value: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(value)
    value["canonical_digest"] = {
        "algorithm": "sha256",
        "scope": "artifact_without_canonical_digest",
        "value": artifact_digest(value),
    }
    return value


def stable_id(prefix: str, *parts: Any, length: int = 24) -> str:
    clean_prefix = re.sub(r"[^A-Za-z0-9._:-]+", "-", prefix).strip("-") or "id"
    return f"{clean_prefix}-{digest(list(parts))[:length]}"


_GENERIC_SPEAKER_PATTERN = re.compile(
    r"\b(?:Speaker(?![\s_\-]*(?:attribution|diarization|verification|identification)\b)(?:[\s_\-]+[A-Z\d]+)?|Voice(?:[\s_\-]+[A-Z\d]+)?|PartnerAdvisory\s+Speaker|PartnerAdvisory\s+Team(?!\s*\(room\))(?:\s*\([^)]*\))?|Unknown(?:\s+Speaker)?|Unattributed|Anonymous|Participant(?:[\s_\-]+[A-Z\d]+)?|SPEAKER_\d+)\b",
    re.IGNORECASE,
)


def trace_id(*parts: Any) -> str:
    return digest(["trace", *parts])[:32]


def span_id(*parts: Any) -> str:
    return digest(["span", *parts])[:16]


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in text if character.isalnum())


def normalize_space(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def slug(value: str, fallback: str = "artifact") -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return (result or fallback)[:72]


def resolve_governed_path(path: Path | str, repository_root: Path | None = None) -> Path:
    """Resolve a governed path, falling back to archive/ for archived release assets."""
    target = Path(path)
    root = (repository_root or ROOT).resolve()
    candidate = (root / target) if not target.is_absolute() else target
    if candidate.exists():
        return candidate
    raw = str(target).replace("\\", "/")
    if "approvals/v" in raw and "approvals/v55" not in raw:
        archive_raw = raw.replace("approvals/", "archive/approvals/")
        archive_candidate = (root / archive_raw) if not Path(archive_raw).is_absolute() else Path(archive_raw)
        if archive_candidate.exists():
            return archive_candidate
    return candidate


def read_json(path: Path | str) -> Any:
    target = resolve_governed_path(path)
    return json.loads(target.read_text(encoding="utf-8"))


def atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    target_token = hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:16]
    temporary = path.with_name(f".{target_token}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp")
    with temporary.open("wb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def write_json(path: Path, value: Any) -> None:
    atomic_write_bytes(
        path,
        (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )


def write_json_exclusive(path: Path, value: Any) -> None:
    """Create a JSON artifact once without replacing an existing receipt."""

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    target_token = hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:16]
    temporary = path.with_name(
        f".{target_token}.{os.getpid()}.{os.urandom(8).hex()}.tmp"
    )
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # A hard-link publish is atomic and fails if the destination exists.
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def connect_sqlite(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    from .storage.governed_context import GovernedStorageContext
    connection = GovernedStorageContext.connect(path, readonly=readonly)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA recursive_triggers=ON")
    connection.execute("PRAGMA cache_size = -64000")
    connection.execute("PRAGMA mmap_size = 2147483648")
    connection.execute("PRAGMA threads = 4")
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        connection.close()
        raise RuntimeError("SQLite foreign-key enforcement is unavailable")
    return connection


def sqlite_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = connect_sqlite(source, readonly=True)
    destination_connection = connect_sqlite(destination)
    try:
        source_connection.backup(destination_connection)
        destination_connection.commit()
    finally:
        destination_connection.close()
        source_connection.close()


def exact_file_copy(source: Path, destination: Path) -> None:
    """Copy an immutable file byte-for-byte and verify the result."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    with source.open("rb") as source_stream, destination.open("xb") as destination_stream:
        shutil.copyfileobj(source_stream, destination_stream, length=1024 * 1024)
        destination_stream.flush()
        os.fsync(destination_stream.fileno())
    if sha256_file(source) != sha256_file(destination):
        raise RuntimeError("byte-for-byte file copy verification failed")


def sqlite_integrity(path: Path) -> dict[str, Any]:
    connection = connect_sqlite(path, readonly=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = [dict(row) for row in connection.execute("PRAGMA foreign_key_check")]
        return {
            "integrity_check": integrity,
            "foreign_key_violation_count": len(foreign_keys),
            "foreign_key_violations": foreign_keys,
        }
    finally:
        connection.close()


_FILE_LOCK_STATE = threading.local()


@contextmanager
def file_lock(
    path: Path,
    timeout_seconds: float = 60.0,
    poll_interval: float = 0.02,
) -> Iterator[None]:
    """Cross-platform advisory lock powered by filelock (reentrant per-thread)."""
    from filelock import FileLock, Timeout

    resolved_path = Path(path).resolve()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    held = getattr(_FILE_LOCK_STATE, "held_locks", None)
    if held is None:
        held = {}
        _FILE_LOCK_STATE.held_locks = held

    canonical_key = str(resolved_path)
    if canonical_key in held:
        held[canonical_key] += 1
        try:
            yield
        finally:
            held[canonical_key] -= 1
            if held[canonical_key] <= 0:
                held.pop(canonical_key, None)
        return

    flock = FileLock(str(resolved_path), timeout=timeout_seconds)
    try:
        flock.acquire(timeout=timeout_seconds, poll_interval=poll_interval)
    except Timeout as exc:
        raise TimeoutError(f"File lock timed out after {timeout_seconds}s: {resolved_path}") from exc

    try:
        held[canonical_key] = 1
        yield
    finally:
        held.pop(canonical_key, None)
        try:
            flock.release()
        except Exception:
            pass


def load_ssot_env(root: Path | None = None, override: bool = False) -> bool:
    """Load the single source of truth environment configuration from env_di."""
    if root is None and not override:
        from config.credentials import ensure_ssot_env_loaded
        return ensure_ssot_env_loaded()
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    target = (root or ROOT) / "env_di"
    if target.is_file():
        return load_dotenv(target, override=override)
    return False


class StorageExhaustionError(RuntimeError):
    """Raised when available disk space is below required minimum threshold."""
    pass


def assert_disk_headroom(target_path: Path | str, required_bytes: int = 2 * 1024 * 1024 * 1024) -> None:
    """Preflight check that target filesystem has at least required_bytes free (default 2GB buffer)."""
    check_dir = Path(target_path).expanduser().resolve()
    while not check_dir.exists() and check_dir.parent != check_dir:
        check_dir = check_dir.parent
    try:
        if (usage := shutil.disk_usage(check_dir)).free < required_bytes:
            raise StorageExhaustionError(
                f"Insufficient disk space on {check_dir}: {usage.free} bytes free, {required_bytes} bytes required"
            )
    except OSError as e:
        raise RuntimeError(f"Fail-Fast: Cannot verify disk headroom on {check_dir}: {e}") from e

