"""Constants for combined Sherpa/status remediation."""
from __future__ import annotations

COMBINED_REMEDIATION_CONTROL_KEY = "com.partner_advisory.nee.combined-remediation-control"
COMBINED_REMEDIATION_POLICY_VERSION = "2026-08-04.1"

_REQUIRED_CONTROL_KEYS = {
    "schema_version",
    "control_type",
    "base_active_pair",
    "source_retirement_execution_order",
    "source_retirement_controls",
    "project_status_reclassification_control",
    "reason",
    "canonical_digest",
}

_PAIR_FIELDS = (
    "runtime_root",
    "generation_id",
    "graph_sha256",
    "sidecar_sha256",
    "embedding_backend",
    "model_id",
    "model_revision",
    "model_manifest_digest",
)
