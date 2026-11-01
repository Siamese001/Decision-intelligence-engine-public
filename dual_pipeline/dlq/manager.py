"""dual_pipeline/dlq/manager.py

Governed Dead Letter Queue (DLQ) Manager for failed ingestion runs:
- Captures failed payloads, unhandled stage exceptions, and forensic traces
- Scoped to explicit runtime/evidence root with read-only inspection safety
- Enforces failure classification (HUMAN_WAIT, TRANSIENT, PERMANENT, UNKNOWN)
- Deduplicates repeated incidents via deterministic fingerprints and occurrence history
- Preserves corrupt records and validates status transitions
"""
from __future__ import annotations

import json
import logging
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .lifecycle import (
    DLQStatus,
    FailureCategory,
    classify_failure,
    compute_incident_fingerprint,
    validate_status_transition,
)

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DLQ_DIR = ROOT / ".local" / "dual_pipeline" / "dlq"

logger = logging.getLogger(__name__)


class DeadLetterQueueManager:
    """Manages recording, listing, and lifecycle of failed ingestion events."""

    def __init__(
        self,
        dlq_dir: Optional[Union[str, Path]] = None,
        runtime_root: Optional[Union[str, Path]] = None,
        read_only: bool = False,
    ):
        if runtime_root:
            self.runtime_root = Path(runtime_root).resolve()
            self.dlq_dir = Path(dlq_dir).resolve() if dlq_dir else self.runtime_root / "dlq"
        else:
            self.dlq_dir = Path(dlq_dir).resolve() if dlq_dir else DEFAULT_DLQ_DIR
            self.runtime_root = self.dlq_dir.parent.parent

        self.read_only = read_only

    def enqueue(
        self,
        input_path: Optional[Union[str, Path]],
        metadata: Dict[str, Any],
        error: Exception,
        stage: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        category: Optional[FailureCategory] = None,
    ) -> str:
        """Captures failed ingestion details into a durable DLQ JSON envelope.

        Deduplicates against active incidents sharing the same fingerprint.
        """
        if self.read_only:
            raise PermissionError("Cannot enqueue to DLQ in read-only mode.")

        self.dlq_dir.mkdir(parents=True, exist_ok=True)

        now = time.time()
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        failure_category = category or classify_failure(error, metadata)
        stage_str = str(stage or "UNKNOWN")

        stable_op_id = str(
            metadata.get("directive_id")
            or metadata.get("operation_id")
            or metadata.get("project")
            or (Path(input_path).name if input_path else "unknown_op")
        )

        fingerprint = compute_incident_fingerprint(
            runtime_root=self.runtime_root,
            stable_op_id=stable_op_id,
            stage=stage_str,
            category=failure_category,
        )

        # Check for active existing incident with matching fingerprint
        existing_record = self._find_active_by_fingerprint(fingerprint)
        if existing_record:
            return self._record_reoccurrence(existing_record, error, stage_str, now_iso)

        time_tag = time.strftime("%Y%m%d_%H%M%S", time.gmtime(now))
        short_id = uuid.uuid4().hex[:8]
        dlq_id = f"dlq_{time_tag}_{short_id}"

        tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))

        safe_meta = {}
        for k, v in metadata.items():
            try:
                json.dumps(v)
                safe_meta[k] = v
            except (TypeError, OverflowError):
                safe_meta[k] = str(v)

        initial_status = DLQStatus.NEW.value
        if failure_category == FailureCategory.HUMAN_WAIT:
            initial_status = DLQStatus.WAITING_APPROVAL.value
        elif failure_category == FailureCategory.TRANSIENT:
            initial_status = DLQStatus.TRANSIENT_RETRY.value

        record = {
            "dlq_id": dlq_id,
            "fingerprint": fingerprint,
            "failure_category": failure_category.value,
            "runtime_root": str(self.runtime_root),
            "stable_op_id": stable_op_id,
            "created_at_utc": now_iso,
            "last_seen_utc": now_iso,
            "created_at_timestamp": now,
            "input_path": str(input_path) if input_path else None,
            "stage": stage_str,
            "error_type": error.__class__.__name__,
            "error_message": str(error),
            "traceback": tb_str,
            "metadata": safe_meta,
            "context": context or {},
            "status": initial_status,
            "retry_count": 0,
            "occurrence_count": 1,
            "occurrences": [
                {
                    "timestamp": now_iso,
                    "error": str(error),
                    "stage": stage_str,
                }
            ],
            "resolved_at": None,
            "resolution_notes": None,
        }

        target_file = self.dlq_dir / f"{dlq_id}.json"
        temp_file = target_file.with_suffix(".tmp")
        temp_file.write_text(json.dumps(record, indent=2), encoding="utf-8")
        temp_file.replace(target_file)

        logger.info("Ingestion failure recorded to DLQ: %s (file: %s)", dlq_id, target_file)
        return dlq_id

    def _find_active_by_fingerprint(self, fingerprint: str) -> Optional[Dict[str, Any]]:
        """Finds existing unclosed DLQ record with the same fingerprint."""
        if not self.dlq_dir.exists():
            return None
        for f in sorted(self.dlq_dir.glob("dlq_*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if (
                    isinstance(data, dict)
                    and data.get("fingerprint") == fingerprint
                    and data.get("status") not in (DLQStatus.RESOLVED.value, DLQStatus.ABANDONED.value)
                ):
                    return data
            except Exception:
                continue
        return None

    def _record_reoccurrence(
        self,
        record: Dict[str, Any],
        error: Exception,
        stage: str,
        now_iso: str,
    ) -> str:
        """Records an additional occurrence on an existing active incident."""
        dlq_id = str(record["dlq_id"])
        record["last_seen_utc"] = now_iso
        record["occurrence_count"] = int(record.get("occurrence_count", 1)) + 1
        occurrences = record.get("occurrences", [])
        occurrences.append({
            "timestamp": now_iso,
            "error": str(error),
            "stage": stage,
        })
        record["occurrences"] = occurrences

        # Manage retry limits for transient failures
        if record.get("status") == DLQStatus.TRANSIENT_RETRY.value:
            max_retries = int(record.get("metadata", {}).get("max_retries", 3))
            retries = int(record.get("retry_count", 0)) + 1
            record["retry_count"] = retries
            if retries >= max_retries:
                record["status"] = DLQStatus.RETRY_EXHAUSTED.value

        target_file = self.dlq_dir / f"{dlq_id}.json"
        temp_file = target_file.with_suffix(".tmp")
        temp_file.write_text(json.dumps(record, indent=2), encoding="utf-8")
        temp_file.replace(target_file)
        logger.info("Recorded reoccurrence on existing DLQ item: %s (count: %d)", dlq_id, record["occurrence_count"])
        return dlq_id

    def list_items(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Returns sorted list of DLQ records, exposing corrupt records if present."""
        items: List[Dict[str, Any]] = []
        if not self.dlq_dir.exists():
            return items

        for f in sorted(self.dlq_dir.glob("dlq_*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    if status is None or data.get("status") == status:
                        items.append(data)
                else:
                    if status is None or status == DLQStatus.CORRUPT.value:
                        items.append({
                            "dlq_id": f.stem,
                            "status": DLQStatus.CORRUPT.value,
                            "file_path": str(f),
                            "error": "Payload is not a JSON object",
                        })
            except Exception as exc:
                if status is None or status == DLQStatus.CORRUPT.value:
                    items.append({
                        "dlq_id": f.stem,
                        "status": DLQStatus.CORRUPT.value,
                        "file_path": str(f),
                        "error": str(exc),
                    })

        return items

    def get_item(self, dlq_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves specific DLQ record by ID."""
        clean_id = dlq_id.strip()
        target = self.dlq_dir / f"{clean_id}.json" if not clean_id.endswith(".json") else self.dlq_dir / clean_id
        if not target.is_file():
            return None

        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            return None

    def mark_status(self, dlq_id: str, new_status: str, notes: Optional[str] = None) -> bool:
        """Updates lifecycle status of a DLQ item adhering to valid transitions and resolution evidence."""
        if self.read_only:
            raise PermissionError("Cannot modify DLQ record in read-only mode.")

        clean_id = dlq_id.strip()
        target = self.dlq_dir / f"{clean_id}.json" if not clean_id.endswith(".json") else self.dlq_dir / clean_id
        if not target.is_file():
            return False

        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            curr_status = data.get("status", DLQStatus.NEW.value)
            validate_status_transition(curr_status, new_status, notes)

            data["status"] = new_status
            if new_status in (DLQStatus.RESOLVED.value, DLQStatus.ABANDONED.value):
                data["resolved_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            if notes:
                data["resolution_notes"] = notes

            temp_file = target.with_suffix(".tmp")
            temp_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            temp_file.replace(target)
            return True
        except Exception as exc:
            logger.error("Failed to mark status for %s: %s", dlq_id, exc)
            raise
