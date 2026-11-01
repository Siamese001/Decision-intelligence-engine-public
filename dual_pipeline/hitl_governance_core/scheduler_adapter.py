"""
dual_pipeline/hitl_governance_core/scheduler_adapter.py

Durable Scheduler / Unattended Host Adapter for Active Ingestion Approval Lifecycle:
  - Scans and lists pending approval requests with age and expiration calculation.
  - Bounded retry policy for notification delivery preventing duplicate alerts or orphaned tasks.
  - State inspection for process restarts and resume operations.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Optional

from dual_pipeline.core import digest, read_json, write_json


def _seal(payload: Dict[str, Any]) -> Dict[str, Any]:
    copy_payload = dict(payload)
    copy_payload.pop("canonical_digest", None)
    copy_payload["canonical_digest"] = {
        "algorithm": "sha256",
        "scope": "artifact_without_canonical_digest",
        "value": digest(copy_payload),
    }
    return copy_payload


def _get_request_dir(storage_root: Path | str) -> Path:
    p = Path(storage_root).resolve()
    req_dir = p / "approval_requests"
    req_dir.mkdir(parents=True, exist_ok=True)
    return req_dir


def _parse_request_record(req_file: Path, now: dt.datetime) -> Optional[Dict[str, Any]]:
    """Parse and validate a single approval request file."""
    try:
        data = read_json(req_file)
        if not isinstance(data, dict):
            return None
        if data.get("contract_id") != "urn:nee:dual-pipeline:active-ingestion-approval-request:1.0.0":
            return None

        status = data.get("response_status", "AWAITING")
        if status not in ("AWAITING", "PENDING"):
            return None

        expires_at_str = data.get("expires_at")
        is_expired = False
        age_seconds = 0.0
        if expires_at_str:
            exp_dt = dt.datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
            is_expired = now > exp_dt
            created_est = exp_dt - dt.timedelta(hours=24)
            age_seconds = max(0.0, (now - created_est).total_seconds())

        return {
            "request_id": data.get("request_id"),
            "run_id": data.get("run_id"),
            "directive_id": data.get("directive_id"),
            "project": data.get("project"),
            "proposed_delta_digest": data.get("proposed_delta_digest"),
            "response_status": "EXPIRED" if is_expired else status,
            "dispatch_status": data.get("dispatch_status", "PENDING"),
            "is_expired": is_expired,
            "age_seconds": age_seconds,
            "file_path": str(req_file),
        }
    except Exception:
        return None


def list_pending_approval_requests(storage_root: Path | str) -> List[Dict[str, Any]]:
    """Scan and list all pending, active approval requests with age and status."""
    req_dir = _get_request_dir(storage_root)
    now = dt.datetime.now(dt.timezone.utc)
    results: List[Dict[str, Any]] = []

    for req_file in sorted(req_dir.glob("*.json")):
        record = _parse_request_record(req_file, now)
        if record is not None:
            results.append(record)

    return results


def get_pending_approval_task(request_id: str, storage_root: Path | str) -> Optional[Dict[str, Any]]:
    """Retrieve full detail for a pending approval task."""
    req_dir = _get_request_dir(storage_root)
    req_file = req_dir / f"{request_id}.json"
    if not req_file.is_file():
        return None
    try:
        return read_json(req_file)
    except Exception:
        return None


def record_delivery_attempt(
    request_id: str,
    storage_root: Path | str,
    success: bool,
    error: Optional[str] = None,
    max_retries: int = 3,
    target_channel: Optional[str] = None,
) -> Dict[str, Any]:
    """Record a notification delivery attempt with bounded retry tracking."""
    req_dir = _get_request_dir(storage_root)
    req_file = req_dir / f"{request_id}.json"
    if not req_file.is_file():
        raise FileNotFoundError(f"Approval request not found: {req_file}")

    data = read_json(req_file)
    extensions = data.setdefault("extensions", {})
    delivery_meta = extensions.setdefault("delivery", {
        "attempts": 0,
        "history": [],
        "delivery_exhausted": False,
        "last_attempt_at": None,
    })

    attempts = delivery_meta.get("attempts", 0) + 1
    delivery_meta["attempts"] = attempts
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    delivery_meta["last_attempt_at"] = now_iso

    entry = {
        "attempt": attempts,
        "timestamp": now_iso,
        "success": success,
        "error": error,
        "target_channel": target_channel,
    }
    delivery_meta["history"].append(entry)

    if success:
        data["dispatch_status"] = "DISPATCHED"
        delivery_meta["delivery_exhausted"] = False
    else:
        if attempts >= max_retries:
            delivery_meta["delivery_exhausted"] = True
            data["dispatch_status"] = "DELIVERY_EXHAUSTED"
        else:
            data["dispatch_status"] = "RETRY_PENDING"

    sealed = _seal(data)
    write_json(req_file, sealed)
    return sealed


def check_duplicate_delivery(
    request_id: str,
    target_channel: str,
    storage_root: Path | str,
) -> bool:
    """Check if notification for request_id was already delivered to channel."""
    task = get_pending_approval_task(request_id, storage_root)
    if not task:
        return False
    delivery = task.get("extensions", {}).get("delivery", {})
    for hist in delivery.get("history", []):
        if hist.get("success") and hist.get("target_channel") == target_channel:
            return True
    return False


def format_pending_approval_summary(storage_root: Path | str) -> Dict[str, Any]:
    """Provide structured observable summary of outstanding scheduled approval tasks."""
    pending = list_pending_approval_requests(storage_root)
    active = [p for p in pending if not p.get("is_expired")]
    expired = [p for p in pending if p.get("is_expired")]
    max_age = max((p.get("age_seconds", 0.0) for p in active), default=0.0)

    return {
        "total_pending": len(active),
        "total_expired": len(expired),
        "max_pending_age_seconds": max_age,
        "pending_requests": active,
        "expired_requests": expired,
        "has_actionable_work": len(active) > 0,
    }
