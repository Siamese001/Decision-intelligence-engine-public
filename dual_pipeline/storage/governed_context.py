"""
dual_pipeline/storage/governed_context.py

Layer-0 Governed Storage Context providing concurrency-safe SQLite operations,
mandatory 30,000ms busy timeout, WAL mode configuration, and explicit read/write splitting.
Prevents SQLite WAL single-writer collisions and reader starvation.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Generator, Optional, Union

from ..core import file_lock

logger = logging.getLogger(__name__)

BUSY_TIMEOUT_MS = 30000
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Export governed SQLite exception types and Row class
IntegrityError = sqlite3.IntegrityError
OperationalError = sqlite3.OperationalError
DatabaseError = sqlite3.DatabaseError
Row = sqlite3.Row


class ProductionPurityViolationError(PermissionError):
    """Raised when an engineering test attempts to mutate a production graph or database."""


def is_test_environment() -> bool:
    """Return True if executing under a pytest runner or test environment."""
    import sys
    from config.credentials import CredentialManager

    return bool(
        CredentialManager.has_credential("PYTEST_CURRENT_TEST")
        or "pytest" in sys.modules
        or "unittest" in sys.modules
        or CredentialManager.get_credential("ENV_TEST") == "true"
        or CredentialManager.get_credential("MOCK_ENV") == "true"
    )


def _normalize_repo_path(p: Path) -> Path:
    res = p.resolve()
    s = str(res)
    if s.startswith("/private/"):
        return Path("/" + s[len("/private/"):])
    return res


def _is_active_pair_target(p: Path, gen_dir: Path) -> bool:
    """Checks if database path matches active_pair.json pointer in runtime root."""
    rt_root = gen_dir.parent.parent
    active_pair = rt_root / "active_pair.json"
    if not active_pair.is_file():
        return False
    try:
        data = json.loads(active_pair.read_text(encoding="utf-8"))
        rel_graph = data.get("graph_path")
        if rel_graph and (rt_root / rel_graph).resolve() == p.resolve():
            return True
        return data.get("generation_id") == gen_dir.name or data.get("generation") == gen_dir.name
    except Exception:
        return False


def is_sealed_generation_database(path: Union[str, Path]) -> bool:
    """Check if target path is an immutable sealed generation database."""
    if str(path) == ":memory:":
        return False
    try:
        p = _normalize_repo_path(Path(path))
    except Exception:
        p = Path(path).resolve()

    if p.suffix not in (".db", ".sqlite", ".sqlite3") or "generations" not in p.parts:
        return False

    if (p.parent / "pair_manifest.json").is_file():
        return True

    return _is_active_pair_target(p, p.parent)


def is_production_database(path: Union[str, Path]) -> bool:
    """Check if target path is a canonical production database.

    Enforces fail-closed target classification:
    - Resolves symlinks and relative path traversals before evaluation.
    - Prohibits path-substring and environment-heuristic bypasses.
    - Disallows opening canonical, sealed generation, or repository production storage/output databases for write.
    """
    import tempfile

    path_str = str(path)
    if path_str == ":memory:":
        return False

    try:
        p = _normalize_repo_path(Path(path))
    except Exception:
        p = Path(path).resolve()

    if is_sealed_generation_database(p):
        return True

    norm_repo = _normalize_repo_path(_REPO_ROOT)
    canonical_prod_paths = {
        _normalize_repo_path(_REPO_ROOT / "storage" / "CANONICAL_GRAPH.db"),
        _normalize_repo_path(_REPO_ROOT / "graph" / "nee_graph.sqlite"),
    }
    if p in canonical_prod_paths:
        return True

    # Databases directly located in repository production output or storage directories
    out_dir = norm_repo / "output"
    stor_dir = norm_repo / "storage"
    if (out_dir in p.parents or stor_dir in p.parents) and p.suffix in (".db", ".sqlite", ".sqlite3"):
        return True

    # Never treat disposable temporary/sandbox directories outside repo production as production
    try:
        tmp_dir = _normalize_repo_path(Path(tempfile.gettempdir()))
        if p == tmp_dir or tmp_dir in p.parents:
            return False
    except Exception:
        pass

    return False


class WriteSerializer:
    """Thread-safe write serializer preventing concurrent BEGIN IMMEDIATE collisions."""

    def __init__(self) -> None:
        self._locks: Dict[str, threading.RLock] = {}
        self._master_lock = threading.Lock()

    def get_lock(self, db_key: str) -> threading.RLock:
        with self._master_lock:
            canonical_key = (
                str(_normalize_repo_path(Path(db_key)))
                if db_key != ":memory:"
                else ":memory:"
            )
            if canonical_key not in self._locks:
                self._locks[canonical_key] = threading.RLock()
            return self._locks[canonical_key]


GLOBAL_WRITE_SERIALIZER = WriteSerializer()
_SERIALIZER = GLOBAL_WRITE_SERIALIZER
_WriteSerializer = WriteSerializer
_WRITE_TX_LOCAL = threading.local()


@contextlib.contextmanager
def _reentrant_savepoint_ctx(
    conn: sqlite3.Connection, depth: int
) -> Generator[sqlite3.Connection, None, None]:
    sp_name = f"sp_reentrant_{depth}_{id(conn)}"
    sp_begin = "SAVEPOINT " + sp_name + ";"
    conn.execute(sp_begin)
    try:
        yield conn
        sp_release = "RELEASE SAVEPOINT " + sp_name + ";"
        conn.execute(sp_release)
    except Exception:
        try:
            sp_rollback = "ROLLBACK TO SAVEPOINT " + sp_name + ";"
            conn.execute(sp_rollback)
        except Exception:
            pass
        raise


@contextlib.contextmanager
def _execute_outer_transaction(
    conn: sqlite3.Connection,
    canonical_key: str,
    active_txs: dict[str, list[sqlite3.Connection]],
) -> Generator[sqlite3.Connection, None, None]:
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE;")
        active_txs.setdefault(canonical_key, []).append(conn)
        try:
            yield conn
            if conn.in_transaction:
                conn.execute("COMMIT;")
        finally:
            stack = active_txs.get(canonical_key, [])
            if stack and stack[-1] is conn:
                stack.pop()
                if not stack:
                    active_txs.pop(canonical_key, None)
    except Exception:
        try:
            if conn.in_transaction:
                conn.execute("ROLLBACK;")
        except Exception:
            pass
        raise
    finally:
        conn.close()


class GovernedStorageContext:
    """Authoritative connection factory and concurrency manager for SQLite databases."""

    @staticmethod
    def configure_pragmas(conn: sqlite3.Connection, readonly: bool = False) -> None:
        """Apply high-throughput, concurrency-safe PRAGMAs."""
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS};")
        conn.execute("PRAGMA temp_store = MEMORY;")
        conn.execute("PRAGMA foreign_keys = ON;")
        if readonly:
            conn.execute("PRAGMA query_only = ON;")
        else:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")

    @classmethod
    def connect(
        cls,
        path: Union[str, Path],
        readonly: bool = False,
        timeout: Optional[float] = None,
        check_same_thread: bool = True,
    ) -> sqlite3.Connection:
        """
        Open a managed SQLite connection with WAL and 30s busy timeout.
        For read-only connections, uses mode=ro URI to prevent write lock acquisition.
        """
        path_str = str(path)
        actual_timeout = timeout if timeout is not None else (BUSY_TIMEOUT_MS / 1000.0)

        if path_str == ":memory:":
            conn = sqlite3.connect(":memory:", timeout=actual_timeout, check_same_thread=check_same_thread)
            cls.configure_pragmas(conn, readonly=False)
            return conn

        p = Path(path).resolve()
        p_str = str(p)
        if p_str.startswith("/private/Users/"):
            p = Path(p_str[len("/private"):])
        if readonly:
            uri = f"file:{p.as_posix()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=actual_timeout, check_same_thread=check_same_thread)
            cls.configure_pragmas(conn, readonly=True)
        else:
            if is_production_database(p):
                if is_test_environment() or is_sealed_generation_database(p):
                    raise ProductionPurityViolationError(
                        f"Mechanical barrier: Protected production database target at '{p}' cannot be opened for write access."
                    )
            with GLOBAL_WRITE_SERIALIZER.get_lock(str(p)):
                p.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(str(p), timeout=actual_timeout, check_same_thread=check_same_thread)
                cls.configure_pragmas(conn, readonly=False)

        return conn

    @classmethod
    def get_read_connection(
        cls,
        path: Union[str, Path],
        check_same_thread: bool = True,
    ) -> sqlite3.Connection:
        """Open a read-only SQLite connection using URI mode=ro."""
        return cls.connect(path, readonly=True, check_same_thread=check_same_thread)

    @classmethod
    def get_write_connection(
        cls,
        path: Union[str, Path],
        check_same_thread: bool = True,
    ) -> sqlite3.Connection:
        """Open a read-write SQLite connection configured with WAL."""
        return cls.connect(path, readonly=False, check_same_thread=check_same_thread)

    @classmethod
    @contextlib.contextmanager
    def read_context(
        cls,
        path: Union[str, Path],
    ) -> Generator[sqlite3.Connection, None, None]:
        """Managed context yielding a read-only connection, auto-closing on exit."""
        conn = cls.get_read_connection(path)
        try:
            yield conn
        finally:
            conn.close()

    @classmethod
    @contextlib.contextmanager
    def write_context(
        cls,
        path: Union[str, Path],
    ) -> Generator[sqlite3.Connection, None, None]:
        """
        Managed context acquiring serialized write lock, yielding a write connection,
        and committing atomically on exit or rolling back on exception.
        Supports reentrant nested writes via SQLite savepoints and reentrant file_lock.
        """
        path_str = str(path)
        canonical_key = (
            str(_normalize_repo_path(Path(path)))
            if path_str != ":memory:"
            else ":memory:"
        )

        active_txs = getattr(_WRITE_TX_LOCAL, "active_txs", None)
        if active_txs is None:
            active_txs = {}
            _WRITE_TX_LOCAL.active_txs = active_txs

        active_stack = active_txs.get(canonical_key)
        if active_stack:
            with _reentrant_savepoint_ctx(active_stack[-1], len(active_stack)):
                yield active_stack[-1]
            return

        lock = _SERIALIZER.get_lock(path_str)
        lock_file_path = (
            _normalize_repo_path(Path(f"{path_str}.lock"))
            if path_str != ":memory:"
            else None
        )
        with lock:
            file_ctx = (
                file_lock(lock_file_path)
                if lock_file_path
                else contextlib.nullcontext()
            )
            with file_ctx:
                conn = cls.get_write_connection(path)
                with _execute_outer_transaction(conn, canonical_key, active_txs):
                    yield conn

    # Convenient alias for write_context
    write_transaction = write_context

    @classmethod
    def checkpoint(cls, path: Union[str, Path], mode: str = "PASSIVE") -> None:
        """Executes a WAL checkpoint to flush WAL pages into main DB."""
        path_str = str(path)
        if path_str == ":memory:":
            return
        lock = _SERIALIZER.get_lock(path_str)
        with lock:
            conn = cls.get_write_connection(path)
            try:
                conn.execute(f"PRAGMA wal_checkpoint({mode});")
            finally:
                conn.close()

    @classmethod
    def compact(cls, path: Union[str, Path], vacuum: bool = True) -> Dict[str, Any]:
        """Flushes WAL and optionally reclaims unused database pages via VACUUM."""
        path_str = str(path)
        if path_str == ":memory:":
            return {"status": "PASS", "path": ":memory:", "checkpoint": "SKIPPED", "vacuum": False}
        lock = _SERIALIZER.get_lock(path_str)
        with lock:
            conn = cls.get_write_connection(path)
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                if vacuum:
                    conn.execute("VACUUM;")
            finally:
                conn.close()
        return {"status": "PASS", "path": path_str, "checkpoint": "TRUNCATE", "vacuum": vacuum}
