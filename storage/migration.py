"""
storage/migration.py

Schema migration engine for SQLite canonical graph and evidence storage.
Tracks applied versions in `_schema_migrations` and applies sequential DDL updates.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from storage.connection import create_sqlite_conn, sqlite3

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    up_sql: str
    down_sql: Optional[str] = None

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.up_sql.strip().encode("utf-8")).hexdigest()


_SCHEMAS_DIR = Path(__file__).parent / "schemas"


def _load_migration_sql(filename: str) -> str:
    path = _SCHEMAS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


# Core sequential migrations
BUILTIN_MIGRATIONS: List[Migration] = [
    Migration(
        version=1,
        name="001_schema_migrations_table",
        up_sql="""
        CREATE TABLE IF NOT EXISTS _schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            checksum TEXT NOT NULL
        );
        """,
    ),
    Migration(
        version=2,
        name="002_storage_metadata_ledger",
        up_sql="""
        CREATE TABLE IF NOT EXISTS _storage_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """,
    ),
    Migration(
        version=3,
        name="003_storage_audit_ledger",
        up_sql="""
        CREATE TABLE IF NOT EXISTS _storage_audit_ledger (
            audit_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """,
    ),
    Migration(
        version=4,
        name="004_canonical_graph_nodes_history",
        up_sql=_load_migration_sql("migration_004.sql"),
    ),
]


class DestructiveMigrationError(RuntimeError):
    """Raised when a migration contains destructive operations without explicit [DATA_LOSS_APPROVED] marker."""
    pass


DESTRUCTIVE_PATTERNS = ["DROP TABLE", "DROP COLUMN", "TRUNCATE"]


def is_destructive_migration(sql: Optional[str]) -> bool:
    if not sql:
        return False
    sql_upper = sql.upper()
    return any(p in sql_upper for p in DESTRUCTIVE_PATTERNS)


class SchemaMigrator:
    """Manages transactional execution of schema migrations on SQLite databases."""

    def __init__(self, migrations: Optional[List[Migration]] = None) -> None:
        self.migrations = sorted(migrations or BUILTIN_MIGRATIONS, key=lambda m: m.version)

    def init_migration_table(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS _schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL,
                checksum TEXT NOT NULL
            );
        """)

    def get_applied_versions(self, conn: sqlite3.Connection) -> dict[int, str]:
        self.init_migration_table(conn)
        cursor = conn.execute("SELECT version, checksum FROM _schema_migrations ORDER BY version ASC")
        return {row[0]: row[1] for row in cursor.fetchall()}

    def apply_pending(self, conn: sqlite3.Connection, allow_destructive: bool = False) -> List[int]:
        applied = self.get_applied_versions(conn)
        applied_now: List[int] = []

        for m in self.migrations:
            if m.version in applied:
                existing_checksum = applied[m.version]
                if existing_checksum != m.checksum and m.version > 1:
                    logger.warning(
                        "Migration %d (%s) checksum mismatch: recorded %s, current %s",
                        m.version, m.name, existing_checksum, m.checksum
                    )
                continue

            # Fail closed on unapproved destructive migrations
            if is_destructive_migration(m.up_sql):
                is_authorized = (
                    ("[DATA_LOSS_APPROVED]" in m.name)
                    or ("[DATA_LOSS_APPROVED]" in m.up_sql)
                    or allow_destructive
                )
                if not is_authorized:
                    raise DestructiveMigrationError(
                        f"Migration {m.version} ('{m.name}') contains destructive operations without mandatory [DATA_LOSS_APPROVED] authorization."
                    )

            logger.info("Applying schema migration %d: %s", m.version, m.name)
            try:
                conn.executescript(m.up_sql)
                now_str = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO _schema_migrations (version, name, applied_at, checksum) VALUES (?, ?, ?, ?)",
                    (m.version, m.name, now_str, m.checksum),
                )
                conn.commit()
                applied_now.append(m.version)
            except Exception as e:
                conn.rollback()
                logger.error("Failed to apply migration %d (%s): %s", m.version, m.name, e)
                raise RuntimeError(f"Migration {m.version} failed: {e}") from e

        return applied_now

    def rollback_to_version(self, conn: sqlite3.Connection, target_version: int, allow_destructive: bool = False) -> List[int]:
        """Rolls back applied migrations down to target_version using down_sql."""
        applied = self.get_applied_versions(conn)
        applied_versions = sorted([v for v in applied.keys() if v > target_version], reverse=True)
        rolled_back: List[int] = []

        version_to_migration = {m.version: m for m in self.migrations}

        for v in applied_versions:
            m = version_to_migration.get(v)
            if not m or not m.down_sql:
                raise RuntimeError(f"Cannot rollback migration {v}: down_sql not defined")

            if is_destructive_migration(m.down_sql):
                is_authorized = (
                    ("[DATA_LOSS_APPROVED]" in m.name)
                    or ("[DATA_LOSS_APPROVED]" in m.down_sql)
                    or allow_destructive
                )
                if not is_authorized:
                    raise DestructiveMigrationError(
                        f"Rollback of migration {m.version} ('{m.name}') contains destructive operations without mandatory [DATA_LOSS_APPROVED] authorization."
                    )

            logger.info("Rolling back migration %d: %s", m.version, m.name)
            try:
                conn.executescript(m.down_sql)
                del_sql = "DELETE FROM _schema_migrations WHERE version = ?"
                conn.execute(del_sql, (v,))
                conn.commit()
                rolled_back.append(v)
            except Exception as e:
                conn.rollback()
                logger.error("Failed to rollback migration %d (%s): %s", m.version, m.name, e)
                raise RuntimeError(f"Rollback of migration {m.version} failed: {e}") from e

        return rolled_back


def run_storage_migrations(db_path: Path) -> List[int]:
    """Applies all pending migrations to the SQLite database at db_path."""
    migrator = SchemaMigrator()
    conn = create_sqlite_conn(Path(db_path))
    try:
        return migrator.apply_pending(conn)
    finally:
        conn.close()
