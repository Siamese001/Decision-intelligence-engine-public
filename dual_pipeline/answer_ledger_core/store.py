"""
dual_pipeline/answer_ledger_core/store.py

SQLite storage manager and cryptographic hash chain ledger for AnswerRecord and AnswerFeedback.
Ensures zero contention (WAL mode, busy_timeout=30000), serialized single-writer access, and best-effort live execution.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, List, Optional, Tuple

from storage.connection import create_sqlite_conn, sqlite3
from dual_pipeline.answer_ledger_core.models import (
    GENESIS_PREV_DIGEST,
    AnswerFeedback,
    AnswerRecord,
)

logger = logging.getLogger("dual_pipeline.answer_ledger")

_TABLE_RECORDS_SQL = (
    "CREATE TABLE IF NOT EXISTS answer_records ("
    "row_id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "answer_id TEXT UNIQUE NOT NULL, "
    "pipeline TEXT NOT NULL, "
    "session_id TEXT, "
    "intent_or_type TEXT NOT NULL, "
    "context_digest TEXT NOT NULL, "
    "retrieval_seeds TEXT NOT NULL, "
    "context_snapshot TEXT NOT NULL, "
    "cited_assertion_ids TEXT NOT NULL, "
    "rigor TEXT NOT NULL, "
    "response_text TEXT NOT NULL, "
    "graph_sha256 TEXT, "
    "generation TEXT, "
    "parameter_snapshot_digest TEXT, "
    "latency_ms REAL, "
    "retrieve_latency_ms REAL, "
    "verify_latency_ms REAL, "
    "render_latency_ms REAL, "
    "stage_latencies_ms TEXT, "
    "model_id TEXT, "
    "timestamp TEXT NOT NULL, "
    "contract_id TEXT NOT NULL, "
    "schema_version TEXT NOT NULL, "
    "prev_record_digest TEXT NOT NULL, "
    "record_digest TEXT NOT NULL);"
)

_TABLE_FEEDBACK_SQL = (
    "CREATE TABLE IF NOT EXISTS answer_feedback ("
    "feedback_id TEXT PRIMARY KEY, "
    "answer_id TEXT NOT NULL, "
    "answer_digest TEXT NOT NULL, "
    "feedback_type TEXT NOT NULL, "
    "rating TEXT NOT NULL, "
    "origin TEXT NOT NULL, "
    "note TEXT, "
    "weight REAL, "
    "timestamp TEXT NOT NULL, "
    "contract_id TEXT NOT NULL, "
    "schema_version TEXT NOT NULL, "
    "signature TEXT NOT NULL, "
    "FOREIGN KEY (answer_id) REFERENCES answer_records(answer_id));"
)

_INDEXES_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_answers_pipeline ON answer_records(pipeline);"
    "CREATE INDEX IF NOT EXISTS idx_answers_session ON answer_records(session_id);"
    "CREATE INDEX IF NOT EXISTS idx_answers_digest ON answer_records(record_digest);"
    "CREATE INDEX IF NOT EXISTS idx_feedback_answer ON answer_feedback(answer_id);"
)


class AnswerLedger:
    """Governed, append-only, tamper-evident answer ledger SQLite store."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        con = create_sqlite_conn(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._write_lock:
            con = self._get_connection()
            try:
                con.execute(_TABLE_RECORDS_SQL)
                con.execute(_TABLE_FEEDBACK_SQL)
                con.executescript(_INDEXES_SQL)
                existing_cols = {
                    row[1] for row in con.execute("PRAGMA table_info(answer_records);").fetchall()
                }
                for col_name, col_type in (
                    ("retrieve_latency_ms", "REAL"),
                    ("verify_latency_ms", "REAL"),
                    ("render_latency_ms", "REAL"),
                    ("stage_latencies_ms", "TEXT"),
                ):
                    if col_name not in existing_cols:
                        con.execute(f"ALTER TABLE answer_records ADD COLUMN {col_name} {col_type};")
                con.commit()
            finally:
                con.close()

    def append_answer(self, record: AnswerRecord) -> AnswerRecord:
        """Appends an AnswerRecord into the ledger, computing and binding the hash chain."""
        with self._write_lock:
            con = self._get_connection()
            try:
                cur = con.cursor()
                cur.execute("BEGIN IMMEDIATE;")
                try:
                    cur.execute(
                        "SELECT record_digest FROM answer_records ORDER BY row_id DESC LIMIT 1;"
                    )
                    row = cur.fetchone()
                    prev_digest = row["record_digest"] if row else GENESIS_PREV_DIGEST
                    record.prev_record_digest = prev_digest
                    record.record_digest = record.compute_digest()

                    cur.execute(
                        """
                        INSERT INTO answer_records (
                            answer_id, pipeline, session_id, intent_or_type, context_digest,
                            retrieval_seeds, context_snapshot, cited_assertion_ids, rigor,
                            response_text, graph_sha256, generation, parameter_snapshot_digest,
                            latency_ms, model_id, timestamp, contract_id, schema_version,
                            prev_record_digest, record_digest,
                            retrieve_latency_ms, verify_latency_ms, render_latency_ms, stage_latencies_ms
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            record.answer_id,
                            record.pipeline,
                            record.session_id,
                            record.intent_or_type,
                            record.context_digest,
                            json.dumps(record.retrieval_seeds, ensure_ascii=False),
                            json.dumps(record.context_snapshot, ensure_ascii=False),
                            json.dumps(record.cited_assertion_ids, ensure_ascii=False),
                            record.rigor,
                            record.response_text,
                            record.graph_sha256,
                            str(record.generation) if record.generation is not None else None,
                            record.parameter_snapshot_digest,
                            record.latency_ms,
                            record.model_id,
                            record.timestamp,
                            record.contract_id,
                            record.schema_version,
                            record.prev_record_digest,
                            record.record_digest,
                            record.retrieve_latency_ms,
                            record.verify_latency_ms,
                            record.render_latency_ms,
                            json.dumps(record.stage_latencies_ms, ensure_ascii=False) if record.stage_latencies_ms else None,
                        ),
                    )
                    con.commit()
                except Exception:
                    try:
                        con.execute("ROLLBACK;")
                    except Exception as rb_err:
                        logger.debug("AnswerLedger rollback failed: %s", rb_err)
                    raise
                return record
            finally:
                con.close()

    def append_answer_safe(self, record: AnswerRecord) -> Optional[AnswerRecord]:
        """Best-effort append that logs errors and returns None rather than failing caller."""
        try:
            return self.append_answer(record)
        except Exception as exc:
            logger.warning("AnswerLedger best-effort append failed: %s", exc)
            return None

    def get_answer(self, answer_id: str) -> Optional[AnswerRecord]:
        """Retrieves an AnswerRecord by answer_id."""
        con = self._get_connection()
        try:
            cur = con.cursor()
            cur.execute("SELECT * FROM answer_records WHERE answer_id = ?;", (answer_id,))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_record(row)
        finally:
            con.close()

    def list_answers(
        self,
        pipeline: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[AnswerRecord]:
        """Lists AnswerRecords with optional pipeline or session_id filters."""
        con = self._get_connection()
        try:
            cur = con.cursor()
            query = "SELECT * FROM answer_records"
            params: List[Any] = []
            conditions: List[str] = []
            if pipeline:
                conditions.append("pipeline = ?")
                params.append(pipeline)
            if session_id:
                conditions.append("session_id = ?")
                params.append(session_id)
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY row_id ASC LIMIT ?"
            params.append(limit)

            cur.execute(query, params)
            return [self._row_to_record(r) for r in cur.fetchall()]
        finally:
            con.close()

    def append_feedback(self, feedback: AnswerFeedback) -> AnswerFeedback:
        """Appends feedback bound to an existing answer record."""
        with self._write_lock:
            con = self._get_connection()
            try:
                cur = con.cursor()
                cur.execute(
                    """
                    INSERT INTO answer_feedback (
                        feedback_id, answer_id, answer_digest, feedback_type, rating,
                        origin, note, weight, timestamp, contract_id, schema_version, signature
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        feedback.feedback_id,
                        feedback.answer_id,
                        feedback.answer_digest,
                        feedback.feedback_type,
                        feedback.rating,
                        feedback.origin,
                        feedback.note,
                        feedback.weight,
                        feedback.timestamp,
                        feedback.contract_id,
                        feedback.schema_version,
                        feedback.signature,
                    ),
                )
                con.commit()
                return feedback
            finally:
                con.close()

    def list_feedback(self, answer_id: Optional[str] = None) -> List[AnswerFeedback]:
        """Lists feedback records."""
        con = self._get_connection()
        try:
            cur = con.cursor()
            if answer_id:
                cur.execute("SELECT * FROM answer_feedback WHERE answer_id = ? ORDER BY timestamp ASC;", (answer_id,))
            else:
                cur.execute("SELECT * FROM answer_feedback ORDER BY timestamp ASC;")
            return [self._row_to_feedback(r) for r in cur.fetchall()]
        finally:
            con.close()

    def verify_chain(self) -> Tuple[bool, str]:
        """Verifies full cryptographic hash chain integrity across all records."""
        con = self._get_connection()
        try:
            cur = con.cursor()
            cur.execute("SELECT * FROM answer_records ORDER BY row_id ASC;")
            rows = cur.fetchall()
            if not rows:
                return True, "Hash chain verified: 0 records in ledger (empty)."

            expected_prev = GENESIS_PREV_DIGEST
            for idx, r in enumerate(rows):
                rec = self._row_to_record(r)
                if rec.prev_record_digest != expected_prev:
                    return (
                        False,
                        f"TAMPER_DETECTED: Row index {idx} ({rec.answer_id}) prev_record_digest "
                        f"'{rec.prev_record_digest}' does not match expected '{expected_prev}'.",
                    )
                computed = rec.compute_digest()
                if rec.record_digest != computed:
                    return (
                        False,
                        f"TAMPER_DETECTED: Row index {idx} ({rec.answer_id}) record_digest "
                        f"'{rec.record_digest}' does not match computed digest '{computed}'.",
                    )
                expected_prev = rec.record_digest

            return True, f"Hash chain verified: {len(rows)} records intact."
        finally:
            con.close()

    def _row_to_record(self, r: sqlite3.Row) -> AnswerRecord:
        r_keys = set(r.keys()) if hasattr(r, "keys") else set()
        st_raw = r["stage_latencies_ms"] if "stage_latencies_ms" in r_keys else None
        return AnswerRecord(
            answer_id=r["answer_id"],
            pipeline=r["pipeline"],
            session_id=r["session_id"],
            intent_or_type=r["intent_or_type"],
            context_digest=r["context_digest"],
            retrieval_seeds=json.loads(r["retrieval_seeds"]),
            context_snapshot=json.loads(r["context_snapshot"]),
            cited_assertion_ids=json.loads(r["cited_assertion_ids"]),
            rigor=r["rigor"],
            response_text=r["response_text"],
            graph_sha256=r["graph_sha256"],
            generation=r["generation"],
            parameter_snapshot_digest=r["parameter_snapshot_digest"],
            latency_ms=r["latency_ms"],
            model_id=r["model_id"],
            timestamp=r["timestamp"],
            contract_id=r["contract_id"],
            schema_version=r["schema_version"],
            prev_record_digest=r["prev_record_digest"],
            record_digest=r["record_digest"],
            retrieve_latency_ms=r["retrieve_latency_ms"] if "retrieve_latency_ms" in r_keys else None,
            verify_latency_ms=r["verify_latency_ms"] if "verify_latency_ms" in r_keys else None,
            render_latency_ms=r["render_latency_ms"] if "render_latency_ms" in r_keys else None,
            stage_latencies_ms=json.loads(st_raw) if st_raw else None,
        )

    def _row_to_feedback(self, r: sqlite3.Row) -> AnswerFeedback:
        return AnswerFeedback(
            feedback_id=r["feedback_id"],
            answer_id=r["answer_id"],
            answer_digest=r["answer_digest"],
            feedback_type=r["feedback_type"],
            rating=r["rating"],
            origin=r["origin"],
            note=r["note"],
            weight=r["weight"],
            timestamp=r["timestamp"],
            contract_id=r["contract_id"],
            schema_version=r["schema_version"],
            signature=r["signature"],
        )
