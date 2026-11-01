"""
tools/tier2_common.py

Shared governed database connection and row mapping for Tier 2 GraphDB scan tools.
Enforces read-only access and repo-relative paths without raw sqlite3 imports.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.storage.governed_context import (  # noqa: E402
    GovernedStorageContext,
    IntegrityError,  # noqa: F401
)

DEFAULT_DB_PATH = ROOT / "graph" / "nee_graph.sqlite"
WORK_DIR = ROOT / "reports" / "graph_scout"
GRAPH_WORK = WORK_DIR / "nee_graph_tier2_work.sqlite"


class DictRow(dict):
    """Dictionary subclass supporting both key and index lookups (emulating sqlite3.Row)."""

    def __getitem__(self, item: Any) -> Any:
        if isinstance(item, int):
            return list(self.values())[item]
        return super().__getitem__(item)


def dict_row_factory(cursor: Any, row: tuple) -> DictRow:
    return DictRow({col[0]: row[idx] for idx, col in enumerate(cursor.description)})


def get_tier2_db_connection(db_path: Path | str = DEFAULT_DB_PATH) -> Any:
    """Connect to SQLite database in strictly read-only mode with DictRow factory."""
    resolved_path = Path(db_path).resolve()
    if not resolved_path.exists():
        raise FileNotFoundError(f"Database not found at {resolved_path}")
    conn = GovernedStorageContext.get_read_connection(resolved_path)
    conn.row_factory = dict_row_factory
    return conn


def get_tier2_write_connection(db_path: Path | str = GRAPH_WORK) -> Any:
    """Connect to SQLite database in read-write mode with DictRow factory."""
    resolved_path = Path(db_path).resolve()
    conn = GovernedStorageContext.connect(resolved_path, readonly=False)
    conn.row_factory = dict_row_factory
    return conn


def utc_now() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_id(prefix: str, *parts: str, length: int = 24) -> str:
    import hashlib
    h = hashlib.sha256("::".join([prefix] + list(parts)).encode())
    return f"{prefix}-{h.hexdigest()[:length]}"


def prepare_tier2_dedup_release(*args: Any, **kwargs: Any) -> Any:
    from dual_pipeline.tier2_release import prepare_dedup_release
    return prepare_dedup_release(*args, **kwargs)


def activate_tier2_dedup_release(*args: Any, **kwargs: Any) -> Any:
    from dual_pipeline.tier2_release import activate_dedup_release
    return activate_dedup_release(*args, **kwargs)


