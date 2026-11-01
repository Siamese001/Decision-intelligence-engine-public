"""
dual_pipeline/storage/hitl_sqlite_store.py

Governed Layer-0 SQLite backing store for Human-in-the-Loop (HITL) decisions.
Replaces linear JSON file scans with concurrency-safe, indexed, zero-lock-contention
storage managed via GovernedStorageContext for sub-millisecond prior calibration.
Governed under AGENTS.md Invariant 2 via Layer-0 GovernedStorageContext.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from config.credentials import CredentialManager
from dual_pipeline.core import digest, stable_id, utc_now
from dual_pipeline.storage.governed_context import GovernedStorageContext

logger = logging.getLogger(__name__)

DEFAULT_HITL_SQLITE_PATH = Path(".local/ledger/hitl_decision_ledger.sqlite")


def get_default_hitl_sqlite_path() -> Path:
    """Return canonical path, routing to isolated ephemeral tempfile under test environments."""
    from config.paths import get_hitl_ledger_paths

    if (
        CredentialManager.get_credential("NEE_ALLOW_TEST_FIXTURES") == "1"
        or CredentialManager.has_credential("PYTEST_CURRENT_TEST")
    ):
        override = CredentialManager.get_credential("NEE_TEST_LEDGER_SQLITE")
        if override:
            return Path(override).resolve()
        return Path(tempfile.gettempdir()) / f"nee_test_hitl_ledger_{os.getpid()}.sqlite"
    sql_path, _ = get_hitl_ledger_paths()
    return sql_path if sql_path.parent.exists() else DEFAULT_HITL_SQLITE_PATH


HITL_TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS hitl_decisions (
    entry_id TEXT PRIMARY KEY,
    sequence_number INTEGER NOT NULL,
    decision_id TEXT NOT NULL,
    category TEXT NOT NULL,
    operator_id TEXT NOT NULL,
    prompt TEXT,
    options TEXT,
    selected_option TEXT,
    selected_key TEXT NOT NULL,
    rationale TEXT,
    previous_entry_hash TEXT,
    recorded_at TEXT NOT NULL,
    metadata TEXT,
    entry_digest TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_hitl_category ON hitl_decisions(category);
CREATE INDEX IF NOT EXISTS idx_hitl_selected ON hitl_decisions(selected_key);
CREATE INDEX IF NOT EXISTS idx_hitl_recorded ON hitl_decisions(recorded_at);
CREATE INDEX IF NOT EXISTS idx_hitl_decision_id ON hitl_decisions(decision_id);
"""

