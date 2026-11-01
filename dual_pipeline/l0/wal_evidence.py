"""Dual-Pipeline Layer 0: SQLite WAL Evidence & Mutation Storage Protocol.

Implements Law 4 (Immutable Provenance First) and Storage Concurrency Safety.
Enforces WAL mode, 30-second busy timeouts, and idempotent record append operations.
Strictly Zero-Dependency (standard library only). Layer L0 must NEVER import L1-L6.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List, Optional

from dual_pipeline.l0.contracts import EvidenceRecord, MutationRecord
from dual_pipeline.l0.provenance import compute_content_digest

# Governed SQLite connection parameters
DEFAULT_BUSY_TIMEOUT_MS = 30_000


def get_governed_wal_connection(db_path: str | Path) -> sqlite3.Connection:
    """Creates a governed SQLite connection in WAL mode with a 30-second busy timeout."""
    from dual_pipeline.parameters import get_int_parameter
    busy_timeout_ms = get_int_parameter("storage.sqlite_wal_busy_timeout_ms", DEFAULT_BUSY_TIMEOUT_MS)
    conn = sqlite3.connect(str(db_path), timeout=busy_timeout_ms / 1000.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms};")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_evidence_wal_db(db_path: str | Path) -> None:
    """Initializes the lightweight append-only tables for evidence and mutations."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with get_governed_wal_connection(db_path) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS l0_evidence_item (
            evidence_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            speaker TEXT NOT NULL,
            text TEXT NOT NULL,
            timestamp REAL NOT NULL,
            namespace TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            digest TEXT UNIQUE NOT NULL,
            properties_json TEXT,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS l0_mutation_record (
            mutation_id TEXT PRIMARY KEY,
            delta_id TEXT NOT NULL,
            operation_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            before_hash TEXT,
            after_hash TEXT,
            applied INTEGER NOT NULL DEFAULT 0,
            timestamp REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_l0_ev_digest ON l0_evidence_item(digest);
        CREATE INDEX IF NOT EXISTS idx_l0_ev_tenant_ns ON l0_evidence_item(tenant_id, namespace);
        """)


def append_evidence_record(
    db_path: str | Path,
    record: EvidenceRecord,
) -> bool:
    """
    Idempotently appends an EvidenceRecord into SQLite WAL.
    If the content digest already exists, ignores duplicate without throwing.
    """
    if not record.digest:
        record.digest = compute_content_digest(record.text).sha256

    init_evidence_wal_db(db_path)
    with get_governed_wal_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO l0_evidence_item (
                evidence_id, session_id, source_id, speaker, text,
                timestamp, namespace, tenant_id, digest, properties_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.evidence_id,
                record.session_id,
                record.source_id,
                record.speaker,
                record.text,
                record.timestamp,
                record.namespace,
                record.tenant_id,
                record.digest,
                json.dumps(record.properties, sort_keys=True),
                record.created_at,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0


def append_mutation_record(
    db_path: str | Path,
    record: MutationRecord,
) -> bool:
    """Idempotently appends a MutationRecord into SQLite WAL."""
    init_evidence_wal_db(db_path)
    with get_governed_wal_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO l0_mutation_record (
                mutation_id, delta_id, operation_type, entity_id,
                before_hash, after_hash, applied, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.mutation_id,
                record.delta_id,
                record.operation_type,
                record.entity_id,
                record.before_hash,
                record.after_hash,
                1 if record.applied else 0,
                record.timestamp,
            ),
        )
        conn.commit()
        return cursor.rowcount > 0


def get_evidence_record_by_digest(
    db_path: str | Path,
    digest: str,
) -> Optional[EvidenceRecord]:
    """Retrieves an EvidenceRecord by its content digest."""
    if not Path(db_path).exists():
        return None

    with get_governed_wal_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM l0_evidence_item WHERE digest = ?",
            (digest,),
        ).fetchone()
        if not row:
            return None

        props = json.loads(row["properties_json"]) if row["properties_json"] else {}
        return EvidenceRecord(
            evidence_id=row["evidence_id"],
            session_id=row["session_id"],
            source_id=row["source_id"],
            speaker=row["speaker"],
            text=row["text"],
            timestamp=row["timestamp"],
            namespace=row["namespace"],
            tenant_id=row["tenant_id"],
            digest=row["digest"],
            properties=props,
            created_at=row["created_at"],
        )


def query_evidence_by_tenant_and_namespace(
    db_path: str | Path,
    tenant_id: str,
    namespace: str,
) -> List[EvidenceRecord]:
    """Queries all EvidenceRecords matching tenant_id and namespace."""
    if not Path(db_path).exists():
        return []

    with get_governed_wal_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM l0_evidence_item WHERE tenant_id = ? AND namespace = ? ORDER BY created_at ASC",
            (tenant_id, namespace),
        ).fetchall()

        results = []
        for row in rows:
            props = json.loads(row["properties_json"]) if row["properties_json"] else {}
            results.append(
                EvidenceRecord(
                    evidence_id=row["evidence_id"],
                    session_id=row["session_id"],
                    source_id=row["source_id"],
                    speaker=row["speaker"],
                    text=row["text"],
                    timestamp=row["timestamp"],
                    namespace=row["namespace"],
                    tenant_id=row["tenant_id"],
                    digest=row["digest"],
                    properties=props,
                    created_at=row["created_at"],
                )
            )
        return results
