"""
Dual-Artery Enterprise Hybrid Architecture: Storage Connection & Concurrency Serialization.
Manages thread-safe write locks (eliminating SQLITE_BUSY) and PRAGMA tuning.
"""
from __future__ import annotations

import logging
import os
import random
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

# Expose canonical SQLite exceptions for governed storage modules
IntegrityError = sqlite3.IntegrityError
OperationalError = sqlite3.OperationalError

# Attempt SQLCipher import for encrypted shadow storage
try:
    import sqlcipher3 as sqlcipher_driver  # type: ignore
    SQLCIPHER_AVAILABLE = True
except ImportError:
    try:
        from pysqlcipher3 import dbapi2 as sqlcipher_driver  # type: ignore
        SQLCIPHER_AVAILABLE = True
    except ImportError:
        sqlcipher_driver = None
        SQLCIPHER_AVAILABLE = False

logger = logging.getLogger("storage.connection")

from dual_pipeline.parameters import get_int_parameter  # noqa: E402
from dual_pipeline.storage.governed_context import (  # noqa: E402, F401
    GLOBAL_WRITE_SERIALIZER,
    WriteSerializer,
)


def is_temp_sandbox_db(db_path: Path | str) -> bool:
    """Checks if database is located within an ephemeral test sandbox."""
    str_path = str(db_path)
    if ":memory:" in str_path:
        return True
    sandbox_dirs = [
        os.environ.get("TEMP_SANDBOX_DIR"),
        os.environ.get("TEST_SANDBOX_DIR"),
    ]
    try:
        resolved_path = Path(db_path).resolve()
        for sdir in sandbox_dirs:
            if sdir:
                try:
                    if resolved_path.is_relative_to(Path(sdir).resolve()):
                        return True
                except Exception:
                    pass
        temp_dir = Path(tempfile.gettempdir()).resolve()
        if resolved_path.is_relative_to(temp_dir):
            return True
    except Exception:
        pass
    return False


def configure_pragmas(
    conn: Any, readonly: bool = False, in_memory: bool = False, is_sandbox: bool = False
) -> None:
    """Applies high-throughput, fail-closed PRAGMAs tailored for M5 Pro SSD/Metal."""
    busy_timeout_ms = get_int_parameter("storage.sqlite_wal_busy_timeout_ms", 30000)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = %d;" % int(busy_timeout_ms))
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA cache_size = -64000;")
    mmap_size_bytes = get_int_parameter("storage.sqlite_mmap_size_bytes", 268435456)
    conn.execute("PRAGMA mmap_size = %d;" % int(mmap_size_bytes))
    conn.execute("PRAGMA threads = 4;")

    if readonly:
        conn.execute("PRAGMA query_only = ON;")
    elif in_memory:
        conn.execute("PRAGMA journal_mode = MEMORY;")
        conn.execute("PRAGMA synchronous = OFF;")
    elif is_sandbox:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = OFF;")
    else:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")