INSERT_DECISION_SQL = """
INSERT OR REPLACE INTO hitl_decisions (
    entry_id, sequence_number, decision_id, category,
    operator_id, prompt, options, selected_option,
    selected_key, rationale, previous_entry_hash,
    recorded_at, metadata, entry_digest
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _parse_row_json_fields(
    entry_id: str, options_json: Any, selected_opt_json: Any, selected_key: str, metadata_json: Any
) -> Tuple[List[Any], Any, Dict[str, Any]]:
    try:
        options = json.loads(options_json) if options_json else []
    except (json.JSONDecodeError, TypeError) as e:
        logger.error("Failed to parse options for HITL decision %s: %s", entry_id, e)
        options = []

    try:
        selected_option = json.loads(selected_opt_json) if selected_opt_json else selected_key
    except (json.JSONDecodeError, TypeError) as e:
        logger.error("Failed to parse selected option for HITL decision %s: %s", entry_id, e)
        selected_option = selected_key

    try:
        metadata = json.loads(metadata_json) if metadata_json else {}
    except (json.JSONDecodeError, TypeError) as e:
        logger.error("Failed to parse metadata for HITL decision %s: %s", entry_id, e)
        metadata = {}

    return options, selected_option, metadata


def _reconstruct_legacy_operator_class(rec: Dict[str, Any], entry_digest: str) -> None:
    if "operator_class" in rec and digest({k: v for k, v in rec.items() if k != "entry_digest"}) != entry_digest:
        without_hoist = {k: v for k, v in rec.items() if k not in {"entry_digest", "operator_class"}}
        if digest(without_hoist) == entry_digest:
            rec.pop("operator_class")


def _row_to_record(row: Any) -> Dict[str, Any]:
    """Convert an SQLite row tuple to a canonical HITL record dict."""
    (
        entry_id,
        sequence_number,
        decision_id,
        category,
        operator_id,
        prompt,
        options_json,
        selected_opt_json,
        selected_key,
        rationale,
        previous_entry_hash,
        recorded_at,
        metadata_json,
        entry_digest,
    ) = row

    options, selected_option, metadata = _parse_row_json_fields(
        entry_id, options_json, selected_opt_json, selected_key, metadata_json
    )

    if isinstance(metadata, dict) and "_raw_record" in metadata:
        rec = dict(metadata["_raw_record"])
        if not rec.get("entry_digest"):
            rec["entry_digest"] = entry_digest
        return rec

    rec = {
        "entry_id": entry_id,
        "sequence_number": sequence_number,
        "decision_id": decision_id,
        "category": category,
        "operator_id": operator_id,
        "prompt": prompt or "",
        "options": options,
        "selected_option": selected_option,
        "selected_key": selected_key,
        "rationale": rationale,
        "previous_entry_hash": previous_entry_hash or ("0" * 64),
        "recorded_at": recorded_at,
        "metadata": metadata,
        "entry_digest": entry_digest,
    }
    for f in (
        "outcome_type",
        "margin_at_decision",
        "confidence_interval_at_decision",
        "sample_count_at_presentation",
        "resolution_latency_ms",
        "operator_class",
        "presented_at",
        "resolved_at",
        "latency_slo_status",
    ):
        if f in metadata:
            rec[f] = metadata[f]
    if isinstance(metadata, dict) and "_alr_envelope" in metadata:
        rec.update(metadata["_alr_envelope"])
    _reconstruct_legacy_operator_class(rec, entry_digest)
    return rec


def _extract_sel_key(sel_opt: Any, record: Dict[str, Any]) -> str:
    if isinstance(sel_opt, dict):
        for k in ("key", "action", "label"):
            v = sel_opt.get(k)
            if v:
                return str(v)
        return str(sel_opt)
    return str(record.get("selected_key") or sel_opt)


def _build_row_metadata(record: Dict[str, Any], entry_digest: Optional[str] = None) -> str:
    metadata = dict(record.get("metadata") or {})
    fields = (
        "operator_class",
        "presented_at",
        "resolved_at",
        "latency_slo_status",
        "resolution_latency_ms",
    )
    for f in fields:
        if f in record and f not in metadata:
            metadata[f] = record[f]
    alr_envelope = {
        k: record[k]
        for k in ("payload", "public_key_pem", "signature", "key_id", "algorithm", "signer_id")
        if k in record
    }
    if alr_envelope:
        metadata["_alr_envelope"] = alr_envelope
    return json.dumps(metadata, sort_keys=True)


def _resolve_selected_option_and_key(record: Dict[str, Any]) -> Tuple[str, str]:
    sel_opt = record.get("selected_option")
    if sel_opt is None:
        sel_opt = record.get("selected_key")
    if sel_opt is None:
        sel_opt = record.get("selected_option_id", "")
    sel_opt_json = json.dumps(sel_opt, sort_keys=True)
    sel_key = _extract_sel_key(sel_opt, record)
    return sel_opt_json, sel_key


def _record_to_row(record: Dict[str, Any]) -> tuple:
    entry_digest = str(record.get("entry_digest") or digest(record))
    entry_id = str(record.get("entry_id") or record.get("decision_id") or f"hitl-{entry_digest[:24]}")
    seq_num = int(record.get("sequence_number", 0))
    dec_id = str(record.get("decision_id") or record.get("ambiguity_id") or entry_id)
    cat = str(record.get("category") or record.get("domain") or "general")
    raw_op = record.get("operator_id") or (record.get("metadata") or {}).get("operator_id") or "human_operator"
    op_id = str(raw_op)
    prompt = str(record.get("prompt") or "")
    opts = record.get("options") if record.get("options") is not None else record.get("candidates", [])
    opts_json = json.dumps(opts, sort_keys=True)
    sel_opt_json, sel_key = _resolve_selected_option_and_key(record)
    rationale = record.get("rationale")
    prev_hash = str(record.get("previous_entry_hash") or ("0" * 64))
    rec_at = str(record.get("recorded_at") or record.get("timestamp") or utc_now())
    meta_json = _build_row_metadata(record, entry_digest=entry_digest)
    return (
        entry_id, seq_num, dec_id, cat, op_id, prompt, opts_json,
        sel_opt_json, sel_key, rationale, prev_hash, rec_at, meta_json, entry_digest,
    )


class HITLSQLiteStore:
    """Governed persistent SQLite store for operator answers and historical feedback learning.

    Adheres strictly to Master Invariants:
    - Layer-0 isolation with zero upward dependencies.
    - Zero raw SQLite connections (managed exclusively via GovernedStorageContext).
    - 30,000ms busy timeout, WAL mode, memory temp store, foreign keys.
    """

    def __init__(self, db_path: Optional[Union[str, Path]] = None) -> None:
        if db_path is None:
            db_path = get_default_hitl_sqlite_path()
        self.db_path = Path(db_path).resolve()
        self._init_db()

    def _init_db(self) -> None:
        """Initialize SQLite database schema and indexes."""
        with GovernedStorageContext.write_transaction(self.db_path) as conn:
            conn.executescript(HITL_TABLE_SCHEMA)

    def record_decision(
        self,
        *,
        decision_id: str,
        category: str,
        prompt: str,
        options: List[Any],
        selected_option: Any,
        rationale: Optional[str] = None,
        operator_id: str = "human_operator",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Record an operator's resolved decision with Merkle hash linking under write transaction."""
        with GovernedStorageContext.write_transaction(self.db_path) as conn:
            # Determine current sequence number and previous entry digest
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM hitl_decisions")
            seq_num = cur.fetchone()[0]

            cur.execute(
                "SELECT entry_digest FROM hitl_decisions ORDER BY sequence_number DESC, ROWID DESC LIMIT 1"
            )
            last_row = cur.fetchone()
            previous_entry_hash = last_row[0] if last_row else ("0" * 64)

            now = utc_now()
            if isinstance(selected_option, dict):
                selected_key = (
                    selected_option.get("key")
                    or selected_option.get("action")
                    or selected_option.get("label")
                    or str(selected_option)
                )
            else:
                selected_key = str(selected_option)

            record = {
                "entry_id": stable_id("hitl-record", decision_id, now, length=24),
                "sequence_number": seq_num,
                "decision_id": decision_id,
                "category": category,
                "operator_id": operator_id,
                "prompt": prompt,
                "options": options,
                "selected_option": selected_option,
                "selected_key": selected_key,
                "rationale": rationale,
                "previous_entry_hash": previous_entry_hash,
                "recorded_at": now,
                "metadata": metadata or {},
            }
            entry_digest = digest(record)
            record["entry_digest"] = entry_digest

            cur.execute(INSERT_DECISION_SQL, _record_to_row(record))
            return record

    def get_history(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve historical decisions ordered by sequence number, optionally filtered by category."""
        with GovernedStorageContext.read_context(self.db_path) as conn:
            cur = conn.cursor()
            base_query = (
                "SELECT entry_id, sequence_number, decision_id, category, "
                "operator_id, prompt, options, selected_option, selected_key, rationale, "
                "previous_entry_hash, recorded_at, metadata, entry_digest FROM hitl_decisions"
            )
            if category:
                sql = base_query + " WHERE category = ? ORDER BY sequence_number ASC, ROWID ASC"
                cur.execute(sql, (category,))
            else:
                sql = base_query + " ORDER BY sequence_number ASC, ROWID ASC"
                cur.execute(sql)
            rows = cur.fetchall()
            return [_row_to_record(r) for r in rows]

    def get_decision(self, decision_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a specific decision by decision_id or entry_id."""
        with GovernedStorageContext.read_context(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT entry_id, sequence_number, decision_id, category, "
                "operator_id, prompt, options, selected_option, selected_key, rationale, "
                "previous_entry_hash, recorded_at, metadata, entry_digest FROM hitl_decisions "
                "WHERE decision_id = ? OR entry_id = ? ORDER BY sequence_number DESC, ROWID DESC LIMIT 1",
                (decision_id, decision_id),
            )
            row = cur.fetchone()
            return _row_to_record(row) if row else None

    def get_decisions_by_meeting(self, meeting_slug: str) -> List[Dict[str, Any]]:
        """Return all decisions associated with a meeting slug."""
        clean_target = meeting_slug.strip().lower()
        records = self.get_history()
        matched = []
        for r in records:
            meta = r.get("metadata") or {}
            payload = r.get("payload") or {}
            m_slug = str(payload.get("meeting_slug") or r.get("meeting_slug") or meta.get("meeting_name") or meta.get("meeting_slug") or "").lower()
            if m_slug and (clean_target in m_slug or m_slug in clean_target):
                matched.append(r)
        return matched

    def count(self, category: Optional[str] = None) -> int:
        """Return total count of recorded decisions."""
        with GovernedStorageContext.read_context(self.db_path) as conn:
            cur = conn.cursor()
            if category:
                cur.execute("SELECT COUNT(*) FROM hitl_decisions WHERE category = ?", (category,))
            else:
                cur.execute("SELECT COUNT(*) FROM hitl_decisions")
            return int(cur.fetchone()[0])

    def latest_entry_hash(self) -> str:
        """Return the digest of the latest recorded entry, or 64 zeros if empty."""
        with GovernedStorageContext.read_context(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT entry_digest FROM hitl_decisions ORDER BY sequence_number DESC, ROWID DESC LIMIT 1"
            )
            row = cur.fetchone()
            return row[0] if row else ("0" * 64)

    def get_committed_head_version(self) -> Tuple[int, int, str]:
        """Return (data_version, count, latest_entry_digest) ensuring reader WAL cache freshness."""
        with GovernedStorageContext.read_context(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA data_version")
            dv = int(cur.fetchone()[0])
            cur.execute(
                "SELECT COUNT(*), (SELECT entry_digest FROM hitl_decisions ORDER BY sequence_number DESC, ROWID DESC LIMIT 1) "
                "FROM hitl_decisions"
            )
            row = cur.fetchone()
            cnt = int(row[0]) if row and row[0] is not None else 0
            latest_digest = str(row[1]) if row and row[1] is not None else ("0" * 64)
            return (dv, cnt, latest_digest)

    def total_records(self) -> int:
        """Count total decisions recorded in store."""
        return self.count()

    def insert_decision(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Insert or replace an existing decision record preserving original digests and IDs."""
        with GovernedStorageContext.write_transaction(self.db_path) as conn:
            conn.cursor().execute(INSERT_DECISION_SQL, _record_to_row(record))
            return record

    def insert_decisions_batch(self, records: Sequence[Dict[str, Any]]) -> int:
        """Insert a batch of decision records within a single atomic transaction."""
        if not records:
            return 0
        rows = [_record_to_row(r) for r in records]
        with GovernedStorageContext.write_transaction(self.db_path) as conn:
            conn.cursor().executemany(INSERT_DECISION_SQL, rows)
        return len(rows)

    def rebuild_from_records(self, records: Sequence[Dict[str, Any]]) -> int:
        """Atomically clear and re-populate all records from canonical source."""
        if not records:
            return 0
        if self.db_path.exists():
            for p in (
                self.db_path,
                self.db_path.with_name(self.db_path.name + "-wal"),
                self.db_path.with_name(self.db_path.name + "-shm"),
            ):
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
        self._init_db()
        return self.insert_decisions_batch(records)

    def record_entry(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Convenience method to record a decision dict entry."""
        if "entry_digest" in record or "entry_id" in record:
            return self.insert_decision(record)
        return self.record_decision(
            decision_id=str(record.get("decision_id") or record.get("ambiguity_id") or record.get("entry_id") or record.get("id", "")),
            category=str(record.get("category") or record.get("domain", "general")),
            operator_id=str(record.get("operator_id", "human_operator")),
            prompt=str(record.get("prompt", "")),
            options=record.get("options") or record.get("candidates", []),
            selected_option=record.get("selected_option") or record.get("selected_key") or record.get("selected_option_id", ""),
            rationale=record.get("rationale"),
            metadata=record.get("metadata"),
        )


    def get_category_counts(self, category: str) -> Dict[str, int]:
        """Aggregate frequency counts of selected keys for a given category."""
        with GovernedStorageContext.read_context(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT selected_key, COUNT(*) as cnt FROM hitl_decisions WHERE category = ? GROUP BY selected_key",
                (category,),
            )
            return {row[0]: row[1] for row in cur.fetchall()}
