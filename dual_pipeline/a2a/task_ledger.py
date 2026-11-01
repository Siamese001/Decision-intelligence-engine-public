"""A2A SQLite WAL Durable Task Ledger.

Guarantees durable task persistence, state transitions, idempotency checks,
and crash recovery for multi-agent workflows.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from storage.connection import sqlite3

from .protocol import A2ATask, A2ATaskStatus


class A2ATaskLedger:
    """Durable SQLite WAL ledger tracking A2A tasks and state transitions."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._mem_conn: Optional[sqlite3.Connection] = None
        if self.db_path == ":memory:":
            from dual_pipeline.storage.governed_context import GovernedStorageContext
            self._mem_conn = GovernedStorageContext.connect(":memory:", check_same_thread=False)
            self._mem_conn.row_factory = sqlite3.Row
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        from dual_pipeline.storage.governed_context import GovernedStorageContext
        conn = GovernedStorageContext.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


    def _init_db(self) -> None:
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._get_connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS a2a_task (
                    task_id TEXT PRIMARY KEY,
                    parent_task_id TEXT,
                    correlation_id TEXT NOT NULL,
                    idempotency_key TEXT UNIQUE NOT NULL,
                    sender_agent_id TEXT NOT NULL,
                    recipient_agent_id TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL,
                    scope_in TEXT,
                    scope_out TEXT,
                    allowed_tools TEXT,
                    budget TEXT,
                    claim_checks TEXT,
                    input_payload TEXT,
                    output_payload TEXT,
                    error_message TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_a2a_task_status ON a2a_task(status);
                CREATE INDEX IF NOT EXISTS idx_a2a_task_correlation ON a2a_task(correlation_id);
                CREATE INDEX IF NOT EXISTS idx_a2a_task_recipient ON a2a_task(recipient_agent_id);

                CREATE TABLE IF NOT EXISTS a2a_task_event (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    details TEXT,
                    FOREIGN KEY(task_id) REFERENCES a2a_task(task_id)
                );

                CREATE INDEX IF NOT EXISTS idx_a2a_event_task ON a2a_task_event(task_id);
                """
            )

    def record_task_submission(self, task: A2ATask) -> A2ATask:
        """Persist a newly submitted task. Enforces idempotency."""
        with self._get_connection() as conn:
            existing = conn.execute(
                "SELECT * FROM a2a_task WHERE idempotency_key = ?", (task.idempotency_key,)
            ).fetchone()
            if existing:
                return self._row_to_task(existing)

            conn.execute(
                """
                INSERT INTO a2a_task (
                    task_id, parent_task_id, correlation_id, idempotency_key,
                    sender_agent_id, recipient_agent_id, objective, status,
                    scope_in, scope_out, allowed_tools, budget, claim_checks,
                    input_payload, output_payload, error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.task_id,
                    task.parent_task_id,
                    task.correlation_id,
                    task.idempotency_key,
                    task.sender_agent_id,
                    task.recipient_agent_id,
                    task.objective,
                    task.status.value,
                    json.dumps(task.scope_in),
                    json.dumps(task.scope_out),
                    json.dumps(task.allowed_tools),
                    json.dumps(task.budget.to_dict()),
                    json.dumps([c.to_dict() for c in task.claim_checks]),
                    json.dumps(task.input_payload),
                    json.dumps(task.output_payload) if task.output_payload else None,
                    task.error_message,
                    task.created_at,
                    task.updated_at,
                ),
            )
            conn.execute(
                """
                INSERT INTO a2a_task_event (task_id, from_status, to_status, timestamp, details)
                VALUES (?, NULL, ?, ?, ?)
                """,
                (task.task_id, task.status.value, time.time(), "Task submitted"),
            )
            conn.commit()
            return task

    def update_task_status(
        self,
        task_id: str,
        new_status: A2ATaskStatus,
        output_payload: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> A2ATask:
        """Atomically transition task status in the ledger."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM a2a_task WHERE task_id = ?", (task_id,)).fetchone()
            if not row:
                raise KeyError(f"TASK_NOT_FOUND: Task '{task_id}' does not exist in ledger.")

            current_task = self._row_to_task(row)
            old_status = current_task.status
            current_task.transition_to(new_status, error=error_message)

            if output_payload is not None:
                current_task.output_payload = output_payload

            conn.execute(
                """
                UPDATE a2a_task
                SET status = ?, output_payload = ?, error_message = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (
                    current_task.status.value,
                    json.dumps(current_task.output_payload) if current_task.output_payload else None,
                    current_task.error_message,
                    current_task.updated_at,
                    task_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO a2a_task_event (task_id, from_status, to_status, timestamp, details)
                VALUES (?, ?, ?, ?, ?)
                """,
                (task_id, old_status.value, new_status.value, time.time(), error_message or "Status updated"),
            )
            conn.commit()
            return current_task

    def get_task(self, task_id: str) -> Optional[A2ATask]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM a2a_task WHERE task_id = ?", (task_id,)).fetchone()
            if not row:
                return None
            return self._row_to_task(row)

    def list_tasks_by_status(self, status: A2ATaskStatus) -> List[A2ATask]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM a2a_task WHERE status = ?", (status.value,)).fetchall()
            return [self._row_to_task(r) for r in rows]

    def list_tasks_by_correlation(self, correlation_id: str) -> List[A2ATask]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM a2a_task WHERE correlation_id = ? ORDER BY created_at ASC",
                (correlation_id,),
            ).fetchall()
            return [self._row_to_task(r) for r in rows]

    def get_task_history(self, task_id: str) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM a2a_task_event WHERE task_id = ? ORDER BY timestamp ASC",
                (task_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def recover_pending_tasks(self) -> List[A2ATask]:
        """Recovers unfinished tasks (SUBMITTED, ACCEPTED, WORKING) after process restart."""
        with self._get_connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM a2a_task
                WHERE status IN ('SUBMITTED', 'ACCEPTED', 'WORKING')
                ORDER BY created_at ASC
                """
            ).fetchall()
            return [self._row_to_task(r) for r in rows]

    def _row_to_task(self, row: sqlite3.Row) -> A2ATask:
        from .protocol import ClaimCheckPointer, DelegationBudget

        return A2ATask(
            task_id=row["task_id"],
            parent_task_id=row["parent_task_id"],
            correlation_id=row["correlation_id"],
            idempotency_key=row["idempotency_key"],
            sender_agent_id=row["sender_agent_id"],
            recipient_agent_id=row["recipient_agent_id"],
            objective=row["objective"],
            status=A2ATaskStatus(row["status"]),
            scope_in=json.loads(row["scope_in"] or "[]"),
            scope_out=json.loads(row["scope_out"] or "[]"),
            allowed_tools=json.loads(row["allowed_tools"] or "[]"),
            budget=DelegationBudget.from_dict(json.loads(row["budget"] or "{}")),
            claim_checks=[ClaimCheckPointer.from_dict(c) for c in json.loads(row["claim_checks"] or "[]")],
            input_payload=json.loads(row["input_payload"] or "{}"),
            output_payload=json.loads(row["output_payload"]) if row["output_payload"] else None,
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
