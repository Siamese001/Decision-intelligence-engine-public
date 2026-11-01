"""
dual_pipeline/learning_loop_core/ledger_scanner.py

Extracts negative feedback failure items from native SQLite AnswerLedgers
and JSON/JSONL feedback stores for ClosedLoopLearningEngine failure scanning.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

from dual_pipeline.core import read_json, utc_now
from storage.connection import create_sqlite_conn
from .models import AnswerFailureItem

logger = logging.getLogger(__name__)


def is_sqlite_db(path: Path) -> bool:
    """Checks whether the file is a SQLite database by extension or header bytes."""
    if path.suffix in (".db", ".sqlite", ".sqlite3"):
        return True
    try:
        with open(path, "rb") as f:
            header = f.read(16)
            return header.startswith(b"SQLite format 3")
    except Exception:
        return False


def _is_negative_rating(rating: Any) -> tuple[bool, float]:
    """Determines if a rating is negative, returning (is_negative, metric_value)."""
    if rating is None:
        return False, 0.0
    if isinstance(rating, (int, float)):
        return (rating < 0), float(rating)
    r_str = str(rating).strip().lower()
    if r_str in ("wrong", "-2"):
        return True, -1.0
    if r_str in ("not_useful", "negative", "-1"):
        return True, -0.5
    try:
        val = float(r_str)
        return (val < 0), val
    except ValueError:
        return False, 0.0


def extract_failures_from_sqlite_ledger(ledger_path: Path) -> List[AnswerFailureItem]:
    """Extracts failure items from a native SQLite AnswerLedger database."""
    failures: List[AnswerFailureItem] = []
    try:
        con = create_sqlite_conn(ledger_path, readonly=True)
        cur = con.cursor()
        query = (
            "SELECT f.feedback_id, f.answer_id, f.rating, f.note, f.origin, f.timestamp, "
            "r.pipeline, r.intent_or_type "
            "FROM answer_feedback f "
            "LEFT JOIN answer_records r ON f.answer_id = r.answer_id "
            "WHERE LOWER(f.rating) IN ('not_useful', 'wrong', 'negative') "
            "OR (f.rating GLOB '-*' AND CAST(f.rating AS REAL) < 0)"
        )
        cur.execute(query)
        for row in cur.fetchall():
            _, val = _is_negative_rating(row["rating"])
            failures.append(
                AnswerFailureItem(
                    source="sqlite_ledger_feedback",
                    receipt_id=row["feedback_id"],
                    question_id=row["answer_id"],
                    pipeline=row["pipeline"] or "copilot",
                    failure_type="negative_feedback",
                    metric_value=val,
                    threshold=0.0,
                    timestamp=row["timestamp"] or utc_now(),
                    details={"rating": row["rating"], "note": row["note"], "origin": row["origin"]},
                )
            )
        con.close()
    except Exception as e:
        logger.warning("Failed to extract failures from SQLite ledger %s: %s", ledger_path, e)
    return failures


def extract_failures_from_json_ledger(ledger_path: Path) -> List[AnswerFailureItem]:
    """Extracts failure items from a JSON or JSONL feedback ledger."""
    failures: List[AnswerFailureItem] = []
    try:
        data = read_json(ledger_path)
        items = data if isinstance(data, list) else data.get("records", data.get("feedback", []))
        for rec in items:
            is_neg, val = _is_negative_rating(rec.get("rating"))
            if is_neg:
                q_id = rec.get("question_id") or rec.get("answer_id") or "ans_unknown"
                failures.append(
                    AnswerFailureItem(
                        source="ledger_feedback",
                        receipt_id=rec.get("record_id", rec.get("feedback_id", "fb_record")),
                        question_id=q_id,
                        pipeline=rec.get("pipeline", "copilot"),
                        failure_type="negative_feedback",
                        metric_value=val,
                        threshold=0.0,
                        timestamp=rec.get("timestamp", utc_now()),
                        details=rec,
                    )
                )
    except Exception as e:
        logger.warning("Failed to read JSON ledger feedback from %s: %s", ledger_path, e)
    return failures


def extract_failures_from_ledger(ledger_path: Path) -> List[AnswerFailureItem]:
    """Unified entry point extracting feedback failures from SQLite or JSON ledgers."""
    if not ledger_path.is_file():
        return []
    if is_sqlite_db(ledger_path):
        return extract_failures_from_sqlite_ledger(ledger_path)
    return extract_failures_from_json_ledger(ledger_path)
