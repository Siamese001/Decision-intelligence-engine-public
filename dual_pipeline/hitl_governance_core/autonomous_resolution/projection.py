"""
dual_pipeline/hitl_governance_core/autonomous_resolution/projection.py

Recoverable JSON ledger projection for authenticated autonomous resolutions.
Guarantees monotonic sequence progression, head protection against out-of-order regression,
and cryptographic readback integrity.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .models import (
    AutonomousResolutionRecord,
)
from .signing import (
    compute_canonical_digest,
    verify_autonomous_signature,
)
from .storage import AutonomousResolutionStore


def export_projection(
    store: AutonomousResolutionStore,
    json_path: Path | str,
) -> Dict[str, Any]:
    """
    Exports SQLite autonomous resolutions to a recoverable JSON projection.
    Enforces monotonic head protection against out-of-order regression.
    """
    target = Path(json_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    records = store.list_all()
    latest_rec = records[-1] if records else None
    latest_seq = latest_rec.sequence_number if latest_rec else 0
    latest_digest = latest_rec.resolution_digest if latest_rec else "0" * 64

    # Check for regression against existing projection
    if target.is_file():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
            existing_seq = int(existing.get("head_sequence_number", 0))
            if latest_seq < existing_seq:
                raise ValueError(
                    f"Out-of-order export rejected: store head sequence ({latest_seq}) "
                    f"is behind existing projection head ({existing_seq})."
                )
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            if "Out-of-order export rejected" in str(e):
                raise

    projection_data: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "record_count": len(records),
        "head_sequence_number": latest_seq,
        "head_resolution_digest": latest_digest,
        "records": [r.to_dict() for r in records],
    }

    # Write atomically
    temp_dir = target.parent
    with tempfile.NamedTemporaryFile("w", dir=temp_dir, delete=False, encoding="utf-8") as tf:
        json.dump(projection_data, tf, indent=2, sort_keys=True)
        temp_name = tf.name

    os.replace(temp_name, target)
    return projection_data


def verify_projection_integrity(
    json_path: Path | str,
    public_key: Ed25519PublicKey,
) -> Tuple[bool, List[str]]:
    """
    Verifies cryptographic signatures, Merkle hashes, and sequence progression of a JSON projection.
    """
    path = Path(json_path).resolve()
    if not path.is_file():
        return False, [f"Projection file not found: {path}"]

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return False, [f"Failed to parse projection JSON: {e}"]

    records_raw = data.get("records", [])
    defects: List[str] = []
    expected_prev_hash = "0" * 64

    for idx, raw in enumerate(records_raw):
        expected_seq = idx + 1
        rec = AutonomousResolutionRecord(
            resolution_id=raw["resolution_id"],
            sequence_number=raw["sequence_number"],
            decision_id=raw["decision_id"],
            revision=raw["revision"],
            scope=None,  # type: ignore
            authority_type=None,  # type: ignore
            category=raw["category"],
            idempotency_key=raw["idempotency_key"],
            selected_option_id=raw["selected_option_id"],
            selected_option=None,  # type: ignore
            options=[],
            margin=raw["margin"],
            entropy=raw["entropy"],
            top_confidence=raw["top_confidence"],
            second_confidence=raw["second_confidence"],
            policy_snapshot=None,  # type: ignore
            decision_content_digest=raw["decision_content_digest"],
            previous_resolution_hash=raw["previous_resolution_hash"],
            recorded_at=raw["recorded_at"],
            signer_id=raw["signer_id"],
            signature_b64=raw["signature_b64"],
            resolution_digest=raw["resolution_digest"],
        )

        if rec.sequence_number != expected_seq:
            defects.append(
                f"Sequence anomaly at index {idx}: got {rec.sequence_number}, expected {expected_seq}."
            )
        if rec.previous_resolution_hash != expected_prev_hash:
            defects.append(
                f"Merkle chain broken at index {idx}: previous_hash '{rec.previous_resolution_hash}' "
                f"does not match expected '{expected_prev_hash}'."
            )

        canonical_payload = {
            "decision_content_digest": raw["decision_content_digest"],
            "decision_id": raw["decision_id"],
            "entropy": round(float(raw["entropy"]), 4),
            "margin": round(float(raw["margin"]), 4),
            "policy_digest": raw["policy_snapshot"]["policy_digest"],
            "previous_resolution_hash": raw["previous_resolution_hash"],
            "recorded_at": raw["recorded_at"],
            "revision": raw["revision"],
            "scope": raw["scope"],
            "second_confidence": round(float(raw["second_confidence"]), 4),
            "selected_option_id": raw["selected_option_id"],
            "sequence_number": raw["sequence_number"],
            "signer_id": raw["signer_id"],
            "top_confidence": round(float(raw["top_confidence"]), 4),
        }

        recomputed_digest = compute_canonical_digest(canonical_payload)
        if rec.resolution_digest != recomputed_digest:
            defects.append(
                f"Digest mismatch at index {idx}: got {rec.resolution_digest}, expected {recomputed_digest}."
            )

        is_valid_sig = verify_autonomous_signature(
            public_key=public_key,
            payload=canonical_payload,
            signature_b64=rec.signature_b64,
        )
        if not is_valid_sig:
            defects.append(f"Invalid digital signature at index {idx} ({rec.resolution_id}).")

        expected_prev_hash = rec.resolution_digest

    return len(defects) == 0, defects
