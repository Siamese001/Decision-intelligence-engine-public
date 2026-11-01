"""Pipeline B (Extraction): Durable Dead-Letter Queue (DLQ) & Failure Quarantine.

Enforces fail-closed extraction integrity:
1. Replaces silent synthetic mock fallbacks with durable error tracking.
2. Quarantines unparseable or failed evidence items after max retries.
3. Provides inspection and redrive capabilities for failed extractions.
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from storage.database import DatabaseManager, WorkItemState

logger = logging.getLogger("pipeline_b_extraction.dlq")


class ExtractionFailureType(str, Enum):
    """Strongly-typed classifications for extraction and synthesis failures."""
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    TRANSIENT_RATE_LIMIT = "TRANSIENT_RATE_LIMIT"
    AUTH_FAILURE = "AUTH_FAILURE"
    SCHEMA_VALIDATION_FAILURE = "SCHEMA_VALIDATION_FAILURE"
    TIMEOUT = "TIMEOUT"
    UNCERTAIN_COMPLETION = "UNCERTAIN_COMPLETION"
    L2_ZERO_RETRY_BLOCKED = "L2_ZERO_RETRY_BLOCKED"
    UNCLASSIFIED = "UNCLASSIFIED"


_L2_ZERO_RETRY_TOKENS = ("zero_retry", "zero retry", "zero-retry", "no retry")
_QUOTA_TOKENS = ("quota", "credit balance", "insufficient_quota")
_RATE_LIMIT_TOKENS = ("rate_limit", "rate limit", "too many requests", "429")
_AUTH_TOKENS = ("auth", "unauthorized", "401", "403", "forbidden", "permission denied")
_SCHEMA_TOKENS = ("schema", "validation", "pydantic", "json decode", "jsondecode", "malformed")
_TIMEOUT_TOKENS = ("timeout", "timed out", "deadline", "deadlineexceeded")
_UNCERTAIN_TOKENS = ("uncertain", "hallucination", "ambiguity", "low confidence")


def _matches_any(text: str, tokens: tuple[str, ...]) -> bool:
    for t in tokens:
        if t in text:
            return True
    return False


def classify_extraction_failure(error: Union[Exception, str]) -> ExtractionFailureType:
    """Classifies an extraction error into a strongly-typed failure domain."""
    err_str = str(error).lower()
    exc_type = type(error).__name__.lower() if isinstance(error, Exception) else ""
    combined = f"{exc_type} {err_str}"

    if "l2" in combined and _matches_any(combined, _L2_ZERO_RETRY_TOKENS):
        return ExtractionFailureType.L2_ZERO_RETRY_BLOCKED
    if _matches_any(combined, _QUOTA_TOKENS):
        return ExtractionFailureType.QUOTA_EXCEEDED
    if _matches_any(combined, _RATE_LIMIT_TOKENS):
        return ExtractionFailureType.TRANSIENT_RATE_LIMIT
    if _matches_any(combined, _AUTH_TOKENS):
        return ExtractionFailureType.AUTH_FAILURE
    if _matches_any(combined, _SCHEMA_TOKENS):
        return ExtractionFailureType.SCHEMA_VALIDATION_FAILURE
    if _matches_any(combined, _TIMEOUT_TOKENS):
        return ExtractionFailureType.TIMEOUT
    if _matches_any(combined, _UNCERTAIN_TOKENS):
        return ExtractionFailureType.UNCERTAIN_COMPLETION

    return ExtractionFailureType.UNCLASSIFIED


def is_non_retryable_failure(failure_type: ExtractionFailureType) -> bool:
    """Identifies failures where automatic retry is barred by governance."""
    return failure_type in (
        ExtractionFailureType.L2_ZERO_RETRY_BLOCKED,
        ExtractionFailureType.AUTH_FAILURE,
        ExtractionFailureType.QUOTA_EXCEEDED,
    )


@dataclass
class DeadLetterEntry:
    """Represents an unrecoverable extraction failure stored in DLQ."""
    event_id: str
    evidence_id: str
    error_message: str
    retry_count: int
    max_retries: int
    payload_snippet: str
    failure_type: str = ExtractionFailureType.UNCLASSIFIED.value
    created_at: float = field(default_factory=time.time)
    state: str = "DEAD_LETTER"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DeadLetterQueueManager:
    """
    Manages durable dead-letter queue operations across SQLite and in-memory caches.
    Ensures extraction failures fail closed rather than generating synthetic mock facts.
    """

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        default_max_retries: int = 3,
    ):
        self.db_manager = db_manager
        self.default_max_retries = default_max_retries
        self._in_memory_dlq: Dict[str, DeadLetterEntry] = {}

    def route_failure(
        self,
        event_id: str,
        evidence_id: str,
        error: Union[Exception, str],
        retry_count: int = 1,
        max_retries: Optional[int] = None,
        payload_snippet: str = "",
        failure_type: Optional[Union[ExtractionFailureType, str]] = None,
    ) -> WorkItemState:
        """
        Routes an extraction failure to durable retry or dead-letter quarantine.
        Never commits synthetic mock facts.
        """
        max_r = max_retries if max_retries is not None else self.default_max_retries
        err_msg = str(error)

        if failure_type is None:
            typed_failure = classify_extraction_failure(error)
        elif isinstance(failure_type, str):
            try:
                typed_failure = ExtractionFailureType(failure_type)
            except ValueError:
                typed_failure = ExtractionFailureType.UNCLASSIFIED
        else:
            typed_failure = failure_type

        # Enforce zero-retry and non-retryable policies
        non_retryable = is_non_retryable_failure(typed_failure)
        effective_max_retries = 0 if non_retryable else max_r

        if self.db_manager is not None:
            try:
                next_state = self.db_manager.fail_work_item(
                    event_id=event_id,
                    error_message=f"[{typed_failure.value}] {err_msg}",
                    max_retries=effective_max_retries,
                )
                if next_state == WorkItemState.DEAD_LETTER:
                    logger.critical(
                        f"[DLQ-QUARANTINE] Event {event_id} (evidence: {evidence_id}) "
                        f"moved to DEAD_LETTER (type: {typed_failure.value}): {err_msg}"
                    )
                else:
                    logger.warning(
                        f"[DLQ-RETRYABLE] Event {event_id} failed (attempt {retry_count}/{effective_max_retries}). "
                        f"Re-queued as PENDING (type: {typed_failure.value}): {err_msg}"
                    )
                return next_state
            except Exception as db_err:
                logger.error(f"Error persisting DLQ status to database: {db_err}")

        # In-memory fallback tracking
        is_exhausted = non_retryable or (retry_count >= effective_max_retries)
        state = WorkItemState.DEAD_LETTER if is_exhausted else WorkItemState.PENDING
        self._in_memory_dlq[event_id] = DeadLetterEntry(
            event_id=event_id,
            evidence_id=evidence_id,
            error_message=err_msg,
            retry_count=retry_count,
            max_retries=effective_max_retries,
            payload_snippet=payload_snippet[:500],
            failure_type=typed_failure.value,
            state=state.value,
        )
        return state

    def list_dead_letters(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Returns dead-letter items from database or in-memory store."""
        if self.db_manager is not None:
            try:
                with self.db_manager.get_evidence_conn() as conn:
                    rows = conn.execute(
                        """
                        SELECT q.event_id, q.evidence_id, q.retry_count, q.error_message, 
                               q.updated_at, COALESCE(e.text, '') AS payload_snippet
                        FROM durable_work_queue q
                        LEFT JOIN immutable_evidence_log e ON q.evidence_id = e.evidence_id
                        WHERE q.state = 'DEAD_LETTER'
                        ORDER BY q.updated_at DESC
                        LIMIT ?
                        """,
                        (limit,)
                    ).fetchall()
                    return [dict(r) for r in rows]
            except Exception as e:
                logger.error(f"Error reading DEAD_LETTER entries from db: {e}")

        return [entry.to_dict() for entry in list(self._in_memory_dlq.values())[:limit]]

    def redrive_item(self, event_id: str) -> bool:
        """Resets a DEAD_LETTER item back to PENDING with retry_count=0."""
        if self.db_manager is not None:
            try:
                with self.db_manager.get_evidence_conn() as conn:
                    with self.db_manager.transaction(conn, db_key="evidence"):
                        res = conn.execute(
                            """
                            UPDATE durable_work_queue
                            SET state = 'PENDING',
                                retry_count = 0,
                                locked_at = NULL,
                                locked_by = NULL,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE event_id = ? AND state = 'DEAD_LETTER'
                            """,
                            (event_id,)
                        )
                        return bool(res.rowcount > 0)
            except Exception as e:
                logger.error(f"Error redriving dead letter item {event_id}: {e}")
                return False

        if event_id in self._in_memory_dlq:
            entry = self._in_memory_dlq[event_id]
            entry.state = "PENDING"
            entry.retry_count = 0
            return True
        return False
