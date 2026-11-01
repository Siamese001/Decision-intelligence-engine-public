"""Constants and policy definitions for release management."""
from __future__ import annotations

import re
from typing import Any

RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
_QUALIFICATION_TRANSITION_MARKER = object()
INTAKE_RELEASE_STAGES = [
    "U0",
    "L1",
    "L0",
    "C0.1",
    "C0.2",
    "C0.3",
    "X1",
    "X2",
    "X3",
    "L4",
]
FACT_PROMOTION_READY_STATUSES = frozenset(
    {
        "candidate_evidence_bound_hitl_required",
        "candidate_evidence_selected_span_hitl_required",
    }
)
_A2A_FINALIZATION_BINDING_FIELDS = frozenset(
    {
        "receipt_id",
        "receipt_canonical_digest",
        "receipt_file_sha256",
        "package_file_sha256",
        "package_canonical_digest",
        "archive_root_id",
        "archive_binding_digest",
        "verification_state",
    }
)
_A2A_SOURCE_ATTEST_CAPABILITY = "knowledge:source-attest"
_A2A_SOURCE_ATTEST_RESOURCES = frozenset(
    {"a2a_finalization_receipt", "a2a_finalization_archive"}
)

APPROVED_BGE_M3_BACKENDS = frozenset(
    {
        "flagembedding-bge-m3",
        "flagembedding-bge-m3-dev",
        "flagembedding-bge-m3-internal",
    }
)


def require_approved_bge_m3_pair(pair: dict[str, Any]) -> None:
    """Reject any active graph/index pair not bound to a verified BGE-M3 model."""
    if pair.get("embedding_backend") not in APPROVED_BGE_M3_BACKENDS:
        raise RuntimeError("active pair requires an approved BGE-M3 embedding backend")
    if pair.get("model_id") != "BAAI/bge-m3":  # noqa: SSOT_EXEMPT
        raise RuntimeError("active pair requires model_id BAAI/bge-m3")
    if not isinstance(pair.get("model_revision"), str) or not re.fullmatch(
        r"[0-9a-f]{40}", str(pair.get("model_revision"))
    ):
        raise RuntimeError("active pair requires an immutable BGE-M3 model revision")
    if not isinstance(pair.get("model_manifest_digest"), str) or not re.fullmatch(
        r"[0-9a-f]{64}", str(pair.get("model_manifest_digest"))
    ):
        raise RuntimeError("active pair requires a BGE-M3 model manifest digest")