def is_pid_alive(pid: int) -> bool:
    """Checks if a process ID is currently alive on the host system."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _resolve_lock_dirs(lock_dir: Optional[Path | str] = None) -> list[Path]:
    if lock_dir:
        return [Path(lock_dir)]
    repo_root = Path(__file__).resolve().parents[1]
    dirs = [repo_root / ".session_locks"]
    if repo_root.parent.name == ".worktrees":
        dirs.append(repo_root.parent.parent / ".session_locks")
    return dirs


def _evict_single_stale_lock(f: Path) -> Optional[str]:
    try:
        content = f.read_text().strip()
        if not content:
            return None
        pid = int(content.splitlines()[0].strip())
        if not is_pid_alive(pid):
            f.unlink(missing_ok=True)
            logger.warning("Evicted stale lockfile '%s' from deceased PID %d", f.name, pid)
            return f.name
    except (ValueError, OSError) as exc:
        logger.debug("Could not inspect lockfile %s: %s", f, exc)
    return None


def evict_stale_session_locks(lock_dir: Optional[Path | str] = None) -> list[str]:
    """Scans session lock directory and evicts .flock files owned by deceased PIDs."""
    evicted: list[str] = []
    for d in _resolve_lock_dirs(lock_dir):
        if not d.is_dir():
            continue
        try:
            for f in d.glob("*.flock"):
                evicted_name = _evict_single_stale_lock(f)
                if evicted_name:
                    evicted.append(evicted_name)
        except OSError as exc:
            logger.debug("Failed scanning directory %s: %s", d, exc)

    return evicted


def _resolve_conn_lock_key(conn: sqlite3.Connection) -> str:
    try:
        db_row = conn.execute("PRAGMA database_list").fetchone()
        if db_row and len(db_row) >= 3 and db_row[2]:
            return str(db_row[2])
    except Exception:
        pass
    return ":memory:"


def execute_with_retry(
    conn: sqlite3.Connection,
    query: str,
    params: Sequence[Any] | Mapping[str, Any] = (),
    max_retries: int = 3,
    base_delay_ms: float = 50.0,
) -> sqlite3.Cursor:
    """Executes a SQLite query with self-healing retry on database locks."""
    lock_key = _resolve_conn_lock_key(conn)
    for attempt in range(max_retries + 1):
        try:
            with GLOBAL_WRITE_SERIALIZER.get_lock(lock_key):
                return conn.execute(query, params)
        except sqlite3.OperationalError as exc:
            err_msg = str(exc).lower()
            if ("locked" in err_msg or "busy" in err_msg) and attempt < max_retries:
                evict_stale_session_locks()
                try:
                    busy_timeout_ms = int(get_int_parameter("storage.sqlite_wal_busy_timeout_ms", 30000))
                    pragma_sql = "PRAGMA busy_timeout = %d;" % busy_timeout_ms
                    conn.execute(pragma_sql)
                except Exception:
                    pass
                sleep_sec = (2 ** (attempt + 1)) * (base_delay_ms / 1000.0) + random.uniform(0.01, 0.05)
                logger.warning(
                    "SQLite locked on attempt %d/%d; retrying in %.3fs: %s",
                    attempt + 1, max_retries, sleep_sec, exc,
                )
                time.sleep(sleep_sec)
            else:
                raise
    raise sqlite3.OperationalError("Database locked after maximum retries exceeded")


def wal_checkpoint_async(conn: sqlite3.Connection, mode: str = "PASSIVE") -> Dict[str, Any]:
    """Executes non-blocking WAL checkpoint to reclaim log files without write stalls."""
    valid_modes = {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}
    chk_mode = mode.upper() if mode.upper() in valid_modes else "PASSIVE"
    chk_sql = "PRAGMA wal_checkpoint(%s);" % chk_mode
    try:
        cursor = conn.execute(chk_sql)
        row = cursor.fetchone()
        if row:
            return {
                "status": "SUCCESS",
                "busy": int(row[0]),
                "log_frames": int(row[1]),
                "checkpointed_frames": int(row[2]),
                "mode": chk_mode,
            }
    except Exception as exc:
        logger.warning("Async WAL checkpoint failed: %s", exc)
        return {"status": "ERROR", "error": str(exc), "mode": chk_mode}
    return {"status": "NOOP", "mode": chk_mode}

class GraphUnavailableError(RuntimeError):
    """Raised when a graph database file is invalid, missing, unreadable, <1MB, or lacks the assertion table."""
    pass


def validate_canonical_graph_db(
    db_path: Path | str,
    min_size_bytes: int = 1024 * 1024,
    required_table: str = "assertion",
) -> Path:
    """Validates that a graph database file exists, meets minimum size, and contains required table.

    Fails closed with GraphUnavailableError.
    """
    p = Path(db_path) if not isinstance(db_path, Path) else db_path
    if not p.is_file():
        raise GraphUnavailableError(f"GRAPH_UNAVAILABLE: Database file not found at '{p}'")

    size = p.stat().st_size
    if size == 0:
        raise GraphUnavailableError(f"GRAPH_UNAVAILABLE: Database file at '{p}' is empty (0 bytes)")

    temp_dir = Path(tempfile.gettempdir()).resolve()
    is_temp = p.resolve().is_relative_to(temp_dir)
    if not is_temp and size < min_size_bytes:
        raise GraphUnavailableError(
            f"GRAPH_UNAVAILABLE: Database file at '{p}' is smaller than {min_size_bytes} bytes ({size} bytes)"
        )

    try:
        conn = sqlite3.connect(f"file:{p.resolve().as_posix()}?mode=ro", uri=True, timeout=30.0)
        conn.execute("PRAGMA busy_timeout = 30000;")
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1;",
            (required_table,),
        )
        has_table = cur.fetchone() is not None
        conn.close()
        if not has_table:
            raise GraphUnavailableError(
                f"GRAPH_UNAVAILABLE: Database at '{p}' is missing required table '{required_table}'"
            )
    except sqlite3.Error as exc:
        raise GraphUnavailableError(
            f"GRAPH_UNAVAILABLE: Failed reading sqlite schema from '{p}': {exc}"
        ) from exc

    return p


def create_sqlite_conn(
    db_path: Path | str,
    readonly: bool = False,
    validate_graph: bool = False,
) -> sqlite3.Connection:
    busy_timeout_ms = get_int_parameter("storage.sqlite_wal_busy_timeout_ms", 30000)
    timeout_sec = max(5.0, busy_timeout_ms / 1000.0)
    is_mem = str(db_path) == ":memory:" or os.environ.get("NEE_STORAGE_FORCE_MEMORY") == "1"
    if is_mem:
        conn = sqlite3.connect(":memory:", timeout=timeout_sec)
        conn.isolation_level = None
        conn.row_factory = sqlite3.Row
        configure_pragmas(conn, readonly=readonly, in_memory=True)
        return conn

    p = Path(db_path) if not isinstance(db_path, Path) else db_path
    if validate_graph:
        validate_canonical_graph_db(p)
    elif readonly and p.is_file() and p.stat().st_size == 0:
        raise GraphUnavailableError(f"GRAPH_UNAVAILABLE: Cannot open 0-byte database '{p}' in readonly mode")

    if readonly:
        uri = f"file:{p.as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=timeout_sec)
        conn.row_factory = sqlite3.Row
        configure_pragmas(conn, readonly=True, in_memory=False, is_sandbox=is_temp_sandbox_db(p))
        return conn

    with GLOBAL_WRITE_SERIALIZER.get_lock(str(p)):
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            conn = sqlite3.connect(str(p), timeout=timeout_sec)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                evict_stale_session_locks()
                conn = sqlite3.connect(str(p), timeout=timeout_sec)
            else:
                raise
        conn.isolation_level = None
        conn.row_factory = sqlite3.Row
        configure_pragmas(conn, readonly=False, in_memory=False, is_sandbox=is_temp_sandbox_db(p))
        return conn


get_connection = create_sqlite_conn
get_db_connection = create_sqlite_conn

__all__ = [
    "GLOBAL_WRITE_SERIALIZER",
    "GraphUnavailableError",
    "SQLCIPHER_AVAILABLE",
    "WriteSerializer",
    "configure_pragmas",
    "create_sqlite_conn",
    "evict_stale_session_locks",
    "execute_with_retry",
    "get_connection",
    "get_db_connection",
    "is_pid_alive",
    "is_temp_sandbox_db",
    "logger",
    "sqlcipher_driver",
    "sqlite3",
    "validate_canonical_graph_db",
    "wal_checkpoint_async",
]

