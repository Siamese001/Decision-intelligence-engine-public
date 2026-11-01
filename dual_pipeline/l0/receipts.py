"""Dual-Pipeline Layer 0: Runtime Receipts, Parent Chaining & Immutable Locks.

Provides immutable audit receipts and sequential completion locks.
Strictly Zero-Dependency (standard library only). Layer L0 must NEVER import L1-L6.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from dual_pipeline.l0.contracts import RuntimeReceipt
from dual_pipeline.l0.provenance import canonical_json_bytes, compute_content_digest


def create_runtime_receipt(
    receipt_id: str,
    wave_id: str,
    step_name: str,
    status: str,
    input_digests: List[str],
    output_digests: List[str],
    parent_receipt_digest: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> RuntimeReceipt:
    """Constructs a RuntimeReceipt and computes its SHA-256 digest."""
    receipt = RuntimeReceipt(
        receipt_id=receipt_id,
        wave_id=wave_id,
        step_name=step_name,
        status=status,
        input_digests=input_digests,
        output_digests=output_digests,
        parent_receipt_digest=parent_receipt_digest,
        timestamp=time.time(),
        metadata=metadata or {},
    )

    # Compute deterministic digest omitting the digest field itself
    receipt_dict = receipt.to_dict()
    receipt_dict["receipt_digest"] = ""
    digest_val = compute_content_digest(receipt_dict).sha256
    receipt.receipt_digest = digest_val
    return receipt


def write_immutable_receipt(
    receipt: RuntimeReceipt,
    dest_path: str | Path,
    allow_overwrite: bool = False,
) -> Path:
    """
    Writes receipt JSON to disk, verifies byte roundtrip, and sets read-only permissions (0o444).
    Enforces exclusive creation: raises FileExistsError if dest_path already exists and allow_overwrite is False.
    """
    target = Path(dest_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        if not allow_overwrite:
            raise FileExistsError(
                f"Receipt file already exists at {target}. Exclusive creation prohibits overwrites."
            )
        try:
            with open(target, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if existing.get("receipt_digest") == receipt.receipt_digest:
                return target
        except Exception:
            pass
        os.chmod(target, 0o644)

    raw_bytes = json.dumps(receipt.to_dict(), indent=2, sort_keys=True).encode("utf-8")
    with open(target, "wb") as f:
        f.write(raw_bytes)

    os.chmod(target, 0o444)
    return target


def create_sequential_lock(
    lock_path: str | Path,
    wave_id: str,
    receipt_digest: str,
    parent_lock_digest: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    allow_overwrite: bool = False,
) -> Path:
    """
    Creates an immutable sequential gate lock linking the completed wave to its receipt digest.
    Enforces exclusive creation: raises FileExistsError if lock_path already exists and allow_overwrite is False.
    """
    target = Path(lock_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        if not allow_overwrite:
            raise FileExistsError(
                f"Sequential lock already exists at {target}. Exclusive creation prohibits overwrites."
            )
        os.chmod(target, 0o644)

    lock_payload = {
        "lock_id": str(target.name),
        "wave_id": wave_id,
        "status": "LOCKED",
        "receipt_digest": receipt_digest,
        "parent_lock_digest": parent_lock_digest,
        "timestamp": time.time(),
        "metadata": metadata or {},
    }

    raw = canonical_json_bytes(lock_payload)
    lock_digest = compute_content_digest(raw).sha256
    lock_payload["lock_digest"] = lock_digest

    with open(target, "w", encoding="utf-8") as f:
        json.dump(lock_payload, f, indent=2)

    os.chmod(target, 0o444)
    return target


def _extract_lock_wave(data: Dict[str, Any]) -> Optional[int]:
    meta = data.get("metadata") or {}
    meta_wave = meta.get("wave") or meta.get("wave_number")
    if meta_wave is not None:
        try:
            return int(meta_wave)
        except ValueError:
            pass
    import re
    m = re.search(r"Wave\s*(\d+)", str(data.get("wave_id", "")), re.IGNORECASE)
    return int(m.group(1)) if m else None


def verify_predecessor_lock(
    parent_lock_path: str | Path,
    expected_parent_wave: Optional[int] = None,
    expected_receipt_digest: Optional[str] = None,
) -> tuple[bool, str, Optional[str]]:
    """
    Cryptographically verifies a predecessor wave lock.
    """
    target = Path(parent_lock_path).resolve()
    if not target.is_file():
        return False, f"Parent lock file does not exist: {target}", None

    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        return False, f"Malformed parent lock JSON: {exc}", None

    if data.get("status") != "LOCKED":
        return False, f"Parent lock status is '{data.get('status')}', expected 'LOCKED'", None

    recorded_lock_digest = data.get("lock_digest")
    copy_payload = dict(data)
    copy_payload.pop("lock_digest", None)
    recalc_digest = compute_content_digest(canonical_json_bytes(copy_payload)).sha256
    if recorded_lock_digest and recorded_lock_digest != recalc_digest:
        return False, f"Parent lock digest mismatch (recorded={recorded_lock_digest}, recalculated={recalc_digest})", None

    parent_digest = recorded_lock_digest or recalc_digest

    if expected_receipt_digest and data.get("receipt_digest") != expected_receipt_digest:
        return False, f"Parent receipt digest mismatch (expected={expected_receipt_digest}, got={data.get('receipt_digest')})", None

    if expected_parent_wave is not None:
        found_wave = _extract_lock_wave(data)
        if found_wave is not None and found_wave != expected_parent_wave:
            return False, f"Parent wave discontinuity: expected Wave {expected_parent_wave}, got Wave {found_wave}", None

    return True, f"Verified predecessor lock {target.name} [digest: {parent_digest[:16]}...]", parent_digest


def verify_sequential_lock(
    lock_path: str | Path,
    expected_receipt_digest: Optional[str] = None,
) -> bool:
    """Verifies that a sequential lock file exists, is valid, and matches receipt digest."""
    target = Path(lock_path).resolve()
    if not target.exists():
        return False

    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("status") != "LOCKED":
            return False
        if expected_receipt_digest and data.get("receipt_digest") != expected_receipt_digest:
            return False
        return True
    except Exception:
        return False


def verify_runtime_receipt(
    receipt_path: str | Path,
    expected_status: Optional[str] = "COMPLETE",
    expected_wave_id: Optional[str] = None,
) -> tuple[bool, str, Optional[str]]:
    """
    Cryptographically verifies a RuntimeReceipt file on disk.
    Checks file existence, valid JSON, status, and recalculates SHA-256 digest
    with receipt_digest set to empty string.
    """
    target = Path(receipt_path).resolve()
    if not target.exists() or not target.is_file():
        return False, f"Receipt file does not exist: {target}", None

    try:
        with open(target, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        return False, f"Malformed receipt JSON: {exc}", None

    if expected_status and data.get("status") != expected_status:
        return False, f"Receipt status is '{data.get('status')}', expected '{expected_status}'", None

    if expected_wave_id and data.get("wave_id") != expected_wave_id:
        return False, f"Receipt wave_id is '{data.get('wave_id')}', expected '{expected_wave_id}'", None

    recorded = data.get("receipt_digest")
    copy_payload = dict(data)
    copy_payload["receipt_digest"] = ""
    recalculated = compute_content_digest(canonical_json_bytes(copy_payload)).sha256

    if recorded and recorded != recalculated:
        return False, f"Receipt digest mismatch (recorded={recorded}, recalculated={recalculated})", None

    return True, f"Verified runtime receipt {target.name} [digest: {recorded[:16] if recorded else ''}...]", recorded


def sanitize_metadata_for_persistence(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitizes metadata dictionary by stripping in-memory objects, stringified representations, and bloated caches."""
    if not isinstance(metadata, dict):
        return {}
    cleaned: Dict[str, Any] = {}
    keys_to_skip = {
        "state_machine", "circuit_breaker", "structured_segments", "structured_utterances",
        "prior_forensic_transcript", "prior_graph_candidate", "prior_canonical_projection",
        "prior_recap_candidate", "prior_executive_recap", "prior_context",
        "raw_transcript", "source_text", "resolved_transcript",
        "remediation_directive_recap", "approval_receipt",
    }
    for k, v in metadata.items():
        if k in keys_to_skip:
            continue
        if isinstance(v, str) and (v.startswith("<dual_pipeline") or ("object at 0x" in v and v.startswith("<"))):
            continue
        if isinstance(v, dict):
            cleaned[k] = sanitize_metadata_for_persistence(v)
        else:
            cleaned[k] = v
    return cleaned

