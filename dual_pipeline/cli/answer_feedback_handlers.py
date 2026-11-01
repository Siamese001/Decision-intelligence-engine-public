"""
dual_pipeline/cli/answer_feedback_handlers.py

CLI handler for `nee answer-feedback <answer_id> --rating ...`.
Enforces authentic operator origin and cryptographic binding to AnswerRecord digest.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.answer_ledger import get_default_answer_ledger, record_operator_feedback


def handle_answer_feedback(args: argparse.Namespace) -> Dict[str, Any]:
    """Handles `nee answer-feedback <answer_id> --rating useful|not_useful|wrong`."""
    runtime_root = getattr(args, "runtime_root", None)
    if runtime_root:
        runtime_root = Path(runtime_root)
    ledger = get_default_answer_ledger(runtime_root)

    answer_id = getattr(args, "answer_id", "").strip()
    rating = getattr(args, "rating", "").strip()
    note = getattr(args, "note", None)
    origin = getattr(args, "origin", "USER_EXPLICIT")

    if not answer_id:
        err = "MISSING_ANSWER_ID: An answer_id must be provided."
        print(f"❌ {err}", file=sys.stderr)
        return {"status": "FAIL", "error": "MISSING_ANSWER_ID", "details": err}

    try:
        fb = record_operator_feedback(
            ledger=ledger,
            answer_id=answer_id,
            rating=rating,
            note=note,
            origin=origin,
        )
        msg = f"Feedback recorded: {fb.feedback_id} (rating: {fb.rating}) bound to answer {fb.answer_id} (digest: {fb.answer_digest[:8]})."
        print(f"✅ {msg}")
        return {
            "status": "PASS",
            "command": "answer-feedback",
            "feedback_id": fb.feedback_id,
            "answer_id": fb.answer_id,
            "answer_digest": fb.answer_digest,
            "rating": fb.rating,
            "origin": fb.origin,
        }
    except Exception as exc:
        err = str(exc)
        print(f"❌ Failed to record feedback: {err}", file=sys.stderr)
        return {"status": "FAIL", "command": "answer-feedback", "error": err}
