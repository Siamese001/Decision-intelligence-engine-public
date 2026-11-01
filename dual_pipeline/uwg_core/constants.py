"""Constants and policy configurations for Universal Write Gateway."""
from __future__ import annotations

from ..core import ROOT

PAIR_FIELDS = (
    "generation_id",
    "graph_snapshot",
    "graph_sha256",
    "sidecar_sha256",
    "embedding_backend",
    "model_id",
    "model_revision",
    "model_manifest_digest",
    "production_qualified",
)
DIRECT_PROMOTION_POSTURES = {"authoritative_internal", "external_first_party"}
UWG_PROMOTION_POLICY_PATH = ROOT / "requirements" / "uwg-promotion-policy.json"
UWG_POLICY_THRESHOLD_FIELDS = (
    "top_k",
    "min_recall_at_k",
    "min_mrr",
    "max_latency_ms",
    "max_negative_leakage_rate",
    "max_lane_leakage_rate",
    "max_acl_leakage_rate",
)
UWG_GOVERNANCE_APPROVAL_CONTRACT = (
    "urn:nee:dual-pipeline:uwg-governance-approval:1.0.0"
)
UWG_RELEASE_KINDS = frozenset(
    {"uwg_promotion", "uwg_governance_approval_registration"}
)
UWG_FACT_PROMOTION_GOVERNANCE = {
    "retrieval_kind": "assertion",
    "answer_use": "candidate_hypothesis",
    "fact_promotion_eligible": True,
}
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
