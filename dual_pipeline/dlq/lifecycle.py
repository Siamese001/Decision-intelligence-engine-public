"""dual_pipeline.dlq.lifecycle

Implements failure classification, deterministic incident fingerprints,
validated lifecycle status transitions, and migration assessment for DLQ incidents.
"""
from __future__ import annotations

import hashlib
import json
import logging
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class FailureCategory(str, Enum):
    HUMAN_WAIT = "HUMAN_WAIT"
    TRANSIENT = "TRANSIENT"
    PERMANENT = "PERMANENT"
    UNKNOWN = "UNKNOWN"


class DLQStatus(str, Enum):
    NEW = "NEW"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    TRANSIENT_RETRY = "TRANSIENT_RETRY"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    RESOLVED = "RESOLVED"
    ABANDONED = "ABANDONED"
    CORRUPT = "CORRUPT"


VALID_TRANSITIONS: dict[DLQStatus, set[DLQStatus]] = {
    DLQStatus.NEW: {
        DLQStatus.WAITING_APPROVAL,
        DLQStatus.TRANSIENT_RETRY,
        DLQStatus.RESOLVED,
        DLQStatus.ABANDONED,
    },
    DLQStatus.WAITING_APPROVAL: {
        DLQStatus.NEW,
        DLQStatus.RESOLVED,
        DLQStatus.ABANDONED,
    },
    DLQStatus.TRANSIENT_RETRY: {
        DLQStatus.TRANSIENT_RETRY,
        DLQStatus.RETRY_EXHAUSTED,
        DLQStatus.RESOLVED,
        DLQStatus.ABANDONED,
    },
    DLQStatus.RETRY_EXHAUSTED: {
        DLQStatus.TRANSIENT_RETRY,  # with manual re-arm
        DLQStatus.RESOLVED,
        DLQStatus.ABANDONED,
    },
    DLQStatus.RESOLVED: set(),
    DLQStatus.ABANDONED: set(),
    DLQStatus.CORRUPT: {DLQStatus.RESOLVED, DLQStatus.ABANDONED},
}


def classify_failure(exc: Exception, meta: dict[str, Any] | None = None) -> FailureCategory:
    """Classifies an ingestion failure into HUMAN_WAIT, TRANSIENT, PERMANENT, or UNKNOWN."""
    meta = meta or {}
    exc_type = type(exc).__name__
    exc_str = str(exc).lower()

    from dual_pipeline.exceptions import HITLRequiredError

    if (
        isinstance(exc, HITLRequiredError)
        or exc_type == "HITLRequiredError"
        or bool(getattr(exc, "hitl_type", None))
        or meta.get("approval_required")
        or meta.get("hitl_pending")
    ):
        return FailureCategory.HUMAN_WAIT

    if "credit_balance_exhausted" in exc_str or "quota" in exc_str:
        return FailureCategory.HUMAN_WAIT

    if (
        "rate limit" in exc_str
        or "429" in exc_str
        or "503" in exc_str
        or "timeout" in exc_str
        or "timed out" in exc_str
        or "connection reset" in exc_str
        or "temporary failure" in exc_str
        or exc_type in ("RateLimitError", "RateLimitExceededError", "TimeoutError")
    ):
        return FailureCategory.TRANSIENT

    if (
        "validationerror" in exc_type.lower()
        or "schemavalidationerror" in exc_type.lower()
        or "jsondecodeerror" in exc_type.lower()
        or "filenotfounderror" in exc_type.lower()
        or "permissionerror" in exc_type.lower()
        or "malformed" in exc_str
        or "invalid schema" in exc_str
    ):
        return FailureCategory.PERMANENT

    return FailureCategory.UNKNOWN


def compute_incident_fingerprint(
    runtime_root: str | Path,
    stable_op_id: str,
    stage: str,
    category: FailureCategory,
) -> str:
    """Computes a deterministic fingerprint binding runtime root, operation identity, stage, and failure category."""
    clean_root = str(Path(runtime_root).resolve())
    clean_op = str(stable_op_id).strip()
    clean_stage = str(stage).strip().upper()
    cat_val = category.value if isinstance(category, FailureCategory) else str(category)

    raw = f"{clean_root}:{clean_op}:{clean_stage}:{cat_val}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def validate_status_transition(current_status: str, new_status: str, notes: str | None = None) -> None:
    """Validates that a DLQ status transition is legally permitted and backed by evidence."""
    try:
        curr = DLQStatus(current_status)
    except ValueError:
        curr = DLQStatus.NEW

    try:
        nxt = DLQStatus(new_status)
    except ValueError:
        raise ValueError(f"Unknown target DLQ status: {new_status}")

    if nxt == DLQStatus.RESOLVED and not (notes and notes.strip()):
        raise ValueError("Resolving a DLQ incident requires verified resolution notes or evidence.")

    allowed = VALID_TRANSITIONS.get(curr, set())
    if nxt not in allowed:
        raise ValueError(f"Illegal DLQ status transition from {curr.value} to {nxt.value}.")


def assess_dlq_migration(dlq_dir: Path) -> dict[str, Any]:
    """Assesses historical DLQ directory, mapping statuses and verifying provenance preservation."""
    report: dict[str, Any] = {
        "total_records": 0,
        "valid_records": 0,
        "corrupt_records": 0,
        "status_counts": {},
        "category_counts": {},
        "unmigrated_records": 0,
    }

    if not dlq_dir.exists():
        return report

    for f in dlq_dir.glob("dlq_*.json"):
        report["total_records"] += 1
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                report["corrupt_records"] += 1
                continue
            report["valid_records"] += 1
            st = str(data.get("status", "UNKNOWN"))
            cat = str(data.get("failure_category", "UNCLASSIFIED"))
            report["status_counts"][st] = report["status_counts"].get(st, 0) + 1
            report["category_counts"][cat] = report["category_counts"].get(cat, 0) + 1
            if "fingerprint" not in data or "failure_category" not in data:
                report["unmigrated_records"] += 1
        except Exception:
            report["corrupt_records"] += 1

    return report
