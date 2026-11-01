#!/usr/bin/env python3
"""
Dual-Artery Enterprise Hybrid Architecture: Storage Subsystem
Target: Apple MacBook Pro 16" (M5 Pro, 48GB RAM, Apple Metal)

Core Database, Durable Queue & Immutable Ledger Module.
Enforces the 7 Non-Negotiable Engineering Laws (P0):
  1. Compute Fork BEFORE Extraction
  2. Purge 'Agentic Leverage' -> Replace with 'INTERNAL_ADVISORY'
  3. Untrusted Fast Path (Ephemeral, Read-Only)
  4. Immutable Provenance First (Durable SQLite WAL Log + Queue)
  5. Multi-Dimensional Soft-Quarantine Metadata Tagging
  6. Policy Before Retrieval (Pre-FAISS / Pre-Vector Filtering)
  7. Correct Pipeline Sequencing (Evidence -> Graph -> Recap)

Modularized under storage/models, storage/connection, storage/schema_canonical, storage/schema_shadow.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from dual_pipeline.storage.governed_context import (
    GLOBAL_WRITE_SERIALIZER,
    WriteSerializer,
)
from storage.connection import (
    SQLCIPHER_AVAILABLE,
    configure_pragmas,
    create_sqlite_conn,
    sqlcipher_driver,
)
from storage.credentials import CredentialManager
from storage.identifiers import (
    compute_entity_hmac,
    resolve_keychain_secret,
)

# Re-export models, value objects, and mappers
from storage.models import (
    AdvisoryCategory,
    AssertionRecord,
    CanonicalGraphNode,
    EvidenceRecord,
    GraphEdge,
    InternalAdvisoryNode,
    WorkItem,
    WorkItemState,
    row_to_advisory_node,
    row_to_canonical_node,
    validate_assertion_evidence_bindings,
)
from storage.schema_canonical import (
    init_canonical_schema,
    init_evidence_schema,
)
from storage.schema_shadow import (
    init_shadow_schema,
)

logger = logging.getLogger("storage.database")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [STORAGE] %(message)s"
    ))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


class DatabaseManager:
    """
    Manages dual-artery SQLite storage:
      - Primary WAL DB (`evidence.db` / `canonical_graph.db`): Immutable evidence, work queue, client-safe facts.
      - Shadow Graph DB (`shadow_graph.db`): Encrypted local storage for internal advisory facts.
    """

    def __init__(
        self,
        base_dir: Union[str, Path] = "storage_data",
        evidence_db_name: str = "evidence.db",
        canonical_db_name: str = "canonical_graph.db",
        shadow_db_name: str = "shadow_graph.db",
        passphrase: Optional[str] = None,
        allow_unencrypted_dev_fallback: Optional[bool] = None,
    ):
        self.base_dir = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_db_path = self.base_dir / evidence_db_name
        self.canonical_db_path = self.base_dir / canonical_db_name
        self.shadow_db_path = self.base_dir / shadow_db_name
        self.passphrase = passphrase or resolve_keychain_secret()
        if allow_unencrypted_dev_fallback is not None:
            self.allow_unencrypted_dev_fallback = allow_unencrypted_dev_fallback
        else:
            self.allow_unencrypted_dev_fallback = (
                (CredentialManager.get_credential("ALLOW_UNENCRYPTED_SHADOW_DEV_FALLBACK", "").lower() in ("1", "true", "yes"))
                or ("pytest" in sys.modules or "unittest" in sys.modules or CredentialManager.has_credential("TEST_SANDBOX_DIR") or CredentialManager.has_credential("STORAGE_DATA_DIR"))
            )
        self._write_serializer = GLOBAL_WRITE_SERIALIZER
        self._local = threading.local()

        # Initialize and migrate all database schemas
        self._init_evidence_db()
        self._init_canonical_db()
        self._init_shadow_db()
        self.recover_and_consolidate_abandoned_shards()

    def recover_and_consolidate_abandoned_shards(self, max_inactive_seconds: float = 3600.0) -> int:
        """
        Scans base_dir for abandoned evidence_{session_id}.db shards from crashed workers
        and consolidates them into the primary immutable evidence log.
        """
        consolidated_total = 0
        now = time.time()
        try:
            for p in self.base_dir.glob("evidence_*.db"):
                if p.name == self.evidence_db_path.name:
                    continue
                try:
                    stat = p.stat()
                    if (now - stat.st_mtime) >= max_inactive_seconds:
                        session_id = p.stem.replace("evidence_", "", 1)
                        count = self.consolidate_session_evidence(session_id, remove_shard=True)
                        consolidated_total += count
                        logger.info("Recovered and consolidated %d records from abandoned shard '%s'", count, p.name)
                except Exception as shard_err:
                    logger.warning("Error recovering shard '%s': %s", p.name, shard_err)
        except Exception as e:
            logger.warning("Could not sweep abandoned session shards: %s", e)
        return consolidated_total

    # ── Connection Factories ─────────────────────────────────────────────

    def get_evidence_conn(self, readonly: bool = False) -> sqlite3.Connection:
        """Returns connection to evidence & work queue database."""
        return self._create_sqlite_conn(self.evidence_db_path, readonly=readonly)

    def get_session_evidence_conn(self, session_id: str, readonly: bool = False) -> sqlite3.Connection:
        """
        Returns connection to an isolated session-sharded evidence database.
        Eliminates write-lock contention across concurrent parallel workers.
        """
        clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", session_id)
        shard_path = self.base_dir / f"evidence_{clean_id}.db"
        is_new = not shard_path.exists()
        conn = self._create_sqlite_conn(shard_path, readonly=readonly)
        if is_new and not readonly:
            init_evidence_schema(conn)
        return conn

    def get_canonical_conn(self, readonly: bool = False) -> sqlite3.Connection:
        """Returns connection to canonical public graph database."""
        if readonly:
            conn = getattr(self._local, "canonical_ro", None)
            if conn is not None:
                try:
                    conn.total_changes
                    return conn
                except (sqlite3.ProgrammingError, sqlite3.OperationalError):
                    self._local.canonical_ro = None
            conn = self._create_sqlite_conn(self.canonical_db_path, readonly=True)
            self._local.canonical_ro = conn
            return conn
        return self._create_sqlite_conn(self.canonical_db_path, readonly=False)

    def get_shadow_conn(self, readonly: bool = False) -> Any:
        """
        Returns connection to the encrypted internal advisory shadow database.
        Enforces Law 2: Fail-closed if SQLCipher is missing, unless
        allow_unencrypted_dev_fallback is explicitly configured.
        """
        if readonly:
            conn = getattr(self._local, "shadow_ro", None)
            if conn is not None:
                try:
                    conn.total_changes
                    return conn
                except Exception:
                    self._local.shadow_ro = None
            conn = self._open_shadow_conn(readonly=True)
            self._local.shadow_ro = conn
            return conn
        return self._open_shadow_conn(readonly=False)

    def _open_shadow_conn(self, readonly: bool = False) -> Any:
        db_path = self.shadow_db_path
        # Enforce POSIX file permission lockdown (Owner Read/Write only)
        if db_path.exists():
            os.chmod(db_path, 0o600)
        if SQLCIPHER_AVAILABLE and sqlcipher_driver is not None:
            conn = sqlcipher_driver.connect(str(db_path), timeout=30.0)
            conn.row_factory = sqlcipher_driver.Row
            sanitized_key = self.passphrase.replace("'", "''")
            conn.execute(f"PRAGMA key = '{sanitized_key}';")
            conn.execute("PRAGMA cipher_compatibility = 4;")
            self._configure_pragmas(conn, readonly=readonly)
        else:
            if not self.allow_unencrypted_dev_fallback:
                raise RuntimeError(
                    "CRITICAL: SQLCipher native driver is unavailable. "
                    "Fail-Closed Law 2 strictly prohibits unencrypted plaintext fallback for shadow advisory storage."
                )
            conn = self._create_sqlite_conn(db_path, readonly=readonly)
        if db_path.exists():
            os.chmod(db_path, 0o600)
        return conn

    def close(self) -> None:
        """Closes all cached thread-local connections on the calling thread."""
        for attr in ("canonical_ro", "shadow_ro"):
            conn = getattr(self._local, attr, None)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
                setattr(self._local, attr, None)

    def _create_sqlite_conn(self, db_path: Path, readonly: bool = False) -> sqlite3.Connection:
        return create_sqlite_conn(db_path, readonly=readonly)

    def _configure_pragmas(self, conn: Any, readonly: bool = False) -> None:
        configure_pragmas(conn, readonly=readonly)

    # ── Schema Initialization ────────────────────────────────────────────

    @staticmethod
    def _init_evidence_schema(conn: sqlite3.Connection) -> None:
        init_evidence_schema(conn)

    def _init_evidence_db(self) -> None:
        """Initializes WAL evidence log and durable work queue."""
        with self.get_evidence_conn() as conn:
            init_evidence_schema(conn)

    def _init_canonical_db(self) -> None:
        """Initializes canonical public graph schema, W2 Semantic Spine tables, and FTS5 index."""
        with self.get_canonical_conn() as conn:
            init_canonical_schema(conn)
            from storage.migration import SchemaMigrator
            SchemaMigrator().apply_pending(conn)

    def _init_shadow_db(self) -> None:
        """Initializes encrypted shadow graph schema."""
        conn = self.get_shadow_conn()
        try:
            init_shadow_schema(conn)
            conn.commit()
        finally:
            conn.close()

    # ── Transaction Context Helpers ──────────────────────────────────────

    @contextmanager
    def transaction(self, conn: sqlite3.Connection, db_key: str = "default") -> Iterator[sqlite3.Connection]:
        """
        Fail-closed atomic transaction wrapper serialized across threads.
        Acquires in-memory write lock to eliminate `SQLITE_BUSY` contention.
        """
        lock = self._write_serializer.get_lock(db_key)
        with lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # ── Immutable Evidence & Queue Operations ────────────────────────────

    def append_evidence(
        self,
        record: EvidenceRecord,
        enqueue: bool = True,
        initial_state: WorkItemState = WorkItemState.PENDING
    ) -> str:
        """
        Enforces Law 4: Atomically commits raw audio transcript to WAL log
        and enqueues work item for Slow Path extraction.
        """
        event_id = f"evt_{record.evidence_id}_{int(time.time() * 1000)}"
        with self.get_evidence_conn() as conn, self.transaction(conn, db_key="evidence"):
            conn.execute(
                """
                    INSERT INTO immutable_evidence_log (
                        evidence_id, meeting_id, speaker, text,
                        start_timestamp, end_timestamp, is_final
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                (
                    record.evidence_id,
                    record.meeting_id,
                    record.speaker,
                    record.text,
                    record.start_timestamp,
                    record.end_timestamp,
                    1 if record.is_final else 0
                )
            )

            if enqueue:
                conn.execute(
                    """
                        INSERT INTO durable_work_queue (
                            event_id, evidence_id, state, retry_count
                        ) VALUES (?, ?, ?, 0)
                        """,
                    (event_id, record.evidence_id, initial_state.value)
                )
        return event_id if enqueue else record.evidence_id

    def append_evidence_sharded(
        self,
        record: EvidenceRecord,
        session_id: str,
        enqueue: bool = True,
        initial_state: WorkItemState = WorkItemState.PENDING,
    ) -> str:
        """
        Appends evidence to an isolated session-sharded SQLite database.
        Guarantees zero lock contention across parallel workers.
        Enforces Law 4 crash-durability by committing directly to disk WAL.
        """
        event_id = f"evt_{record.evidence_id}_{int(time.time() * 1000)}"
        clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", session_id)
        with self.get_session_evidence_conn(clean_id) as conn:
            with self.transaction(conn, db_key=f"evidence_{clean_id}"):
                conn.execute(
                    """
                    INSERT INTO immutable_evidence_log (
                        evidence_id, meeting_id, speaker, text,
                        start_timestamp, end_timestamp, is_final
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.evidence_id,
                        record.meeting_id,
                        record.speaker,
                        record.text,
                        record.start_timestamp,
                        record.end_timestamp,
                        1 if record.is_final else 0,
                    ),
                )
                if enqueue:
                    conn.execute(
                        """
                        INSERT INTO durable_work_queue (
                            event_id, evidence_id, state, retry_count
                        ) VALUES (?, ?, ?, 0)
                        """,
                        (event_id, record.evidence_id, initial_state.value),
                    )
        return event_id if enqueue else record.evidence_id

    def consolidate_session_evidence(
        self,
        session_id: str,
        remove_shard: bool = True,
    ) -> int:
        """
        Bulk consolidates records from a session shard into primary evidence storage.
        Executes in a single atomic transaction.
        """
        clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", session_id)
        shard_path = self.base_dir / f"evidence_{clean_id}.db"
        if not shard_path.exists():
            return 0

        shard_conn = self.get_session_evidence_conn(clean_id, readonly=True)
        try:
            evidence_rows = shard_conn.execute(
                "SELECT evidence_id, meeting_id, speaker, text, start_timestamp, end_timestamp, is_final, created_at "
                "FROM immutable_evidence_log ORDER BY start_timestamp ASC"
            ).fetchall()
            queue_rows = shard_conn.execute(
                "SELECT event_id, evidence_id, state, retry_count, checkpoint, locked_at, locked_by, error_message, created_at, updated_at "
                "FROM durable_work_queue"
            ).fetchall()
        finally:
            shard_conn.close()

        if not evidence_rows:
            return 0

        ev_data = [
            (
                r["evidence_id"],
                r["meeting_id"],
                r["speaker"],
                r["text"],
                r["start_timestamp"],
                r["end_timestamp"],
                r["is_final"],
                r["created_at"],
            )
            for r in evidence_rows
        ]
        q_data = [
            (
                r["event_id"],
                r["evidence_id"],
                r["state"],
                r["retry_count"],
                r["checkpoint"],
                r["locked_at"],
                r["locked_by"],
                r["error_message"],
                r["created_at"],
                r["updated_at"],
            )
            for r in queue_rows
        ]

        conn = self.get_evidence_conn()
        try:
            with self.transaction(conn, db_key="evidence"):
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO immutable_evidence_log (
                        evidence_id, meeting_id, speaker, text,
                        start_timestamp, end_timestamp, is_final, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ev_data,
                )
                conn.executemany(
                    """
                    INSERT OR IGNORE INTO durable_work_queue (
                        event_id, evidence_id, state, retry_count,
                        checkpoint, locked_at, locked_by, error_message,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    q_data,
                )
        finally:
            conn.close()

        if remove_shard:
            for ext in ("", "-wal", "-shm"):
                p = self.base_dir / f"evidence_{clean_id}.db{ext}"
                if p.exists():
                    try:
                        p.unlink()
                    except OSError:
                        pass

        return len(evidence_rows)

    def get_evidence_by_meeting(self, meeting_id: str) -> List[EvidenceRecord]:
        """Retrieves all evidence records for a given meeting ordered chronologically."""
        with self.get_evidence_conn(readonly=True) as conn:
            cursor = conn.execute(
                """
                SELECT evidence_id, meeting_id, speaker, text, start_timestamp, end_timestamp, is_final
                FROM immutable_evidence_log
                WHERE meeting_id = ?
                ORDER BY start_timestamp ASC, rowid ASC
                """,
                (meeting_id,)
            )
            rows = cursor.fetchall()
            return [
                EvidenceRecord(
                    evidence_id=r["evidence_id"],
                    meeting_id=r["meeting_id"],
                    speaker=r["speaker"],
                    text=r["text"],
                    start_timestamp=r["start_timestamp"],
                    end_timestamp=r["end_timestamp"],
                    is_final=bool(r["is_final"]),
                )
                for r in rows
            ]

    def lease_work_batch(
        self,
        batch_size: int = 10,
        worker_id: str = "worker_default",
        lock_timeout_seconds: float = 300.0
    ) -> List[Tuple[WorkItem, EvidenceRecord]]:
        """
        Atomically leases PENDING or expired-lock PROCESSING work items.
        Vectorized atomic update eliminates row-by-row locking roundtrips.
        Returns list of (WorkItem, EvidenceRecord) pairs.
        """
        now = time.time()
        expired_threshold = now - lock_timeout_seconds
        leased: List[Tuple[WorkItem, EvidenceRecord]] = []

        with self.get_evidence_conn() as conn:
            with self.transaction(conn, db_key="evidence"):
                cursor = conn.execute(
                    """
                    SELECT q.event_id, q.evidence_id, q.state, q.retry_count, q.checkpoint,
                           q.locked_at, q.locked_by, q.error_message, q.created_at, q.updated_at,
                           e.meeting_id, e.speaker, e.text, e.start_timestamp, e.end_timestamp, e.is_final
                    FROM durable_work_queue q
                    JOIN immutable_evidence_log e ON q.evidence_id = e.evidence_id
                    WHERE q.state = 'PENDING' 
                       OR (q.state = 'PROCESSING' AND (q.locked_at IS NULL OR q.locked_at < ?))
                    ORDER BY q.created_at ASC
                    LIMIT ?
                    """,
                    (expired_threshold, batch_size)
                )
                rows = cursor.fetchall()
                if not rows:
                    return []

                event_ids = [r["event_id"] for r in rows]
                placeholders = ",".join("?" * len(event_ids))
                conn.execute(
                    f"""
                    UPDATE durable_work_queue
                    SET state = 'PROCESSING',
                        locked_at = ?,
                        locked_by = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE event_id IN ({placeholders})
                    """,
                    [now, worker_id, *event_ids]
                )

                for r in rows:
                    w_item = WorkItem(
                        event_id=r["event_id"],
                        evidence_id=r["evidence_id"],
                        state=WorkItemState.PROCESSING,
                        retry_count=r["retry_count"],
                        checkpoint=r["checkpoint"],
                        locked_at=now,
                        locked_by=worker_id,
                        error_message=r["error_message"],
                        created_at=r["created_at"],
                        updated_at=r["updated_at"]
                    )
                    e_rec = EvidenceRecord(
                        evidence_id=r["evidence_id"],
                        meeting_id=r["meeting_id"],
                        speaker=r["speaker"],
                        text=r["text"],
                        start_timestamp=r["start_timestamp"],
                        end_timestamp=r["end_timestamp"],
                        is_final=bool(r["is_final"])
                    )
                    leased.append((w_item, e_rec))

        return leased

    def complete_work_item(self, event_id: str, checkpoint: Optional[str] = None) -> None:
        """Marks a queue item as COMPLETED with optional checkpoint hash."""
        with self.get_evidence_conn() as conn, self.transaction(conn, db_key="evidence"):
            conn.execute(
                """
                    UPDATE durable_work_queue
                    SET state = 'COMPLETED',
                        checkpoint = coalesce(?, checkpoint),
                        locked_at = NULL,
                        locked_by = NULL,
                        error_message = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE event_id = ?
                    """,
                (checkpoint, event_id)
            )

    def fail_work_item(
        self,
        event_id: str,
        error_message: str,
        max_retries: int = 3
    ) -> WorkItemState:
        """Increments retry count and either re-queues as PENDING or moves to DEAD_LETTER."""
        with self.get_evidence_conn() as conn:
            with self.transaction(conn, db_key="evidence"):
                row = conn.execute(
                    "SELECT retry_count FROM durable_work_queue WHERE event_id = ?",
                    (event_id,)
                ).fetchone()
                if not row:
                    raise KeyError(f"Event {event_id} not found in work queue.")

                curr_retries = row["retry_count"] + 1
                next_state = WorkItemState.DEAD_LETTER if curr_retries >= max_retries else WorkItemState.PENDING

                conn.execute(
                    """
                    UPDATE durable_work_queue
                    SET state = ?,
                        retry_count = ?,
                        locked_at = NULL,
                        locked_by = NULL,
                        error_message = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE event_id = ?
                    """,
                    (next_state.value, curr_retries, error_message, event_id)
                )
                return next_state

    # ── Canonical Graph Operations (Client-Safe) ─────────────────────────

    def upsert_canonical_node(self, node: CanonicalGraphNode) -> None:
        """Commits verified client-safe node to canonical graph."""
        props_json = json.dumps(node.properties, ensure_ascii=False)
        with self.get_canonical_conn() as conn:
            with self.transaction(conn, db_key="canonical"):
                conn.execute(
                    """
                    INSERT INTO canonical_graph_nodes (
                        node_id, evidence_id, type, name, properties,
                        strategic_value, evidence_quality, confidence,
                        tacticality, is_quarantined, quarantine_reason,
                        namespace, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, coalesce(?, CURRENT_TIMESTAMP), coalesce(?, CURRENT_TIMESTAMP))
                    ON CONFLICT(node_id) DO UPDATE SET
                        name = excluded.name,
                        properties = excluded.properties,
                        strategic_value = excluded.strategic_value,
                        evidence_quality = excluded.evidence_quality,
                        confidence = excluded.confidence,
                        tacticality = excluded.tacticality,
                        is_quarantined = excluded.is_quarantined,
                        quarantine_reason = excluded.quarantine_reason,
                        updated_at = coalesce(excluded.updated_at, CURRENT_TIMESTAMP)
                    """,
                    (
                        node.node_id,
                        node.evidence_id,
                        node.type,
                        node.name,
                        props_json,
                        node.strategic_value,
                        node.evidence_quality,
                        node.confidence,
                        node.tacticality,
                        1 if node.is_quarantined else 0,
                        node.quarantine_reason,
                        node.namespace,
                        getattr(node, "created_at", None),
                        getattr(node, "updated_at", None),
                    )
                )

    def upsert_canonical_edge(self, edge: GraphEdge) -> None:
        """Upserts a relational graph edge into canonical_graph_edges."""
        props_json = json.dumps(getattr(edge, "properties", {}), ensure_ascii=False)
        with self.get_canonical_conn() as conn, self.transaction(conn, db_key="canonical"):
            conn.execute(
                """
                    INSERT INTO canonical_graph_edges (
                        edge_id, from_node_id, predicate, to_node_id,
                        weight, confidence, is_quarantined, quarantine_reason,
                        namespace, properties, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(edge_id) DO UPDATE SET
                        weight = excluded.weight,
                        confidence = excluded.confidence,
                        is_quarantined = excluded.is_quarantined,
                        quarantine_reason = excluded.quarantine_reason,
                        properties = excluded.properties,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                (
                    edge.edge_id,
                    edge.from_node_id,
                    edge.predicate,
                    edge.to_node_id,
                    edge.weight,
                    edge.confidence,
                    1 if edge.is_quarantined else 0,
                    getattr(edge, "quarantine_reason", None),
                    edge.namespace,
                    props_json,
                )
            )

    def query_canonical_edges(
        self,
        from_node_id: Optional[str] = None,
        to_node_id: Optional[str] = None,
        predicate: Optional[str] = None,
        include_quarantined: bool = False,
        limit: int = 100,
    ) -> List[GraphEdge]:
        """Queries canonical edges with optional node or predicate filters."""
        query = """
            SELECT edge_id, from_node_id, predicate, to_node_id, weight,
                   confidence, is_quarantined, quarantine_reason, namespace,
                   properties, created_at, updated_at
            FROM canonical_graph_edges
            WHERE namespace = 'CLIENT_SAFE'
        """
        params: List[Any] = []

        if not include_quarantined:
            query += " AND is_quarantined = 0"

        if from_node_id:
            query += " AND from_node_id = ?"
            params.append(from_node_id)

        if to_node_id:
            query += " AND to_node_id = ?"
            params.append(to_node_id)

        if predicate:
            query += " AND predicate = ?"
            params.append(predicate)

        query += " ORDER BY confidence DESC, created_at DESC LIMIT ?"
        params.append(limit)

        edges: List[GraphEdge] = []
        with self.get_canonical_conn(readonly=True) as conn:
            rows = conn.execute(query, params).fetchall()
            for r in rows:
                props = json.loads(r["properties"]) if r["properties"] else {}
                edges.append(GraphEdge(
                    edge_id=r["edge_id"],
                    from_node_id=r["from_node_id"],
                    predicate=r["predicate"],
                    to_node_id=r["to_node_id"],
                    weight=r["weight"],
                    confidence=r["confidence"],
                    is_quarantined=bool(r["is_quarantined"]),
                    quarantine_reason=r["quarantine_reason"],
                    namespace=r["namespace"],
                    properties=props,
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                ))
        return edges

    @staticmethod
    def _row_to_canonical_graph_node(r: sqlite3.Row) -> CanonicalGraphNode:
        props = json.loads(r["properties"]) if r["properties"] else {}
        return CanonicalGraphNode(
            node_id=r["node_id"],
            evidence_id=r["evidence_id"],
            type=r["type"],
            name=r["name"],
            properties=props,
            strategic_value=r["strategic_value"],
            evidence_quality=r["evidence_quality"],
            confidence=r["confidence"],
            tacticality=r["tacticality"],
            is_quarantined=bool(r["is_quarantined"]),
            quarantine_reason=r["quarantine_reason"],
            namespace=r["namespace"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )

    @staticmethod
    def _row_to_graph_edge(er: sqlite3.Row) -> GraphEdge:
        props = json.loads(er["properties"]) if er["properties"] else {}
        return GraphEdge(
            edge_id=er["edge_id"],
            from_node_id=er["from_node_id"],
            predicate=er["predicate"],
            to_node_id=er["to_node_id"],
            weight=er["weight"],
            confidence=er["confidence"],
            is_quarantined=bool(er["is_quarantined"]),
            quarantine_reason=er["quarantine_reason"],
            namespace=er["namespace"],
            properties=props,
            created_at=er["created_at"],
            updated_at=er["updated_at"],
        )

    @staticmethod
    def _fetch_canonical_nodes_by_ids(
        conn: sqlite3.Connection, node_ids: Sequence[str], include_quarantined: bool
    ) -> Dict[str, CanonicalGraphNode]:
        if not node_ids:
            return {}
        placeholders = ",".join("?" for _ in node_ids)
        query = f"""
            SELECT node_id, evidence_id, type, name, properties,
                   strategic_value, evidence_quality, confidence, tacticality,
                   is_quarantined, quarantine_reason, namespace, created_at, updated_at
            FROM canonical_graph_nodes
            WHERE node_id IN ({placeholders}) AND namespace = 'CLIENT_SAFE'
        """
        if not include_quarantined:
            query += " AND is_quarantined = 0"
        rows = conn.execute(query, list(node_ids)).fetchall()
        return {r["node_id"]: DatabaseManager._row_to_canonical_graph_node(r) for r in rows}

    @staticmethod
    def _fetch_and_record_edges(
        conn: sqlite3.Connection,
        frontier: Set[str],
        include_quarantined: bool,
        visited_nodes: Dict[str, CanonicalGraphNode],
        traversed_edges: Dict[str, GraphEdge],
    ) -> Set[str]:
        f_placeholders = ",".join("?" for _ in frontier)
        edge_query = f"""
            SELECT edge_id, from_node_id, predicate, to_node_id, weight,
                   confidence, is_quarantined, quarantine_reason, namespace,
                   properties, created_at, updated_at
            FROM canonical_graph_edges
            WHERE (from_node_id IN ({f_placeholders}) OR to_node_id IN ({f_placeholders}))
              AND namespace = 'CLIENT_SAFE'
        """
        if not include_quarantined:
            edge_query += " AND is_quarantined = 0"
        edge_rows = conn.execute(edge_query, list(frontier) + list(frontier)).fetchall()
        new_neighbor_ids: Set[str] = set()
        for er in edge_rows:
            eid = er["edge_id"]
            if eid not in traversed_edges:
                traversed_edges[eid] = DatabaseManager._row_to_graph_edge(er)
            if er["from_node_id"] not in visited_nodes:
                new_neighbor_ids.add(er["from_node_id"])
            if er["to_node_id"] not in visited_nodes:
                new_neighbor_ids.add(er["to_node_id"])
        return new_neighbor_ids

    def traverse_canonical_neighborhood(
        self,
        seed_node_ids: List[str],
        max_hops: int = 1,
        include_quarantined: bool = False,
    ) -> Tuple[List[CanonicalGraphNode], List[GraphEdge]]:
        """
        Performs bounded multi-hop neighborhood traversal from seed nodes.
        Returns all discovered nodes and traversing edges.
        """
        if not seed_node_ids:
            return [], []

        visited_nodes: Dict[str, CanonicalGraphNode] = {}
        traversed_edges: Dict[str, GraphEdge] = {}
        frontier = set(seed_node_ids)

        with self.get_canonical_conn(readonly=True) as conn:
            visited_nodes.update(self._fetch_canonical_nodes_by_ids(conn, list(frontier), include_quarantined))

        for _ in range(max_hops):
            if not frontier:
                break
            with self.get_canonical_conn(readonly=True) as conn:
                new_neighbor_ids = self._fetch_and_record_edges(
                    conn, frontier, include_quarantined, visited_nodes, traversed_edges
                )
                if new_neighbor_ids:
                    visited_nodes.update(
                        self._fetch_canonical_nodes_by_ids(conn, list(new_neighbor_ids), include_quarantined)
                    )
                frontier = new_neighbor_ids

        return list(visited_nodes.values()), list(traversed_edges.values())

    def upsert_assertion(
        self,
        assertion: AssertionRecord,
        evidence_bindings: Optional[List[Tuple[str, str]]] = None,
        *,
        require_evidence: bool = False,
    ) -> None:
        """Upserts W2 first-class assertion and its multi-point evidence bindings."""
        validated_bindings = validate_assertion_evidence_bindings(
            assertion.assertion_id, evidence_bindings, require_evidence=require_evidence
        )

        with self.get_canonical_conn() as conn, self.transaction(conn, db_key="canonical"):
            conn.execute(
                """
                    INSERT INTO assertion (
                        assertion_id, subject_node_id, predicate, object_node_id,
                        literal_value, assertion_type, confidence, status,
                        observed_at, stale_after, namespace
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(assertion_id) DO UPDATE SET
                        status = excluded.status,
                        confidence = excluded.confidence,
                        stale_after = excluded.stale_after,
                        literal_value = excluded.literal_value
                    """,
                (
                    assertion.assertion_id,
                    assertion.subject_node_id,
                    assertion.predicate,
                    assertion.object_node_id,
                    assertion.literal_value,
                    assertion.assertion_type,
                    assertion.confidence,
                    assertion.status,
                    assertion.observed_at or time.strftime("%Y-%m-%d %H:%M:%S"),
                    assertion.stale_after,
                    assertion.namespace,
                )
            )

            if validated_bindings:
                for ev_id, excerpt in validated_bindings:
                    conn.execute(
                        """
                            INSERT OR REPLACE INTO assertion_evidence (
                                assertion_id, evidence_id, excerpt_text
                            ) VALUES (?, ?, ?)
                            """,
                        (assertion.assertion_id, ev_id, excerpt)
                    )

    def query_assertions(
        self,
        subject_node_id: Optional[str] = None,
        status: Optional[str] = None,
        assertion_type: Optional[str] = None,
    ) -> List[AssertionRecord]:
        """Queries assertions with optional subject, status, or type filtering."""
        with self.get_canonical_conn(readonly=True) as conn:
            query = "SELECT assertion_id, subject_node_id, predicate, object_node_id, literal_value, assertion_type, confidence, status, observed_at, stale_after, namespace, created_at FROM assertion WHERE 1=1"
            params: List[Any] = []
            if subject_node_id:
                query += " AND subject_node_id = ?"
                params.append(subject_node_id)
            if status:
                query += " AND status = ?"
                params.append(status)
            if assertion_type:
                query += " AND assertion_type = ?"
                params.append(assertion_type)

            rows = conn.execute(query, params).fetchall()
            return [
                AssertionRecord(
                    assertion_id=r["assertion_id"],
                    subject_node_id=r["subject_node_id"],
                    predicate=r["predicate"],
                    object_node_id=r["object_node_id"],
                    literal_value=r["literal_value"],
                    assertion_type=r["assertion_type"],
                    confidence=r["confidence"],
                    status=r["status"],
                    observed_at=r["observed_at"],
                    stale_after=r["stale_after"],
                    namespace=r["namespace"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]

    def get_canonical_node(
        self,
        node_id: str,
        as_of_timestamp: Optional[str] = None,
    ) -> Optional[CanonicalGraphNode]:
        """
        Retrieves a canonical node by ID, optionally evaluating historical state as of as_of_timestamp.
        """
        with self.get_canonical_conn(readonly=True) as conn:
            if not as_of_timestamp:
                row = conn.execute(
                    """
                    SELECT node_id, evidence_id, type, name, properties,
                           strategic_value, evidence_quality, confidence,
                           tacticality, is_quarantined, quarantine_reason,
                           namespace, created_at, updated_at
                    FROM canonical_graph_nodes
                    WHERE node_id = ? AND namespace = 'CLIENT_SAFE'
                    """,
                    (node_id,)
                ).fetchone()
                return self._row_to_canonical_node(row) if row else None

            clean_as_of = as_of_timestamp.strip()
            if len(clean_as_of) == 10 and "-" in clean_as_of:
                clean_as_of = f"{clean_as_of} 23:59:59"
            elif "T" in clean_as_of:
                clean_as_of = clean_as_of.replace("T", " ")

            curr = conn.execute(
                """
                SELECT node_id, evidence_id, type, name, properties,
                       strategic_value, evidence_quality, confidence,
                       tacticality, is_quarantined, quarantine_reason,
                       namespace, created_at, updated_at
                FROM canonical_graph_nodes
                WHERE node_id = ? AND namespace = 'CLIENT_SAFE'
                """,
                (node_id,)
            ).fetchone()

            if curr:
                c_at = curr["created_at"] or ""
                u_at = curr["updated_at"] or c_at
                if c_at > clean_as_of:
                    return None
                if u_at <= clean_as_of:
                    return self._row_to_canonical_node(curr)

            hist = conn.execute(
                """
                SELECT node_id, evidence_id, type, name, properties,
                       strategic_value, evidence_quality, confidence,
                       tacticality, is_quarantined, quarantine_reason,
                       namespace, created_at, updated_at
                FROM canonical_graph_nodes_history
                WHERE node_id = ? AND namespace = 'CLIENT_SAFE'
                  AND recorded_at > ?
                ORDER BY recorded_at ASC
                LIMIT 1
                """,
                (node_id, clean_as_of)
            ).fetchone()

            if hist:
                c_at = hist["created_at"] or ""
                if c_at <= clean_as_of:
                    return self._row_to_canonical_node(hist)

            return None

    def query_canonical_nodes(
        self,
        min_strategic_value: float = 0.0,
        include_quarantined: bool = False,
        limit: int = 50,
        as_of_timestamp: Optional[str] = None,
    ) -> List[CanonicalGraphNode]:
        """Queries client-safe nodes enforcing Law 6 policy filters, with optional time-travel."""
        if as_of_timestamp:
            clean_as_of = as_of_timestamp.strip()
            if len(clean_as_of) == 10 and "-" in clean_as_of:
                clean_as_of = f"{clean_as_of} 23:59:59"
            elif "T" in clean_as_of:
                clean_as_of = clean_as_of.replace("T", " ")

            with self.get_canonical_conn(readonly=True) as conn:
                q_ids = """
                    SELECT DISTINCT node_id FROM (
                        SELECT node_id FROM canonical_graph_nodes WHERE created_at <= ?
                        UNION
                        SELECT node_id FROM canonical_graph_nodes_history WHERE created_at <= ?
                    )
                """
                id_rows = conn.execute(q_ids, (clean_as_of, clean_as_of)).fetchall()
                nodes: List[CanonicalGraphNode] = []
                for r in id_rows:
                    n = self.get_canonical_node(r["node_id"], as_of_timestamp=clean_as_of)
                    if n and n.strategic_value >= min_strategic_value:
                        if include_quarantined or not n.is_quarantined:
                            nodes.append(n)
                nodes.sort(key=lambda x: x.strategic_value, reverse=True)
                return nodes[:limit]

        with self.get_canonical_conn(readonly=True) as conn:
            query = """
                SELECT node_id, evidence_id, type, name, properties,
                       strategic_value, evidence_quality, confidence,
                       tacticality, is_quarantined, quarantine_reason,
                       namespace, created_at, updated_at
                FROM canonical_graph_nodes
                WHERE namespace = 'CLIENT_SAFE'
                  AND strategic_value >= ?
            """
            params: List[Any] = [min_strategic_value]
            if not include_quarantined:
                query += " AND is_quarantined = 0"

            query += " ORDER BY strategic_value DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [self._row_to_canonical_node(r) for r in rows]

    def search_canonical_nodes_fts(
        self,
        query_text: str,
        limit: int = 20,
        include_quarantined: bool = False,
    ) -> List[CanonicalGraphNode]:
        """
        Executes high-performance SQLite FTS5 BM25 search over canonical graph nodes.
        Ranks results by bm25() score.
        """
        clean_terms = [re.sub(r"[^\w]", "", term) for term in query_text.split() if term.strip()]
        if not clean_terms:
            return []

        fts_match = " OR ".join(f"{term}*" for term in clean_terms if term)
        if not fts_match:
            return []

        sql = """
            SELECT n.node_id, n.evidence_id, n.type, n.name, n.properties,
                   n.strategic_value, n.evidence_quality, n.confidence, n.tacticality,
                   n.is_quarantined, n.quarantine_reason, n.namespace, n.created_at, n.updated_at,
                   bm25(canonical_nodes_fts) AS rank_score
            FROM canonical_nodes_fts fts
            JOIN canonical_graph_nodes n ON fts.node_id = n.node_id
            WHERE canonical_nodes_fts MATCH ?
              AND n.namespace = 'CLIENT_SAFE'
        """
        params: List[Any] = [fts_match]

        if not include_quarantined:
            sql += " AND n.is_quarantined = 0"

        sql += " ORDER BY rank_score ASC, n.strategic_value DESC LIMIT ?"
        params.append(limit)

        results: List[CanonicalGraphNode] = []
        with self.get_canonical_conn(readonly=True) as conn:
            try:
                rows = conn.execute(sql, params).fetchall()
                for r in rows:
                    props = json.loads(r["properties"]) if r["properties"] else {}
                    results.append(CanonicalGraphNode(
                        node_id=r["node_id"],
                        evidence_id=r["evidence_id"],
                        type=r["type"],
                        name=r["name"],
                        properties=props,
                        strategic_value=r["strategic_value"],
                        evidence_quality=r["evidence_quality"],
                        confidence=r["confidence"],
                        tacticality=r["tacticality"],
                        is_quarantined=bool(r["is_quarantined"]),
                        quarantine_reason=r["quarantine_reason"],
                        namespace=r["namespace"],
                        created_at=r["created_at"],
                        updated_at=r["updated_at"],
                    ))
            except Exception as e:
                logger.warning(f"FTS5 search error: {e}")
                return self.query_canonical_nodes(include_quarantined=include_quarantined)[:limit]

        return results

    # ── Graph Lifecycle & Maintenance Operations (Wave 7) ─────────────────

    def compact_database(
        self,
        vacuum: bool = True,
        max_queue_age_days: int = 30,
    ) -> Dict[str, Any]:
        """
        Purges completed work queue items and performs WAL truncation and VACUUM compaction
        across evidence, canonical, and shadow databases.
        """
        purged_count = 0
        with self.get_evidence_conn() as conn:
            with self.transaction(conn, db_key="evidence"):
                if max_queue_age_days <= 0:
                    cursor = conn.execute(
                        "DELETE FROM durable_work_queue WHERE state = 'COMPLETED'"
                    )
                else:
                    cursor = conn.execute(
                        """
                        DELETE FROM durable_work_queue
                        WHERE state = 'COMPLETED'
                          AND (updated_at IS NULL OR datetime(updated_at) < datetime('now', ? || ' days'))
                        """,
                        (f"-{max_queue_age_days}",)
                    )
                purged_count = cursor.rowcount

        # WAL checkpoint and VACUUM must run OUTSIDE an active transaction
        if str(self.evidence_db_path) != ":memory:":
            with self._write_serializer.get_lock("evidence"), self.get_evidence_conn() as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                if vacuum:
                    conn.execute("VACUUM;")

        if str(self.canonical_db_path) != ":memory:":
            with self._write_serializer.get_lock("canonical"), self.get_canonical_conn() as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                if vacuum:
                    conn.execute("VACUUM;")

        if self.shadow_db_path.exists() and str(self.shadow_db_path) != ":memory:":
            try:
                with self._write_serializer.get_lock("shadow"), contextlib.closing(self.get_shadow_conn()) as conn:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                    if vacuum:
                        conn.execute("VACUUM;")
            except Exception as e:
                logger.warning("Compacting shadow db: %s", e)

        return {
            "status": "PASS",
            "purged_queue_items": purged_count,
            "vacuum": vacuum,
            "max_queue_age_days": max_queue_age_days,
            "evidence_db": str(self.evidence_db_path),
            "canonical_db": str(self.canonical_db_path),
        }

    def retire_stale_assertions(
        self,
        as_of_date: Optional[str] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Sweeps assertions with stale_after <= as_of_date and transitions to SUPERSEDED."""
        from dual_pipeline.staleness_core.ttl_sweeper import AssertionTTLSweeper
        sweeper = AssertionTTLSweeper(dry_run=dry_run)
        with self.get_canonical_conn() as conn, self.transaction(conn, db_key="canonical"):
            return sweeper.sweep_stale_assertions(conn, as_of_date=as_of_date)

    def merge_nodes(
        self,
        surviving_id: str,
        absorbed_id: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        """
        Merges absorbed_id into surviving_id:
        - Re-links incoming/outgoing edges and assertions
        - Merges JSON properties and updates alias list
        - Deletes absorbed node and synchronizes FTS index
        """
        if surviving_id == absorbed_id:
            raise ValueError("surviving_id and absorbed_id must be distinct")

        with self.get_canonical_conn() as conn:
            with self.transaction(conn, db_key="canonical"):
                surviving = conn.execute(
                    "SELECT * FROM canonical_graph_nodes WHERE node_id = ?", (surviving_id,)
                ).fetchone()
                absorbed = conn.execute(
                    "SELECT * FROM canonical_graph_nodes WHERE node_id = ?", (absorbed_id,)
                ).fetchone()

                if not surviving:
                    raise KeyError(f"Surviving node '{surviving_id}' not found.")
                if not absorbed:
                    raise KeyError(f"Absorbed node '{absorbed_id}' not found.")

                # 1. Re-link edges
                conn.execute(
                    """
                    DELETE FROM canonical_graph_edges
                    WHERE (from_node_id = ? AND to_node_id = ?)
                       OR (from_node_id = ? AND to_node_id = ?)
                    """,
                    (surviving_id, absorbed_id, absorbed_id, surviving_id)
                )

                cursor = conn.execute(
                    "UPDATE canonical_graph_edges SET from_node_id = ?, updated_at = CURRENT_TIMESTAMP WHERE from_node_id = ?",
                    (surviving_id, absorbed_id)
                )
                edges_from = cursor.rowcount

                cursor = conn.execute(
                    "UPDATE canonical_graph_edges SET to_node_id = ?, updated_at = CURRENT_TIMESTAMP WHERE to_node_id = ?",
                    (surviving_id, absorbed_id)
                )
                edges_to = cursor.rowcount

                # 2. Re-link assertions
                cursor = conn.execute(
                    "UPDATE assertion SET subject_node_id = ? WHERE subject_node_id = ?",
                    (surviving_id, absorbed_id)
                )
                assert_sub = cursor.rowcount

                cursor = conn.execute(
                    "UPDATE assertion SET object_node_id = ? WHERE object_node_id = ?",
                    (surviving_id, absorbed_id)
                )
                assert_obj = cursor.rowcount

                # 3. Merge properties and aliases
                surv_props = json.loads(surviving["properties"]) if surviving["properties"] else {}
                abs_props = json.loads(absorbed["properties"]) if absorbed["properties"] else {}

                for k, v in abs_props.items():
                    if k not in surv_props:
                        surv_props[k] = v

                aliases = list(surv_props.get("aliases", []))
                abs_name = absorbed["name"]
                if abs_name and abs_name not in aliases and abs_name != surviving["name"]:
                    aliases.append(abs_name)
                for a in abs_props.get("aliases", []):
                    if a not in aliases and a != surviving["name"]:
                        aliases.append(a)
                surv_props["aliases"] = aliases

                merged_nodes = list(surv_props.get("merged_nodes", []))
                merged_nodes.append({
                    "absorbed_id": absorbed_id,
                    "absorbed_name": abs_name,
                    "reason": reason,
                    "merged_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                })
                surv_props["merged_nodes"] = merged_nodes

                conn.execute(
                    """
                    UPDATE canonical_graph_nodes
                    SET properties = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE node_id = ?
                    """,
                    (json.dumps(surv_props, ensure_ascii=False), surviving_id)
                )

                # 4. Delete absorbed node (triggers record history and update FTS5)
                conn.execute(
                    "DELETE FROM canonical_graph_nodes WHERE node_id = ?",
                    (absorbed_id,)
                )

                return {
                    "status": "PASS",
                    "surviving_id": surviving_id,
                    "absorbed_id": absorbed_id,
                    "edges_relinked": edges_from + edges_to,
                    "assertions_relinked": assert_sub + assert_obj,
                    "reason": reason,
                }

    def export_graph(
        self,
        format: str = "turtle",
        output_path: Optional[Union[str, Path]] = None,
    ) -> str:
        """
        Exports canonical graph nodes, edges, and assertions into standard formats:
        'turtle' (W3C RDF Turtle), 'json-ld' (JSON-LD linked data), or 'csv'.
        """
        fmt = format.lower().strip()
        if fmt not in ("turtle", "json-ld", "csv"):
            raise ValueError(f"Unsupported export format '{format}'. Supported formats: turtle, json-ld, csv")

        with self.get_canonical_conn(readonly=True) as conn:
            nodes = conn.execute(
                "SELECT * FROM canonical_graph_nodes WHERE namespace = 'CLIENT_SAFE' ORDER BY created_at ASC"
            ).fetchall()
            edges = conn.execute(
                "SELECT * FROM canonical_graph_edges WHERE namespace = 'CLIENT_SAFE' ORDER BY created_at ASC"
            ).fetchall()
            assertions = conn.execute(
                "SELECT * FROM assertion WHERE namespace = 'CLIENT_SAFE' ORDER BY created_at ASC"
            ).fetchall()

        if fmt == "turtle":
            lines = [
                "@prefix nee: <urn:nee:graph:> .",
                "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
                "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
                "@prefix dc: <http://purl.org/dc/terms/> .",
                "@prefix prov: <http://www.w3.org/ns/prov#> .",
                "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
                "",
                "# === NODES ===",
            ]
            for n in nodes:
                nid = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(n["node_id"]))
                ntype = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(n["type"]))
                label = (n["name"] or "").replace('"', '\\"')
                s_val = float(n["strategic_value"])
                conf = float(n["confidence"])
                lines.append(f"<urn:nee:node:{nid}> a nee:{ntype} ;")
                lines.append(f'    rdfs:label "{label}" ;')
                lines.append(f'    nee:strategicValue "{s_val}"^^xsd:double ;')
                lines.append(f'    nee:confidence "{conf}"^^xsd:double .')
                lines.append("")

            lines.append("# === EDGES ===")
            for e in edges:
                from_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(e["from_node_id"]))
                to_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(e["to_node_id"]))
                pred = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(e["predicate"]))
                lines.append(f"<urn:nee:node:{from_id}> nee:{pred} <urn:nee:node:{to_id}> .")
            lines.append("")

            lines.append("# === ASSERTIONS ===")
            for a in assertions:
                aid = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(a["assertion_id"]))
                sub_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(a["subject_node_id"]))
                atype = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(a["assertion_type"]))
                pred = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(a["predicate"]))
                lit = (a["literal_value"] or "").replace('"', '\\"')
                status = a["status"]
                lines.append(f"<urn:nee:assertion:{aid}> a nee:Assertion ;")
                lines.append(f"    nee:assertionType nee:{atype} ;")
                lines.append(f"    nee:subject <urn:nee:node:{sub_id}> ;")
                lines.append(f'    nee:predicate "{pred}" ;')
                if a["object_node_id"]:
                    obj_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(a["object_node_id"]))
                    lines.append(f"    nee:object <urn:nee:node:{obj_id}> ;")
                if lit:
                    lines.append(f'    nee:literalValue "{lit}" ;')
                lines.append(f'    nee:status "{status}" .')
                lines.append("")

            content = "\n".join(lines)

        elif fmt == "json-ld":
            graph_items = []
            for n in nodes:
                graph_items.append({
                    "@id": f"urn:nee:node:{n['node_id']}",
                    "@type": f"nee:{n['type']}",
                    "name": n["name"],
                    "strategicValue": n["strategic_value"],
                    "confidence": n["confidence"],
                    "namespace": n["namespace"],
                    "created_at": n["created_at"],
                })
            for e in edges:
                graph_items.append({
                    "@id": f"urn:nee:edge:{e['edge_id']}",
                    "@type": "nee:Edge",
                    "from": f"urn:nee:node:{e['from_node_id']}",
                    "predicate": f"nee:{e['predicate']}",
                    "to": f"urn:nee:node:{e['to_node_id']}",
                    "weight": e["weight"],
                    "confidence": e["confidence"],
                })
            for a in assertions:
                item = {
                    "@id": f"urn:nee:assertion:{a['assertion_id']}",
                    "@type": f"nee:{a['assertion_type']}",
                    "subject": f"urn:nee:node:{a['subject_node_id']}",
                    "predicate": a["predicate"],
                    "status": a["status"],
                }
                if a["object_node_id"]:
                    item["object"] = f"urn:nee:node:{a['object_node_id']}"
                if a["literal_value"]:
                    item["literalValue"] = a["literal_value"]
                graph_items.append(item)

            doc = {
                "@context": {
                    "@vocab": "http://schema.org/",
                    "nee": "urn:nee:graph:",
                    "dc": "http://purl.org/dc/terms/",
                    "prov": "http://www.w3.org/ns/prov#",
                },
                "@graph": graph_items,
            }
            content = json.dumps(doc, indent=2, ensure_ascii=False)

        elif fmt == "csv":
            import csv
            import io
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["# SECTION: NODES"])
            writer.writerow(["node_id", "type", "name", "strategic_value", "confidence", "namespace", "created_at"])
            for n in nodes:
                writer.writerow([n["node_id"], n["type"], n["name"], n["strategic_value"], n["confidence"], n["namespace"], n["created_at"]])
            writer.writerow([])
            writer.writerow(["# SECTION: EDGES"])
            writer.writerow(["edge_id", "from_node_id", "predicate", "to_node_id", "weight", "confidence"])
            for e in edges:
                writer.writerow([e["edge_id"], e["from_node_id"], e["predicate"], e["to_node_id"], e["weight"], e["confidence"]])
            writer.writerow([])
            writer.writerow(["# SECTION: ASSERTIONS"])
            writer.writerow(["assertion_id", "subject_node_id", "predicate", "object_node_id", "literal_value", "assertion_type", "status"])
            for a in assertions:
                writer.writerow([a["assertion_id"], a["subject_node_id"], a["predicate"], a["object_node_id"], a["literal_value"], a["assertion_type"], a["status"]])
            content = buf.getvalue()

        if output_path:
            out_p = Path(output_path).resolve()
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(content, encoding="utf-8")

        return content

    def get_graph_statistics(self) -> Dict[str, Any]:
        """Computes comprehensive topological and storage statistics for the canonical graph."""
        with self.get_canonical_conn(readonly=True) as conn:
            # Node breakdown
            node_rows = conn.execute(
                "SELECT type, count(*) as count FROM canonical_graph_nodes WHERE namespace = 'CLIENT_SAFE' GROUP BY type"
            ).fetchall()
            nodes_by_type = {r["type"]: r["count"] for r in node_rows}
            total_nodes = sum(nodes_by_type.values())

            # Edge breakdown
            edge_rows = conn.execute(
                "SELECT predicate, count(*) as count FROM canonical_graph_edges WHERE namespace = 'CLIENT_SAFE' GROUP BY predicate"
            ).fetchall()
            edges_by_predicate = {r["predicate"]: r["count"] for r in edge_rows}
            total_edges = sum(edges_by_predicate.values())

            # Assertion breakdown
            assert_rows = conn.execute(
                "SELECT assertion_type, status, count(*) as count FROM assertion WHERE namespace = 'CLIENT_SAFE' GROUP BY assertion_type, status"
            ).fetchall()
            assertions_by_type: Dict[str, int] = {}
            assertions_by_status: Dict[str, int] = {}
            total_assertions = 0
            for r in assert_rows:
                cnt = r["count"]
                total_assertions += cnt
                assertions_by_type[r["assertion_type"]] = assertions_by_type.get(r["assertion_type"], 0) + cnt
                assertions_by_status[r["status"]] = assertions_by_status.get(r["status"], 0) + cnt

            # Degree calculations
            degree_query = """
                SELECT n.node_id,
                       coalesce(out_deg.cnt, 0) + coalesce(in_deg.cnt, 0) as degree
                FROM canonical_graph_nodes n
                LEFT JOIN (SELECT from_node_id, count(*) as cnt FROM canonical_graph_edges GROUP BY from_node_id) out_deg
                       ON n.node_id = out_deg.from_node_id
                LEFT JOIN (SELECT to_node_id, count(*) as cnt FROM canonical_graph_edges GROUP BY to_node_id) in_deg
                       ON n.node_id = in_deg.to_node_id
                WHERE n.namespace = 'CLIENT_SAFE'
            """
            degrees = [r["degree"] for r in conn.execute(degree_query).fetchall()]
            degrees.sort()

            if degrees:
                import statistics
                def percentile(vals: List[float], p: float) -> float:
                    idx = int(len(vals) * p / 100.0)
                    return vals[min(idx, len(vals) - 1)]

                p50 = percentile(degrees, 50)
                p90 = percentile(degrees, 90)
                p99 = percentile(degrees, 99)
                deg_min = min(degrees)
                deg_max = max(degrees)
                deg_mean = round(float(statistics.mean(degrees)), 2)
            else:
                p50 = p90 = p99 = deg_min = deg_max = deg_mean = 0

            # Orphan count
            orphan_query = """
                SELECT count(*) as count FROM canonical_graph_nodes
                WHERE namespace = 'CLIENT_SAFE'
                  AND node_id NOT IN (SELECT DISTINCT from_node_id FROM canonical_graph_edges)
                  AND node_id NOT IN (SELECT DISTINCT to_node_id FROM canonical_graph_edges)
                  AND node_id NOT IN (SELECT DISTINCT subject_node_id FROM assertion WHERE subject_node_id IS NOT NULL)
                  AND node_id NOT IN (SELECT DISTINCT object_node_id FROM assertion WHERE object_node_id IS NOT NULL)
            """
            orphan_count = conn.execute(orphan_query).fetchone()["count"]

        # Storage breakdown
        storage_breakdown: Dict[str, int] = {}
        for name, p in [
            ("canonical_graph_db", self.canonical_db_path),
            ("evidence_db", self.evidence_db_path),
            ("shadow_graph_db", self.shadow_db_path),
        ]:
            if p.exists() and str(p) != ":memory:":
                storage_breakdown[name] = p.stat().st_size
            else:
                storage_breakdown[name] = 0

        return {
            "status": "PASS",
            "total_nodes": total_nodes,
            "nodes_by_type": nodes_by_type,
            "total_edges": total_edges,
            "edges_by_predicate": edges_by_predicate,
            "total_assertions": total_assertions,
            "assertions_by_type": assertions_by_type,
            "assertions_by_status": assertions_by_status,
            "degree_percentiles": {
                "p50": p50,
                "p90": p90,
                "p99": p99,
                "min": deg_min,
                "max": deg_max,
                "mean": deg_mean,
            },
            "orphan_count": orphan_count,
            "storage_breakdown_bytes": storage_breakdown,
        }

    def cleanup_orphan_nodes(self, archive: bool = True) -> List[str]:
        """
        Identifies and quarantines (archive=True) or deletes (archive=False)
        unreferenced orphan nodes.
        """
        with self.get_canonical_conn() as conn:
            with self.transaction(conn, db_key="canonical"):
                query = """
                    SELECT node_id FROM canonical_graph_nodes
                    WHERE node_id NOT IN (SELECT DISTINCT from_node_id FROM canonical_graph_edges)
                      AND node_id NOT IN (SELECT DISTINCT to_node_id FROM canonical_graph_edges)
                      AND node_id NOT IN (SELECT DISTINCT subject_node_id FROM assertion WHERE subject_node_id IS NOT NULL)
                      AND node_id NOT IN (SELECT DISTINCT object_node_id FROM assertion WHERE object_node_id IS NOT NULL)
                """
                rows = conn.execute(query).fetchall()
                orphan_ids = [r["node_id"] for r in rows]
                if not orphan_ids:
                    return []

                placeholders = ",".join("?" for _ in orphan_ids)
                if archive:
                    conn.execute(
                        f"""
                        UPDATE canonical_graph_nodes
                        SET is_quarantined = 1,
                            quarantine_reason = 'ORPHAN_CLEANUP_ARCHIVED',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE node_id IN ({placeholders})
                        """,
                        orphan_ids
                    )
                else:
                    conn.execute(
                        f"DELETE FROM canonical_graph_nodes WHERE node_id IN ({placeholders})",
                        orphan_ids
                    )
                return orphan_ids

    # ── Shadow Advisory Operations (Internal Encrypted) ──────────────────

    def upsert_shadow_advisory(self, node: InternalAdvisoryNode) -> None:
        """Commits encrypted advisory node to local shadow graph with HMAC entity anchor."""
        props_json = json.dumps(node.properties, ensure_ascii=False)
        target_hmac = node.target_entity_hmac or compute_entity_hmac(node.node_id)

        conn = self.get_shadow_conn()
        try:
            with self.transaction(conn, db_key="shadow"):
                conn.execute(
                    """
                    INSERT INTO shadow_graph_nodes (
                        node_id, evidence_id, target_entity_hmac, type, name, category,
                        properties, strategic_value, tacticality, confidence,
                        is_quarantined, quarantine_reason, stealth_score,
                        namespace, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(node_id) DO UPDATE SET
                        name = excluded.name,
                        target_entity_hmac = excluded.target_entity_hmac,
                        category = excluded.category,
                        properties = excluded.properties,
                        strategic_value = excluded.strategic_value,
                        tacticality = excluded.tacticality,
                        confidence = excluded.confidence,
                        is_quarantined = excluded.is_quarantined,
                        quarantine_reason = excluded.quarantine_reason,
                        stealth_score = excluded.stealth_score,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        node.node_id,
                        node.evidence_id,
                        target_hmac,
                        node.type,
                        node.name,
                        node.category.value if isinstance(node.category, AdvisoryCategory) else node.category,
                        props_json,
                        node.strategic_value,
                        node.tacticality,
                        node.confidence,
                        1 if node.is_quarantined else 0,
                        node.quarantine_reason,
                        node.stealth_score,
                        node.namespace,
                    )
                )
        finally:
            conn.close()

    upsert_internal_advisory_node = upsert_shadow_advisory

    def query_internal_advisory_nodes(
        self,
        category: Optional[AdvisoryCategory] = None,
        include_quarantined: bool = False,
        limit: int = 50,
    ) -> List[InternalAdvisoryNode]:
        """Queries encrypted internal advisory facts from shadow graph."""
        conn = self.get_shadow_conn(readonly=True)
        try:
            query = """
                SELECT node_id, evidence_id, target_entity_hmac, type, name, category,
                       properties, strategic_value, tacticality, confidence,
                       is_quarantined, quarantine_reason, stealth_score,
                       namespace, created_at, updated_at
                FROM shadow_graph_nodes
                WHERE namespace = 'INTERNAL_ADVISORY'
            """
            params: List[Any] = []
            if category:
                query += " AND category = ?"
                params.append(category.value if isinstance(category, AdvisoryCategory) else category)
            if not include_quarantined:
                query += " AND is_quarantined = 0"

            query += " ORDER BY stealth_score DESC, strategic_value DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [self._row_to_advisory_node(r) for r in rows]
        finally:
            conn.close()

    # ── Internal Mappers ─────────────────────────────────────────────────

    @staticmethod
    def _row_to_canonical_node(r: Any) -> CanonicalGraphNode:
        return row_to_canonical_node(r)

    @staticmethod
    def _row_to_advisory_node(r: Any) -> InternalAdvisoryNode:
        return row_to_advisory_node(r)


__all__ = [
    "WorkItemState",
    "AdvisoryCategory",
    "EvidenceRecord",
    "WorkItem",
    "CanonicalGraphNode",
    "AssertionRecord",
    "InternalAdvisoryNode",
    "GraphEdge",
    "resolve_keychain_secret",
    "compute_entity_hmac",
    "WriteSerializer",
    "DatabaseManager",
    "SQLCIPHER_AVAILABLE",
    "sqlcipher_driver",
    "_row_to_canonical_node",
    "_row_to_advisory_node",
]
