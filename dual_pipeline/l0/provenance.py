"""Dual-Pipeline Layer 0: Cryptographic Provenance, Content Digests & Claim-Checks.

Implements immutable provenance tracing (Law 4) across all pipelines.
RFC-8259 canonical JSON serialization guarantees deterministic SHA-256 hashing.
Strictly Zero-Dependency (standard library only). Layer L0 must NEVER import L1-L6.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Optional

from dual_pipeline.l0.contracts import ClaimCheck, ContentDigest


def canonical_json_bytes(data: Any) -> bytes:
    """Serializes arbitrary Python dicts/lists into RFC-8259 canonical JSON bytes."""
    return json.dumps(
        data,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def compute_content_digest(payload: Any) -> ContentDigest:
    """Computes a deterministic SHA-256 digest from canonical JSON bytes or str/bytes."""
    if isinstance(payload, bytes):
        raw_bytes = payload
    elif isinstance(payload, str):
        raw_bytes = payload.encode("utf-8")
    elif hasattr(payload, "to_dict"):
        raw_bytes = canonical_json_bytes(payload.to_dict())
    elif isinstance(payload, (dict, list)):
        raw_bytes = canonical_json_bytes(payload)
    else:
        raw_bytes = str(payload).encode("utf-8")

    sha256 = hashlib.sha256(raw_bytes).hexdigest()
    return ContentDigest(sha256=sha256, byte_count=len(raw_bytes))


def mint_claim_check(
    payload: Any,
    storage_uri: str,
    namespace: str = "CLIENT_SAFE",
    tenant_id: str = "default",
    claim_id: Optional[str] = None,
) -> ClaimCheck:
    """Mints an immutable Claim-Check pointer binding payload to digest and URI (Law 4)."""
    digest_obj = compute_content_digest(payload)
    if not claim_id:
        claim_id = f"claim:{namespace.lower()}:{digest_obj.sha256[:16]}"
    return ClaimCheck(
        claim_id=claim_id,
        digest=digest_obj.sha256,
        storage_uri=storage_uri,
        namespace=namespace,
        tenant_id=tenant_id,
        created_at=time.time(),
    )


def verify_digest_integrity(payload: Any, expected_digest: str) -> bool:
    """Verifies that payload canonical bytes match expected SHA-256 digest."""
    computed = compute_content_digest(payload)
    return computed.sha256 == expected_digest.lower()


def chain_parent_digests(parent_digests: list[str]) -> str:
    """Computes a deterministic composite hash chaining one or more parent digests."""
    sorted_digests = sorted(d.strip().lower() for d in parent_digests if d.strip())
    concat = ":".join(sorted_digests).encode("utf-8")
    return hashlib.sha256(concat).hexdigest()
