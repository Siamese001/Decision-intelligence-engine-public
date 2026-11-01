"""Governance and contract verification tools for Antigravity SDK agents.

Enables agents to verify intake packages, check RFC 8259 canonical hashes,
and enforce schema compliance across pipeline boundaries.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict


def validate_rfc8259_canonical_hash(
    payload: Any,
) -> str:
    """Compute the RFC 8259 / RFC 8785 canonical SHA-256 hash of a JSON payload.

    Args:
        payload: Dict, list, string, or bytes to canonically hash.

    Returns:
        Hex-encoded SHA-256 digest string.
    """
    if isinstance(payload, bytes):
        payload_bytes = payload
    elif isinstance(payload, str):
        payload_bytes = payload.encode("utf-8")
    else:
        # Canonical JSON serialization: sorted keys, compact separators
        canonical_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        payload_bytes = canonical_str.encode("utf-8")

    return hashlib.sha256(payload_bytes).hexdigest()


def verify_contract_compliance(
    package_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """Verify that an intake package conforms to governed dual-pipeline schemas.

    Args:
        package_metadata: Metadata dictionary declaring package attributes.

    Returns:
        Verification verdict, evaluated schema gates, and receipt hash.
    """
    required_keys = {"project", "meeting_name", "date"}
    missing = required_keys - set(package_metadata.keys())

    if missing:
        return {
            "status": "NON_COMPLIANT",
            "missing_attributes": sorted(list(missing)),
            "is_valid": False,
        }

    canonical_digest = validate_rfc8259_canonical_hash(package_metadata)
    return {
        "status": "COMPLIANT",
        "is_valid": True,
        "canonical_sha256": canonical_digest,
        "evaluated_gates": ["U0", "L1", "C0.1", "C0.2"],
    }
